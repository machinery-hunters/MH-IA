"""
chunker

Produce Document(page_content, metadata) donde metadata debe casar EXACTO
con lo que pipeline_ai/nodes/retriever_node.py espera al construir `citations`:
  - metadata["source"]  -> lo que se muestra al usuario/Claude como cita
  - metadata["chunk_id"] -> identificador único y estable del chunk
  - metadata["area"]     -> área de negocio inferida de la carpeta (security.areas.Area), que
    decide quién lo ve (security.access) y su sensibilidad mínima (ver domain_mapping.py)
  - metadata["sensitivity"] -> nivel (int de Sensitivity) con que se indexó: la mínima del
    área. Queda en la base para filtrar por nivel dentro de Qdrant; una etiqueta futura por
    documento solo podrá subirlo

Tablas (hojas de cálculo y CSV): se parten por FILAS y cada fragmento repite la fila de
encabezados. Cortar por caracteres dejaba los fragmentos 2..N sin nombres de columna, y una
fila de 20 columnas sin encabezado no significa nada ni para el juez de relevancia ni para
Claude.
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from pipeline_ai.config import settings
from pipeline_ai.ingestion.domain_mapping import UNCLASSIFIED, infer_area_from_folder_path
from pipeline_ai.ingestion.drive_client import GOOGLE_EXPORT_MIME, DriveFile
from pipeline_ai.security.areas import AREA_MIN_SENSITIVITY, Area

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=settings.chunk_size,
    chunk_overlap=settings.chunk_overlap,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def split_table(text: str, max_chars: int) -> list[str]:
    """Parte una tabla en fragmentos de hasta ~max_chars, cada uno con la fila de encabezados
    al inicio. Una fila más larga que el límite va sola (con su encabezado): nunca se corta."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return ["\n".join(lines)] if lines else []

    header, rows = lines[0], lines[1:]
    chunks: list[str] = []
    current: list[str] = []
    size = len(header)
    for row in rows:
        if current and size + len(row) + 1 > max_chars:
            chunks.append("\n".join([header, *current]))
            current, size = [], len(header)
        current.append(row)
        size += len(row) + 1
    if current:
        chunks.append("\n".join([header, *current]))
    return chunks


def _is_table(file: DriveFile) -> bool:
    return GOOGLE_EXPORT_MIME.get(file.mime_type, file.mime_type) == "text/csv"


def chunk_file(file: DriveFile, text: str) -> list[Document]:
    if not text.strip():
        return []

    area_name = infer_area_from_folder_path(file.folder_path)
    if area_name == UNCLASSIFIED:
        # El indexer ya los descarta antes; aquí se repite por si alguien llama directo.
        raise ValueError("archivo sin área: no se fragmenta ni se indexa")
    area = Area(area_name)
    department = file.folder_path[0] if file.folder_path else "(raíz)"

    raw_chunks = (
        split_table(text, settings.chunk_size) if _is_table(file) else _splitter.split_text(text)
    )
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
                    "area": area.value,
                    "sensitivity": int(AREA_MIN_SENSITIVITY[area]),
                    "department": department,
                },
            )
        )
    return docs
