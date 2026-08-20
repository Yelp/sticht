from typing import Any
from typing import Dict
from typing import Optional

try:
    import yelp_meteorite as _meteorite
except ImportError:
    _meteorite = None  # type: ignore[assignment]


class _NoopTimer:

    def __enter__(self) -> '_NoopTimer':
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _NoopCounter:

    def count(self, value: int = 1) -> None:
        pass


def create_timer(
    name: str,
    default_dimensions: Optional[Dict[str, str]] = None,
) -> Any:
    """Create a timer context manager. Noops if yelp_meteorite is unavailable."""
    if _meteorite is not None:
        return _meteorite.create_timer(name, default_dimensions=default_dimensions or {})
    return _NoopTimer()


def create_counter(
    name: str,
    default_dimensions: Optional[Dict[str, str]] = None,
) -> Any:
    """Create a counter. Noops if yelp_meteorite is unavailable."""
    if _meteorite is not None:
        return _meteorite.create_counter(name, default_dimensions=default_dimensions or {})
    return _NoopCounter()
