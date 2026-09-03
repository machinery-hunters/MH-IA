"""
loaders

Convierte los bytes descargados de Drive (o exportados desde formatos
nativos de Google) a texto plano, según el mimeType efectivo.
"""

from __future__ import annotations

import csv
import io

import docx
from pypdf import PdfReader

from app.ingestion.drive_client import GOOGLE_EXPORT_MIME, DriveFile


def extract_text(file: DriveFile, raw: bytes) -> str:
    effective_mime = GOOGLE_EXPORT_MIME.get(file.mime_type, file.mime_type)

    if effective_mime == "text/plain":
        return raw.decode("utf-8", errors="ignore")

    if effective_mime == "text/csv":
        text = raw.decode("utf-8", errors="ignore")
        reader = csv.reader(io.StringIO(text))
        return "\n".join(", ".join(row) for row in reader)

    if effective_mime == "application/pdf":
        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if effective_mime == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        document = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in document.paragraphs)

    raise ValueError(f"Tipo de archivo no soportado: {file.mime_type} ({file.name})")
