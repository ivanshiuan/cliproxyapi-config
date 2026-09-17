"""Domain exceptions raised by the ingestion pipeline.

These deliberately inherit from :class:`restaurant_api.api.errors.DomainError`
so that if the router-layer spec (a later slice) exposes ``POST
/knowledge/ingest``, the same exceptions render as the project-standard
JSON envelope without any extra plumbing.
"""

from __future__ import annotations

from fastapi import status

from ...api.errors import DomainError


class IngestionError(DomainError):
    """Base for any ingestion-time failure. Subclasses set the status code."""

    code = "INGESTION_FAILED"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class IngestionDeniedError(IngestionError):
    """The source was rejected before any work started.

    Common triggers: byte cap (> 200 MB), unsupported mime type, adapter
    not implemented (e.g. drive_file_id when Drive OAuth isn't wired).
    """

    code = "INGESTION_DENIED"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class IngestionExtractError(IngestionError):
    """Markitdown subprocess failed, timed out, or produced empty output."""

    code = "INGESTION_EXTRACT_FAILED"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class IngestionEmbedError(IngestionError):
    """Embedding provider failed after retries, or returned malformed output."""

    code = "INGESTION_EMBED_FAILED"
    status_code = status.HTTP_502_BAD_GATEWAY


__all__ = [
    "IngestionDeniedError",
    "IngestionEmbedError",
    "IngestionError",
    "IngestionExtractError",
]
