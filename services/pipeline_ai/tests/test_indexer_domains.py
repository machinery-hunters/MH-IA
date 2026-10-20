"""Ingesta: los archivos de carpetas sin dominio no se indexan (falla cerrada) y se recuperan solos
cuando la carpeta se mapea."""

import json
import logging

import pytest

from pipeline_ai.ingestion import domain_mapping
from pipeline_ai.ingestion.drive_client import DriveFile
from pipeline_ai.ingestion.indexer import Indexer


def _file(folder, file_id="f1", modified="2026-10-01"):
    return DriveFile(
        file_id=file_id,
        name=f"{file_id}.txt",
        mime_type="text/plain",
        modified_time=modified,
        web_view_link="",
        folder_path=list(folder),
    )


class FakeDrive:
    def __init__(self, files):
        self.files = files

    def list_files_recursive(self, folder_id):
        return self.files

    def download_content(self, file):
        return f"contenido de {file.file_id}".encode()


class FakeEmbeddings:
    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeQdrant:
    def __init__(self):
        self.upserts = []
        self.deleted_file_ids = []

    def upsert(self, collection_name, points):
        self.upserts.extend(points)

    def delete(self, collection_name, points_selector):
        self.deleted_file_ids.append(points_selector.filter.must[0].match.value)


@pytest.fixture
def make_indexer(monkeypatch, tmp_path):
    monkeypatch.setattr(Indexer, "_ensure_collection", lambda self: None)
    monkeypatch.setattr(
        "pipeline_ai.ingestion.indexer.settings.sync_manifest_path",
        str(tmp_path / "manifest.json"),
    )
    manifest_path = tmp_path / "manifest.json"

    def _make(files):
        indexer = Indexer.__new__(Indexer)
        indexer.drive = FakeDrive(files)
        indexer.embeddings = FakeEmbeddings()
        indexer.qdrant = FakeQdrant()
        return indexer

    _make.manifest = lambda: json.loads(manifest_path.read_text(encoding="utf-8"))
    return _make


def _domains(indexer):
    return {p.payload["metadata"]["domain"] for p in indexer.qdrant.upserts}


def test_a_file_in_an_unrecognised_folder_is_not_indexed_nor_registered(make_indexer, caplog):
    indexer = make_indexer([_file(["Recursos Humanos"])])
    with caplog.at_level(logging.WARNING):
        indexer.sync(folder_id="root")

    assert indexer.qdrant.upserts == []
    assert make_indexer.manifest() == {}
    assert "Sin dominio" in caplog.text and "Recursos Humanos" in caplog.text


def test_files_at_the_shared_root_are_not_indexed(make_indexer):
    indexer = make_indexer([_file([])])
    indexer.sync(folder_id="root")

    assert indexer.qdrant.upserts == []


def test_classified_files_are_indexed_with_their_domain(make_indexer):
    indexer = make_indexer([_file(["Proyectos"]), _file(["General"], file_id="f2")])
    indexer.sync(folder_id="root")

    assert _domains(indexer) == {"proyectos", "general"}
    assert set(make_indexer.manifest()) == {"f1", "f2"}


def test_once_the_folder_is_mapped_the_next_sync_indexes_the_file(make_indexer, monkeypatch):
    first = make_indexer([_file(["Mi Carpeta Nueva"])])
    first.sync(folder_id="root")
    assert first.qdrant.upserts == []

    # Se agrega la palabra clave a domain_mapping.py: el mismo archivo, sin cambios en Drive.
    monkeypatch.setitem(domain_mapping._KEYWORDS, "proyectos", ("proyecto", "carpeta"))
    second = make_indexer([_file(["Mi Carpeta Nueva"])])
    second.sync(folder_id="root")

    assert _domains(second) == {"proyectos"}


def test_a_previously_indexed_file_whose_folder_stops_being_mapped_is_withdrawn(make_indexer):
    first = make_indexer([_file(["Ventas"])])
    first.sync(folder_id="root")
    assert _domains(first) == {"ventas"}

    # La carpeta se renombra a algo que ya no coincide con ningún dominio.
    second = make_indexer([_file(["Archivo muerto"])])
    second.sync(folder_id="root")

    assert second.qdrant.deleted_file_ids == ["f1"]  # sus fragmentos salen de Qdrant
    assert second.qdrant.upserts == []
    assert make_indexer.manifest() == {}


def test_a_withdrawn_file_is_deleted_only_once(make_indexer):
    make_indexer([_file(["Ventas"])]).sync(folder_id="root")
    second = make_indexer([_file(["Archivo muerto"])])
    second.sync(folder_id="root")

    # Sigue en Drive (seen_ids): no debe contarse además como "eliminado de Drive".
    assert second.qdrant.deleted_file_ids.count("f1") == 1


def test_unchanged_files_are_skipped(make_indexer):
    make_indexer([_file(["Ventas"])]).sync(folder_id="root")
    again = make_indexer([_file(["Ventas"])])
    again.sync(folder_id="root")

    assert again.qdrant.upserts == []
