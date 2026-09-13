"""Database storage is now instance-scoped; use TestEngine.store or Store."""

from test_engine.database import Store

__all__ = ["Store"]
