"""
drive_client

Acceso de SOLO LECTURA a Google Drive vía cuenta de servicio.

Por qué cuenta de servicio y no OAuth de usuario:
- No requiere flujo interactivo de login (corre desatendido en el Mac Mini).
- Se limita a las carpetas que explícitamente compartas con su email
  (client_email del JSON), a diferencia de un OAuth de usuario que arrastra
  todo el Drive personal.

Setup (una vez, ver DEPLOYMENT.md / INGESTION.md):
  1. Crear un Service Account en Google Cloud Console.
  2. Habilitar Google Drive API.
  3. Descargar el JSON de credenciales -> ./credentials/service_account.json
  4. En Drive, compartir la carpeta objetivo con el "client_email" del JSON
     (rol: Lector).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from pipeline_ai.config import settings

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Exportamos los formatos nativos de Google Workspace a texto plano / csv
# para no depender de parsers propietarios.
GOOGLE_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

FOLDER_MIME = "application/vnd.google-apps.folder"

SUPPORTED_BINARY_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "text/plain",
    "text/csv",
}


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    modified_time: str
    web_view_link: str
    # Nombres de carpeta desde la raíz compartida hasta el archivo (sin
    # incluir el archivo mismo). folder_path[0] es la carpeta de
    # departamento (Ventas, Inventario, Proveedores, ...) — se usa para
    # clasificar el dominio de negocio del documento en pipeline_ai/ingestion/
    # domain_mapping.py, independiente de la clasificación que hace el
    # router sobre la consulta del usuario.
    folder_path: list[str]


class DriveClient:
    def __init__(self) -> None:
        creds = service_account.Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
        self._service = build("drive", "v3", credentials=creds)

    def list_files_recursive(self, folder_id: str) -> list[DriveFile]:
        """Recorre la carpeta y subcarpetas, devolviendo solo archivos
        (Google-native soportados o binarios soportados)."""
        files: list[DriveFile] = []
        self._walk(folder_id, files, path=[])
        return files

    def _walk(self, folder_id: str, acc: list[DriveFile], path: list[str]) -> None:
        query = f"'{folder_id}' in parents and trashed = false"
        page_token = None
        while True:
            resp = (
                self._service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
                    pageToken=page_token,
                    pageSize=100,
                )
                .execute()
            )
            for f in resp.get("files", []):
                mime = f["mimeType"]
                if mime == FOLDER_MIME:
                    self._walk(f["id"], acc, path=[*path, f["name"]])
                    continue
                if mime in GOOGLE_EXPORT_MIME or mime in SUPPORTED_BINARY_MIME:
                    acc.append(
                        DriveFile(
                            file_id=f["id"],
                            name=f["name"],
                            mime_type=mime,
                            modified_time=f["modifiedTime"],
                            web_view_link=f.get("webViewLink", ""),
                            folder_path=path,
                        )
                    )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def download_content(self, file: DriveFile) -> bytes:
        """Devuelve el contenido crudo (bytes) listo para pasar a loaders.py."""
        if file.mime_type in GOOGLE_EXPORT_MIME:
            export_mime = GOOGLE_EXPORT_MIME[file.mime_type]
            request = self._service.files().export_media(fileId=file.file_id, mimeType=export_mime)
        else:
            request = self._service.files().get_media(fileId=file.file_id)

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()
