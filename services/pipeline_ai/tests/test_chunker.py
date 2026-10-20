"""Chunker: tablas por filas con encabezado repetido, texto por caracteres, metadata y dominio."""

from pipeline_ai.ingestion.chunker import chunk_file, split_table
from pipeline_ai.ingestion.drive_client import DriveFile

HEADER = "proyecto, responsable, estado, avance, fecha"


def _table(n_rows: int) -> str:
    rows = [
        f"proyecto {i}, persona {i}, en curso, {i}%, 2026-10-{i % 28 + 1:02d}"
        for i in range(n_rows)
    ]
    return "\n".join([HEADER, *rows])


def _file(mime="application/vnd.google-apps.spreadsheet", folder=("Proyectos",)) -> DriveFile:
    return DriveFile(
        file_id="f1",
        name="Hoja.gsheet",
        mime_type=mime,
        modified_time="2026-10-01T00:00:00Z",
        web_view_link="",
        folder_path=list(folder),
    )


# -- split_table -------------------------------------------------------------------------------


def test_every_table_chunk_starts_with_the_header_row():
    chunks = split_table(_table(60), max_chars=400)

    assert len(chunks) > 1
    assert all(chunk.splitlines()[0] == HEADER for chunk in chunks)


def test_no_row_is_lost_duplicated_or_cut_and_order_is_kept():
    text = _table(60)
    chunks = split_table(text, max_chars=400)

    rows = [ln for chunk in chunks for ln in chunk.splitlines()[1:]]
    assert rows == text.splitlines()[1:]


def test_chunks_respect_the_size_limit():
    chunks = split_table(_table(60), max_chars=400)

    assert all(len(chunk) <= 400 for chunk in chunks)


def test_a_row_longer_than_the_limit_goes_alone_with_its_header_and_is_not_cut():
    long_row = "x" * 900
    chunks = split_table(f"{HEADER}\nfila corta\n{long_row}\notra corta", max_chars=300)

    assert any(long_row in chunk and chunk.startswith(HEADER) for chunk in chunks)
    assert sum(chunk.count(long_row) for chunk in chunks) == 1


def test_a_small_table_is_a_single_chunk():
    assert split_table(_table(3), max_chars=1000) == [_table(3)]


def test_blank_lines_are_ignored():
    assert split_table(f"{HEADER}\n\n\nfila uno\n\n", max_chars=1000) == [f"{HEADER}\nfila uno"]


def test_degenerate_tables():
    assert split_table("", max_chars=100) == []
    assert split_table("solo encabezado", max_chars=100) == ["solo encabezado"]


# -- chunk_file --------------------------------------------------------------------------------


def test_spreadsheets_and_csv_are_chunked_as_tables():
    for mime in ("application/vnd.google-apps.spreadsheet", "text/csv"):
        docs = chunk_file(_file(mime=mime), _table(200))

        assert len(docs) > 1
        assert all(d.page_content.startswith(HEADER) for d in docs)


def test_plain_text_is_not_treated_as_a_table():
    text = "\n\n".join(f"Párrafo {i}. " + "texto " * 40 for i in range(20))
    docs = chunk_file(_file(mime="text/plain", folder=("General",)), text)

    assert len(docs) > 1
    assert sum(d.page_content.startswith("Párrafo 0") for d in docs) == 1


def test_metadata_carries_the_domain_the_source_and_stable_chunk_ids():
    docs = chunk_file(_file(), _table(200))

    assert [d.metadata["chunk_id"] for d in docs] == [f"f1::{i}" for i in range(len(docs))]
    assert {d.metadata["domain"] for d in docs} == {"proyectos"}
    assert {d.metadata["source"] for d in docs} == {"Google Drive: Hoja.gsheet"}
    assert {d.metadata["department"] for d in docs} == {"Proyectos"}


def test_a_file_in_an_unrecognised_folder_gets_the_fail_closed_domain():
    docs = chunk_file(_file(folder=("Recursos Humanos",)), _table(5))

    assert {d.metadata["domain"] for d in docs} == {"sin_clasificar"}


def test_empty_text_gives_no_chunks():
    assert chunk_file(_file(), "  \n ") == []
