from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, cast

import numpy as np
import torch
import torch.nn as nn


def conv_bn(
    in_channels: int,
    out_channels: int,
    kernel_size: int,
    stride: int,
    padding: int,
    groups: int = 1,
) -> nn.Sequential:
    block = nn.Sequential()
    block.add_module(
        "conv",
        nn.Conv2d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            groups=groups,
            bias=False,
        ),
    )
    block.add_module("bn", nn.BatchNorm2d(num_features=out_channels))
    return block


class RepVGGBlock(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: int,
        stride: int = 1,
        padding: int = 0,
        dilation: int = 1,
        groups: int = 1,
        padding_mode: Literal["zeros", "reflect", "replicate", "circular"] = "zeros",
        deploy: bool = False,
    ):
        super().__init__()

        self.deploy = deploy
        self.groups = groups
        self.in_channels = in_channels

        assert kernel_size == 3, "RepVGG blocks currently assume 3x3 kernels."
        assert padding == 1, "RepVGG blocks currently assume padding of 1."

        padding_11 = padding - kernel_size // 2
        self.nonlinearity = nn.ReLU()

        if deploy:
            self.rbr_reparam = nn.Conv2d(
                in_channels=in_channels,
                out_channels=out_channels,
                kernel_size=kernel_size,
                stride=stride,
                padding=padding,
                dilation=dilation,
                groups=groups,
                bias=True,
                padding_mode=padding_mode,
            )
        else:
            self.rbr_identity = (
                nn.BatchNorm2d(num_features=in_channels)
                if out_channels == in_channels and stride == 1
                else None
            )
            self.rbr_dense = conv_bn(
                in_channels=in_channels,
                out_channels=out_channels,
                kernel_size=kernel_size,
                stride=stride,
                padding=padding,
                groups=groups,
            )
            self.rbr_1x1 = conv_bn(
                in_channels=in_channels,
                out_channels=out_channels,
                kernel_size=1,
                stride=stride,
                padding=padding_11,
                groups=groups,
            )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        if hasattr(self, "rbr_reparam"):
            return cast(torch.Tensor, self.nonlinearity(self.rbr_reparam(inputs)))

        id_out = 0 if self.rbr_identity is None else self.rbr_identity(inputs)
        return cast(
            torch.Tensor,
            self.nonlinearity(self.rbr_dense(inputs) + self.rbr_1x1(inputs) + id_out),
        )

    def get_equivalent_kernel_bias(self) -> tuple[torch.Tensor, torch.Tensor]:
        kernel3x3, bias3x3 = self._fuse_bn_tensor(self.rbr_dense)
        kernel1x1, bias1x1 = self._fuse_bn_tensor(self.rbr_1x1)
        kernelid, biasid = self._fuse_bn_tensor(self.rbr_identity)
        assert isinstance(kernel3x3, torch.Tensor)
        assert isinstance(bias3x3, torch.Tensor)
        assert isinstance(kernel1x1, torch.Tensor)
        assert isinstance(bias1x1, torch.Tensor)
        k_pad = self._pad_1x1_to_3x3_tensor(kernel1x1)
        z = torch.zeros((), device=kernel3x3.device, dtype=kernel3x3.dtype)
        kernel = kernel3x3 + k_pad + (kernelid if isinstance(kernelid, torch.Tensor) else z)
        bias = bias3x3 + bias1x1 + (biasid if isinstance(biasid, torch.Tensor) else z)
        return kernel, bias

    def _pad_1x1_to_3x3_tensor(self, kernel1x1: torch.Tensor) -> torch.Tensor:
        return torch.nn.functional.pad(kernel1x1, [1, 1, 1, 1])

    def _fuse_bn_tensor(
        self, branch: nn.Module | None
    ) -> tuple[torch.Tensor | int, torch.Tensor | int]:
        if branch is None:
            return 0, 0

        if isinstance(branch, nn.Sequential):
            conv = cast(nn.Conv2d, branch.conv)
            bn = cast(nn.BatchNorm2d, branch.bn)
            kernel = conv.weight
            running_mean = bn.running_mean
            running_var = bn.running_var
            gamma = bn.weight
            beta = bn.bias
            eps = bn.eps
        else:
            assert isinstance(
                branch, nn.BatchNorm2d
            ), "Branch must be BatchNorm2d when not Sequential."
            if not hasattr(self, "id_tensor"):
                input_dim = self.in_channels // self.groups
                kernel_value = np.zeros((self.in_channels, input_dim, 3, 3), dtype=np.float32)
                for i in range(self.in_channels):
                    kernel_value[i, i % input_dim, 1, 1] = 1
                self.id_tensor = torch.from_numpy(kernel_value).to(branch.weight.device)

            kernel = self.id_tensor
            running_mean = branch.running_mean
            running_var = branch.running_var
            gamma = branch.weight
            beta = branch.bias
            eps = branch.eps

        assert running_var is not None
        assert running_mean is not None
        assert gamma is not None and beta is not None
        std = torch.sqrt(running_var + eps)
        t = (gamma / std).reshape(-1, 1, 1, 1)
        fused_kernel = kernel * t
        fused_bias = beta - running_mean * gamma / std
        return fused_kernel, fused_bias

    def switch_to_deploy(self):
        if hasattr(self, "rbr_reparam"):
            return

        kernel, bias = self.get_equivalent_kernel_bias()
        dense_conv = cast(nn.Conv2d, self.rbr_dense.conv)
        self.rbr_reparam = nn.Conv2d(
            in_channels=dense_conv.in_channels,
            out_channels=dense_conv.out_channels,
            kernel_size=cast(int | tuple[int, int], dense_conv.kernel_size),
            stride=cast(int | tuple[int, int], dense_conv.stride),
            padding=cast(int | tuple[int, int] | str, dense_conv.padding),
            dilation=cast(int | tuple[int, int], dense_conv.dilation),
            groups=dense_conv.groups,
            bias=True,
        )
        self.rbr_reparam.weight.data = kernel
        if self.rbr_reparam.bias is not None:
            self.rbr_reparam.bias.data = bias

        for param in self.parameters():
            param.detach_()

        for attr in ("rbr_dense", "rbr_1x1", "rbr_identity"):
            if hasattr(self, attr):
                delattr(self, attr)


optional_groupwise_layers = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26]
g2_map = dict.fromkeys(optional_groupwise_layers, 2)
g4_map = dict.fromkeys(optional_groupwise_layers, 4)


class RepVGG(nn.Module):
    def __init__(
        self,
        num_blocks: Sequence[int],
        num_classes: int = 1000,
        width_multiplier: Sequence[float] | None = None,
        override_groups_map: dict[int, int] | None = None,
        deploy: bool = False,
    ):
        super().__init__()
        assert width_multiplier is not None and len(width_multiplier) == 4
        self.deploy = deploy
        self.override_groups_map = override_groups_map or {}
        assert 0 not in self.override_groups_map

        self.in_planes = min(64, int(64 * width_multiplier[0]))
        self.stage0 = RepVGGBlock(
            in_channels=3,
            out_channels=self.in_planes,
            kernel_size=3,
            stride=2,
            padding=1,
            deploy=self.deploy,
        )
        self.cur_layer_idx = 1
        self.stage1 = self._make_stage(int(64 * width_multiplier[0]), num_blocks[0], stride=2)
        self.stage2 = self._make_stage(int(128 * width_multiplier[1]), num_blocks[1], stride=2)
        self.stage3 = self._make_stage(int(256 * width_multiplier[2]), num_blocks[2], stride=2)
        self.stage4 = self._make_stage(int(512 * width_multiplier[3]), num_blocks[3], stride=2)
        self.gap = nn.AdaptiveAvgPool2d(output_size=1)
        self.linear = nn.Linear(int(512 * width_multiplier[3]), num_classes)

    def _make_stage(self, planes: int, num_blocks: int, stride: int) -> nn.Sequential:
        strides = [stride] + [1] * (num_blocks - 1)
        blocks = []
        for stride_val in strides:
            cur_groups = self.override_groups_map.get(self.cur_layer_idx, 1)
            blocks.append(
                RepVGGBlock(
                    in_channels=self.in_planes,
                    out_channels=planes,
                    kernel_size=3,
                    stride=stride_val,
                    padding=1,
                    groups=cur_groups,
                    deploy=self.deploy,
                )
            )
            self.in_planes = planes
            self.cur_layer_idx += 1
        return nn.Sequential(*blocks)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.stage0(x)
        out = self.stage1(out)
        out = self.stage2(out)
        out = self.stage3(out)
        out = self.stage4(out)
        out = self.gap(out)
        out = out.view(out.size(0), -1)
        out = self.linear(out)
        return cast(torch.Tensor, out)

    def switch_to_deploy(self):
        if self.deploy:
            return

        for module in self.modules():
            if isinstance(module, RepVGGBlock):
                module.switch_to_deploy()

        self.deploy = True


def create_RepVGG_A0(deploy: bool = False) -> RepVGG:
    return RepVGG(
        num_blocks=[2, 4, 14, 1],
        num_classes=8,
        width_multiplier=[0.75, 0.75, 0.75, 2.5],
        override_groups_map=None,
        deploy=deploy,
    )
