"""
chunker

Produce Document(page_content, metadata) donde metadata debe casar EXACTO
con lo que app/nodes/retriever_node.py espera al construir `citations`:
  - metadata["source"]  -> lo que se muestra al usuario/Claude como cita
  - metadata["chunk_id"] -> identificador único y estable del chunk
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings
from app.ingestion.drive_client import DriveFile

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=settings.chunk_size,
    chunk_overlap=settings.chunk_overlap,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_file(file: DriveFile, text: str) -> list[Document]:
    if not text.strip():
        return []

    raw_chunks = _splitter.split_text(text)
    docs: list[Document] = []
    for i, chunk in enumerate(raw_chunks):
        chunk_id = f"{file.file_id}::{i}"
        docs.append(
            Document(
                page_content=chunk,
                metadata={
                    "source": f"Google Drive: {file.name}",
                    "chunk_id": chunk_id,
                    "file_id": file.file_id,
                    "chunk_index": i,
                    "web_view_link": file.web_view_link,
                    "modified_time": file.modified_time,
                },
            )
        )
    return docs
