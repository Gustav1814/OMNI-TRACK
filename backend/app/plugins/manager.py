"""
Lightweight plugin manager for optional OmniTrack integrations.

Plugins are trusted Python modules enabled explicitly through configuration.
A plugin module may expose:
  - router: FastAPI APIRouter
  - setup(context): returns an object with optional hooks
  - on_startup(context), on_shutdown(context)
  - on_pipeline_results(results, global_state)
  - on_lifecycle(event, payload)

This keeps the core API small while leaving stable hooks for future modules.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional

from fastapi import FastAPI
from loguru import logger


@dataclass
class PluginContext:
    """Shared runtime objects passed to trusted plugins."""

    app: FastAPI
    settings: Any
    cache: Any = None
    broadcast: Any = None
    pipeline: Any = None
    persistence: Any = None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class PluginManager:
    """Loads configured plugin modules and wires their optional hooks."""

    def __init__(self, module_names: Iterable[str]):
        self.module_names = [name.strip() for name in module_names if name and name.strip()]
        self._modules: List[Any] = []
        self._instances: List[Any] = []

    @property
    def enabled(self) -> bool:
        return bool(self.module_names)

    @property
    def loaded_names(self) -> List[str]:
        return [getattr(module, "__name__", "unknown") for module in self._modules]

    def load(self) -> None:
        """Import configured plugin modules once."""
        if self._modules:
            return
        for module_name in self.module_names:
            try:
                module = importlib.import_module(module_name)
                self._modules.append(module)
                logger.info(f"Plugin loaded: {module_name}")
            except Exception as exc:
                logger.error(f"Plugin load failed for {module_name}: {exc}")
                raise

    def register_routes(self, app: FastAPI) -> None:
        """Attach plugin routers before the app starts serving requests."""
        for module in self._modules:
            router = getattr(module, "router", None)
            if router is not None:
                app.include_router(router)
                logger.info(f"Plugin router registered: {module.__name__}")

    async def startup(self, context: PluginContext) -> None:
        """Run setup/startup hooks and keep returned plugin instances."""
        for module in self._modules:
            setup = getattr(module, "setup", None)
            if callable(setup):
                instance = await _maybe_await(setup(context))
                if instance is not None:
                    self._instances.append(instance)

            hook = getattr(module, "on_startup", None)
            if callable(hook):
                await _maybe_await(hook(context))

        for instance in self._instances:
            hook = getattr(instance, "on_startup", None)
            if callable(hook):
                await _maybe_await(hook(context))

    async def shutdown(self, context: PluginContext) -> None:
        """Run shutdown hooks in reverse order."""
        for instance in reversed(self._instances):
            hook = getattr(instance, "on_shutdown", None)
            if callable(hook):
                await _maybe_await(hook(context))

        for module in reversed(self._modules):
            hook = getattr(module, "on_shutdown", None)
            if callable(hook):
                await _maybe_await(hook(context))

    def register_pipeline_hooks(self, pipeline: Any) -> None:
        """Wire plugin callbacks into the existing pipeline hook system."""
        for target in [*self._modules, *self._instances]:
            results_hook = getattr(target, "on_pipeline_results", None)
            if callable(results_hook):
                pipeline.on_results(self._guarded_results_hook(target, results_hook))
                logger.info(f"Plugin pipeline results hook registered: {self._target_name(target)}")

            lifecycle_hook = getattr(target, "on_lifecycle", None)
            if callable(lifecycle_hook):
                pipeline.on_lifecycle(self._guarded_lifecycle_hook(target, lifecycle_hook))
                logger.info(f"Plugin lifecycle hook registered: {self._target_name(target)}")

    def _guarded_results_hook(self, target: Any, hook: Callable) -> Callable:
        async def wrapped(results: Any, global_state: Any) -> None:
            try:
                await _maybe_await(hook(results, global_state))
            except Exception as exc:
                logger.warning(f"Plugin results hook failed ({self._target_name(target)}): {exc}")

        return wrapped

    def _guarded_lifecycle_hook(self, target: Any, hook: Callable) -> Callable:
        async def wrapped(event: str, payload: dict) -> None:
            try:
                await _maybe_await(hook(event, payload))
            except Exception as exc:
                logger.warning(f"Plugin lifecycle hook failed ({self._target_name(target)}): {exc}")

        return wrapped

    @staticmethod
    def _target_name(target: Any) -> str:
        return getattr(target, "__name__", target.__class__.__name__)
