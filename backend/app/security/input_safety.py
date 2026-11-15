"""
OmniTrack AI — input safety for user-supplied camera sources and model names.

Camera sources (``validate_camera_source``):
  * ``rtsp://`` / ``rtsps://``           - allowed (LAN cameras); credentials in the
    URL are allowed but never echoed back by the API.
  * ``http://`` / ``https://``           - only when the host is on
    CAMERA_SOURCE_ALLOWLIST (host names or IP/CIDR). Prevents SSRF against
    cloud metadata, localhost admin ports, etc. DNS is resolved and every
    resolved address must be allowed, so a hostname cannot point at a blocked IP.
  * local file                            - must resolve (realpath, symlinks
    followed) inside one of VIDEO_SOURCE_DIRS. ``../`` and symlink escapes fail.
  * webcam index ``"0"``..``"9"``          - when ALLOW_WEBCAM_SOURCES.
  Every other scheme (file://, ftp://, gopher://, data:, pipes ...) is refused.

Model names (``validate_model_name``): a bare file name that exists inside
MODEL_WEIGHTS_DIR (realpath-checked).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union
from urllib.parse import urlsplit

from app.config import settings

ALLOWED_MODEL_SUFFIXES = {".pt", ".onnx", ".engine", ".pth", ".torchscript"}
_WEBCAM_RE = re.compile(r"^\d{1,2}$")
_ALWAYS_BLOCKED_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),     # link-local incl. cloud metadata 169.254.169.254
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba metadata
    ipaddress.ip_network("fd00:ec2::254/128"),   # AWS IPv6 metadata
]


class UnsafeInputError(ValueError):
    """User input rejected by a safety rule. Message is safe to show to the client."""


IPNet = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def _parse_allowlist(entries: Iterable[str]):
    hosts, nets = set(), []
    for raw in entries or []:
        item = str(raw).strip().lower()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            hosts.add(item.rstrip("."))
    return hosts, nets


def _resolve(host: str) -> List[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeInputError(f"Camera host '{host}' does not resolve") from e
    return sorted({ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}, key=str)


def _is_always_blocked(addr) -> bool:
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr in net for net in _ALWAYS_BLOCKED_NETS if addr.version == net.version)


def _check_http_host(host: str, allowlist: Sequence[str]) -> None:
    hosts, nets = _parse_allowlist(allowlist)
    host_l = host.lower().rstrip(".")
    if not hosts and not nets:
        raise UnsafeInputError(
            "http(s) camera sources are disabled: add the camera host to CAMERA_SOURCE_ALLOWLIST"
        )
    addrs = _resolve(host_l)
    for addr in addrs:
        if _is_always_blocked(addr):
            raise UnsafeInputError("Camera host resolves to a blocked address (link-local/metadata)")
    if host_l in hosts:
        return
    if addrs and all(any(a in n for n in nets if a.version == n.version) for a in addrs):
        return
    raise UnsafeInputError(f"Camera host '{host}' is not on CAMERA_SOURCE_ALLOWLIST")


def _video_roots(roots: Optional[Sequence[Union[str, Path]]] = None) -> List[Path]:
    out = []
    for r in roots if roots is not None else settings.VIDEO_SOURCE_DIRS:
        try:
            out.append(Path(os.path.realpath(r)))
        except OSError:
            continue
    return out


def resolve_video_file(path_str: str, roots: Optional[Sequence[Union[str, Path]]] = None) -> Path:
    """Return the real path of a video file that lives inside an allowed directory.

    Relative paths are tried against each root (``caviar/x.mpg`` works); absolute
    paths must already be inside a root. Symlinks are resolved before the check.
    """
    if "\x00" in path_str:
        raise UnsafeInputError("Invalid path")
    allowed = _video_roots(roots)
    raw = Path(path_str).expanduser()
    candidates = [raw] if raw.is_absolute() else [root / raw for root in allowed]
    for cand in candidates:
        real = Path(os.path.realpath(cand))
        if not any(real == root or root in real.parents for root in allowed):
            continue
        if real.is_file():
            return real
    raise UnsafeInputError("Video file not found inside the allowed video directories")


def validate_camera_source(
    source: str,
    *,
    allowlist: Optional[Sequence[str]] = None,
    video_roots: Optional[Sequence[Union[str, Path]]] = None,
    allow_webcam: Optional[bool] = None,
) -> str:
    """Return a normalised, safe source string or raise UnsafeInputError."""
    if not isinstance(source, str):
        raise UnsafeInputError("Camera source must be a string")
    src = source.strip()
    if not src or len(src) > 500 or any(ord(c) < 32 for c in src):
        raise UnsafeInputError("Camera source is empty, too long or contains control characters")

    if _WEBCAM_RE.match(src):
        if allow_webcam if allow_webcam is not None else settings.ALLOW_WEBCAM_SOURCES:
            return src
        raise UnsafeInputError("Local webcam sources are disabled")

    parts = urlsplit(src)
    scheme = parts.scheme.lower()
    if scheme in ("rtsp", "rtsps"):
        if not parts.hostname:
            raise UnsafeInputError("RTSP URL has no host")
        for addr in _resolve_lenient(parts.hostname):
            if _is_always_blocked(addr):
                raise UnsafeInputError("Camera host resolves to a blocked address (link-local/metadata)")
        return src
    if scheme in ("http", "https"):
        if not parts.hostname:
            raise UnsafeInputError("URL has no host")
        _check_http_host(parts.hostname, settings.CAMERA_SOURCE_ALLOWLIST if allowlist is None else allowlist)
        return src
    if scheme and len(scheme) > 1:  # "c:" style drive letters are not schemes
        raise UnsafeInputError(f"Camera source scheme '{scheme}' is not allowed (use rtsp://, http(s):// or a video file)")
    return str(resolve_video_file(src, video_roots))


def _resolve_lenient(host: str):
    """RTSP cameras may be on names only resolvable later; skip unresolvable ones."""
    try:
        return _resolve(host)
    except UnsafeInputError:
        return []


def redact_source(source: Optional[str]) -> Optional[str]:
    """Hide credentials in rtsp/http URLs for API responses and logs."""
    if not source:
        return source
    parts = urlsplit(source)
    if parts.password is None and parts.username is None:
        return source
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return parts._replace(netloc=f"***@{netloc}").geturl()


def validate_model_name(name: str, weights_dir: Optional[Union[str, Path]] = None) -> Path:
    """Return the real path of a weight file inside MODEL_WEIGHTS_DIR."""
    if not isinstance(name, str) or not name.strip():
        raise UnsafeInputError("Model name is empty")
    name = name.strip()
    root = Path(os.path.realpath(weights_dir or settings.MODEL_WEIGHTS_DIR))
    if "\x00" in name or "/" in name or "\\" in name or name.startswith("."):
        raise UnsafeInputError("Model must be a bare file name inside model_weight/")
    real = Path(os.path.realpath(root / name))
    if real.parent != root and root not in real.parents:
        raise UnsafeInputError("Model path escapes model_weight/")
    if real.suffix.lower() not in ALLOWED_MODEL_SUFFIXES:
        raise UnsafeInputError(f"Unsupported model file type '{real.suffix}'")
    if not real.is_file():
        raise UnsafeInputError(f"Model '{name}' not found in model_weight/")
    return real
