try:
    from _version import __version__
except Exception:  # pragma: no cover - fallback for unusual import paths
    __version__ = "0.0.0"

__all__ = ["__version__"]
