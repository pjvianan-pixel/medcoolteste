"""File storage utilities for medical document files."""

import os
import uuid
from datetime import datetime, timezone

from app.core.config import settings


def _ensure_storage_dir() -> str:
    """Return the absolute path of the storage directory, creating it if needed."""
    path = os.path.abspath(settings.DOCUMENTS_STORAGE_PATH)
    os.makedirs(path, exist_ok=True)
    return path


def save_document_file(document_id: uuid.UUID, content: bytes, extension: str = "pdf") -> str:
    """Save *content* to the documents storage directory.

    The file is named ``doc_{document_id}_{timestamp}.{extension}`` to ensure
    uniqueness even if the same document is regenerated.

    Returns the URL path (relative to the app root) that should be stored in
    ``MedicalDocument.file_url``.
    """
    storage_dir = _ensure_storage_dir()
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    filename = f"doc_{document_id}_{timestamp}.{extension}"
    file_path = os.path.join(storage_dir, filename)
    with open(file_path, "wb") as fh:
        fh.write(content)
    # Return the URL path that will be served via the static-files mount.
    return f"{settings.DOCUMENTS_BASE_URL}/{filename}"
