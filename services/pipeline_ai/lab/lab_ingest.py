"""Ingesta local de los documentos SINTETICOS de lab/docs hacia el Qdrant del laboratorio.

Reutiliza el mismo codigo de produccion (loaders, chunker, embeddings, dominio por carpeta);
solo cambia el origen: una carpeta local en lugar de Google Drive. La primera subcarpeta de
lab/docs hace de "carpeta de departamento" (Ventas, Inventario, Proveedores, ...).

Recrea la coleccion en cada corrida: el laboratorio es desechable.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

import _env  # DEBE ir antes que pipeline_ai: fija QDRANT_URL/COLLECTION antes de leer settings

# isort: split
from qdrant_client import QdrantClient

from pipeline_ai.config import settings
from pipeline_ai.embeddings import TaskPrefixedOllamaEmbeddings
from pipeline_ai.ingestion.chunker import chunk_file
from pipeline_ai.ingestion.domain_mapping import infer_domain_from_folder_path
from pipeline_ai.ingestion.drive_client import DriveFile
from pipeline_ai.ingestion.loaders import extract_text
from pipeline_ai.retrieval.collection import build_point, create_collection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mhia.lab.ingest")

_MIME_BY_SUFFIX = {
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _local_files() -> list[tuple[DriveFile, bytes]]:
    files: list[tuple[DriveFile, bytes]] = []
    for path in sorted(_env.DOCS_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _MIME_BY_SUFFIX:
            continue
        relative = path.relative_to(_env.DOCS_DIR)
        raw = path.read_bytes()
        files.append(
            (
                DriveFile(
                    file_id=hashlib.sha256(str(relative).encode("utf-8")).hexdigest()[:16],
                    name=path.name,
                    mime_type=_MIME_BY_SUFFIX[path.suffix.lower()],
                    modified_time="lab",
                    web_view_link="",
                    folder_path=list(relative.parts[:-1]),
                ),
                raw,
            )
        )
    return files


def main() -> None:
    files = _local_files()
    if not files:
        raise SystemExit(f"No hay documentos en {_env.DOCS_DIR}")

    embeddings = TaskPrefixedOllamaEmbeddings(
        model=settings.ollama_embed_model, base_url=settings.ollama_base_url
    )
    client = QdrantClient(url=settings.qdrant_url)

    dim = len(embeddings.embed_query("dimension probe"))
    if settings.qdrant_collection in [c.name for c in client.get_collections().collections]:
        client.delete_collection(settings.qdrant_collection)
    create_collection(client, settings.qdrant_collection, dim)
    log.info("Coleccion '%s' recreada (dim=%d)", settings.qdrant_collection, dim)

    total_chunks = 0
    for file, raw in files:
        docs = chunk_file(file, extract_text(file, raw))
        if not docs:
            log.warning("Sin contenido extraible: %s", file.name)
            continue
        vectors = embeddings.embed_documents([d.page_content for d in docs])
        points = [
            build_point(_point_id(doc.metadata["chunk_id"]), doc, vector)
            for doc, vector in zip(docs, vectors, strict=True)
        ]
        client.upsert(collection_name=settings.qdrant_collection, points=points)
        total_chunks += len(points)
        log.info(
            "Indexado: %-45s %2d chunks  carpeta=%-14s dominio=%s",
            file.name,
            len(points),
            "/".join(file.folder_path) or "(raiz)",
            infer_domain_from_folder_path(file.folder_path),
        )

    log.info("Listo: %d archivos, %d chunks.", len(files), total_chunks)


if __name__ == "__main__":
    main()
