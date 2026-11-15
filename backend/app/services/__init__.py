"""OmniTrack AI — Services Package.

Names are resolved lazily (PEP 562) so importing a light submodule such as
``app.services.evidence`` does not pull in torch / ultralytics via the pipeline.
"""
from importlib import import_module

_EXPORTS = {
    "RedisCache": "app.services.cache",
    "BroadcastService": "app.services.broadcast",
    "ProcessingPipeline": "app.services.pipeline",
    "StreamManager": "app.services.stream_manager",
    "ExportService": "app.services.export",
    "UserService": "app.services.crud",
    "CameraService": "app.services.crud",
    "DetectionService": "app.services.crud",
    "EmbeddingService": "app.services.crud",
    "AuditService": "app.services.crud",
    "AnalyticsService": "app.services.crud",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
