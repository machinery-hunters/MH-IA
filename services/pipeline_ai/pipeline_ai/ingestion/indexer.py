"""
indexer

Orquesta el pipeline completo de ingesta:

  Drive (list + download) -> loaders (texto) -> chunker (Document + metadata)
  -> OllamaEmbeddings + vector de palabras clave -> upsert en Qdrant

Cada punto lleva dos vectores (ver retrieval/collection.py): el denso de nomic-embed-text y el
disperso BM25 para la búsqueda híbrida.

Sync incremental:
  Se mantiene un manifest local (JSON) file_id -> modifiedTime. Si un
  archivo no cambió desde la última corrida, se omite por completo
  (no se re-descarga ni re-embebe). Si cambió, se borran sus chunks
  anteriores en Qdrant (por file_id) antes de insertar los nuevos, para
  no dejar chunks obsoletos huérfanos.

Colección anterior (sin vectores híbridos): la ingesta NO mezcla puntos de esquemas distintos.
Se detiene con instrucciones para reindexar en una colección nueva.

Nota de privacidad: todo este pipeline corre local (Ollama + Qdrant en
Docker). Ningún contenido de Drive se envía a la API de Claude en esta
etapa — eso solo ocurre, ya anonimizado, en tiempo de consulta
(pipeline_ai/nodes/ollama_node.py -> pipeline_ai/nodes/claude_node.py).
"""

from __future__ import annotations

import json
import logging
import os
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from pipeline_ai.config import settings
from pipeline_ai.embeddings import TaskPrefixedOllamaEmbeddings
from pipeline_ai.ingestion.chunker import chunk_file
from pipeline_ai.ingestion.domain_mapping import UNCLASSIFIED, infer_domain_from_folder_path
from pipeline_ai.ingestion.drive_client import DriveClient, DriveFile
from pipeline_ai.ingestion.loaders import extract_text
from pipeline_ai.retrieval.collection import build_point, create_collection, is_hybrid

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
            info = self.qdrant.get_collection(settings.qdrant_collection)
            if not is_hybrid(info):
                raise RuntimeError(
                    f"La colección '{settings.qdrant_collection}' usa el esquema anterior "
                    "(sin vectores de palabras clave). Para reindexar: define "
                    "QDRANT_COLLECTION con un nombre nuevo, borra el manifest "
                    f"({settings.sync_manifest_path}) y vuelve a correr la ingesta; cuando "
                    "termine, apunta la API a la colección nueva y borra la anterior."
                )
            return

        # Determinar dimensión embebiendo un texto de prueba
        dim = len(self.embeddings.embed_query("dimension probe"))
        create_collection(self.qdrant, settings.qdrant_collection, dim)
        log.info("Colección híbrida '%s' creada (dim=%d)", settings.qdrant_collection, dim)

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
            build_point(_point_id(doc.metadata["chunk_id"]), doc, vector)
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
        indexed, skipped, failed, unclassified = 0, 0, 0, 0

        for file in files:
            seen_ids.add(file.file_id)
            prev = manifest.get(file.file_id)

            # Falla cerrada: sin dominio asignado nadie podría verlo (RBAC) y no se debe
            # indexar a ciegas. No entra al manifest: al mapear su carpeta, el siguiente sync
            # lo indexa solo. Si ya estaba indexado (carpeta renombrada), se retira.
            if infer_domain_from_folder_path(file.folder_path) == UNCLASSIFIED:
                unclassified += 1
                if prev:
                    self._delete_file_chunks(file.file_id)
                    del manifest[file.file_id]
                log.warning(
                    "Sin dominio: la carpeta '%s' no está en domain_mapping.py; el archivo "
                    "NO se indexa hasta asignarle un dominio.",
                    "/".join(file.folder_path) or "(raíz)",
                )
                continue

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
                domain = infer_domain_from_folder_path(file.folder_path)
                log.info(
                    "Indexado: %s (%d chunks, carpeta=%s, dominio=%s)",
                    file.name,
                    n_chunks,
                    "/".join(file.folder_path) or "(raíz)",
                    domain,
                )
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
            "Sync completo. indexados=%d, sin_cambios=%d, fallidos=%d, sin_dominio=%d, "
            "eliminados=%d",
            indexed,
            skipped,
            failed,
            unclassified,
            len(removed_ids),
        )
