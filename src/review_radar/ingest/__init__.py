"""Review ingestion: one `ReviewSource` protocol, three sources (App Store feed, CSV, Play stub)."""

from .base import IngestError, ReviewIn, ReviewSource, source_for

__all__ = ["IngestError", "ReviewIn", "ReviewSource", "source_for"]
