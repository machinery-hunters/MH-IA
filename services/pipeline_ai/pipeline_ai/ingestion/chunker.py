"""
chunker

Produce Document(page_content, metadata) donde metadata debe casar EXACTO
con lo que pipeline_ai/nodes/retriever_node.py espera al construir `citations`:
  - metadata["source"]  -> lo que se muestra al usuario/Claude como cita
  - metadata["chunk_id"] -> identificador único y estable del chunk
  - metadata["domain"]   -> dominio de negocio inferido de la carpeta
    (ventas/inventario/proveedores/general), usado por retriever_node.py
    como segunda señal de confidencialidad (ver domain_mapping.py)
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from pipeline_ai.config import settings
from pipeline_ai.ingestion.domain_mapping import infer_domain_from_folder_path
from pipeline_ai.ingestion.drive_client import DriveFile

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=settings.chunk_size,
    chunk_overlap=settings.chunk_overlap,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_file(file: DriveFile, text: str) -> list[Document]:
    if not text.strip():
        return []

    domain = infer_domain_from_folder_path(file.folder_path)
    department = file.folder_path[0] if file.folder_path else "(raíz)"

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
                    "domain": domain,
                    "department": department,
                },
            )
        )
    return docs
