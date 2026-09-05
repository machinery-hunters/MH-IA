"""
indexer

Orquesta el pipeline completo de ingesta:

  Drive (list + download) -> loaders (texto) -> chunker (Document + metadata)
  -> OllamaEmbeddings -> upsert en Qdrant

Sync incremental:
  Se mantiene un manifest local (JSON) file_id -> modifiedTime. Si un
  archivo no cambió desde la última corrida, se omite por completo
  (no se re-descarga ni re-embebe). Si cambió, se borran sus chunks
  anteriores en Qdrant (por file_id) antes de insertar los nuevos, para
  no dejar chunks obsoletos huérfanos.

Nota de privacidad: todo este pipeline corre local (Ollama + Qdrant en
Docker). Ningún contenido de Drive se envía a la API de Claude en esta
etapa — eso solo ocurre, ya anonimizado, en tiempo de consulta
(app/nodes/ollama_node.py -> app/nodes/claude_node.py).
"""

from __future__ import annotations

import json
import logging
import os
import uuid


from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import settings
from app.embeddings import TaskPrefixedOllamaEmbeddings
from app.ingestion.chunker import chunk_file
from app.ingestion.drive_client import DriveClient, DriveFile
from app.ingestion.loaders import extract_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mhia.ingest")


def _load_manifest() -> dict:
    if os.path.exists(settings.sync_manifest_path):
        with open(settings.sync_manifest_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_manifest(manifest: dict) -> None:
    os.makedirs(os.path.dirname(settings.sync_manifest_path), exist_ok=True)
    with open(settings.sync_manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def _point_id(chunk_id: str) -> str:
    # Qdrant exige IDs UUID o entero: derivamos un UUID determinístico
    # del chunk_id para poder recalcular/actualizar el mismo punto siempre.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


class Indexer:
    def __init__(self) -> None:
        self.drive = DriveClient()
        self.embeddings = TaskPrefixedOllamaEmbeddings(
            model=settings.ollama_embed_model, base_url=settings.ollama_base_url
        )
        self.qdrant = QdrantClient(url=settings.qdrant_url)

    def _ensure_collection(self) -> None:
        collections = [c.name for c in self.qdrant.get_collections().collections]
        if settings.qdrant_collection in collections:
            return

        # Determinar dimensión embebiendo un texto de prueba
        dim = len(self.embeddings.embed_query("dimension probe"))
        self.qdrant.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
        )
        log.info("Colección '%s' creada (dim=%d)", settings.qdrant_collection, dim)

    def _delete_file_chunks(self, file_id: str) -> None:
        self.qdrant.delete(
            collection_name=settings.qdrant_collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="metadata.file_id",
                            match=qmodels.MatchValue(value=file_id),
                        )
                    ]
                )
            ),
        )

    def _index_file(self, file: DriveFile) -> int:
        raw = self.drive.download_content(file)
        text = extract_text(file, raw)
        docs = chunk_file(file, text)

        if not docs:
            log.warning("Sin contenido extraíble: %s", file.name)
            return 0

        vectors = self.embeddings.embed_documents([d.page_content for d in docs])

        points = [
            qmodels.PointStruct(
                id=_point_id(doc.metadata["chunk_id"]),
                vector=vector,
                payload={"page_content": doc.page_content, "metadata": doc.metadata},
            )
            for doc, vector in zip(docs, vectors, strict=True)
        ]

        self.qdrant.upsert(collection_name=settings.qdrant_collection, points=points)
        return len(points)

    def sync(self, folder_id: str | None = None) -> None:
        folder_id = folder_id or settings.google_drive_folder_id
        if not folder_id:
            raise ValueError("Falta GOOGLE_DRIVE_FOLDER_ID (o pásalo explícito).")

        self._ensure_collection()
        manifest = _load_manifest()

        files = self.drive.list_files_recursive(folder_id)
        log.info("Encontrados %d archivos en Drive", len(files))

        seen_ids = set()
        indexed, skipped, failed = 0, 0, 0

        for file in files:
            seen_ids.add(file.file_id)
            prev = manifest.get(file.file_id)

            if prev and prev.get("modified_time") == file.modified_time:
                skipped += 1
                continue

            try:
                if prev:
                    self._delete_file_chunks(file.file_id)

                n_chunks = self._index_file(file)
                manifest[file.file_id] = {
                    "name": file.name,
                    "modified_time": file.modified_time,
                    "n_chunks": n_chunks,
                }
                indexed += 1
                log.info("Indexado: %s (%d chunks)", file.name, n_chunks)
            except Exception:
                failed += 1
                log.exception("Fallo indexando %s", file.name)

        # Archivos borrados/movidos fuera del folder en Drive -> limpiar de Qdrant
        removed_ids = set(manifest.keys()) - seen_ids
        for file_id in removed_ids:
            self._delete_file_chunks(file_id)
            log.info("Eliminado de Qdrant (ya no está en Drive): %s", manifest[file_id]["name"])
            del manifest[file_id]

        _save_manifest(manifest)
        log.info(
            "Sync completo. indexados=%d, sin_cambios=%d, fallidos=%d, eliminados=%d",
            indexed,
            skipped,
            failed,
            len(removed_ids),
        )
