# Ingesta Google Drive -> Qdrant

## 1. Crear cuenta de servicio (una sola vez)

1. Google Cloud Console -> IAM & Admin -> Service Accounts -> Create.
2. Habilitar **Google Drive API** en el proyecto.
3. Generar una key JSON y guardarla en `./credentials/service_account.json`
   (esta carpeta ya está montada `:ro` en el contenedor y en `.gitignore`).
4. Copiar el `client_email` del JSON (algo como
   `mhia-ingest@tu-proyecto.iam.gserviceaccount.com`).
5. En Google Drive, compartir la carpeta raíz de documentos de Machinery
   Hunters con ese email, rol **Lector**.
6. Copiar el ID de esa carpeta (el segmento después de `/folders/` en la URL)
   a `GOOGLE_DRIVE_FOLDER_ID` en `.env`.

## 2. Tipos de archivo soportados

- Google Docs, Sheets, Slides (se exportan automáticamente a texto/CSV)
- PDF (`.pdf`)
- Word (`.docx`)
- Texto plano / CSV

Otros tipos (imágenes, hojas de cálculo con macros, etc.) se ignoran
silenciosamente por ahora.

## 3. Correr la sincronización

```bash
# la app ya debe estar levantada (docker compose up -d con el override correspondiente)
docker exec -it mhia-app python -m app.ingestion.run_ingest
```

Es **incremental**: guarda un manifest en `./data/sync_manifest.json` con
`modifiedTime` por archivo. Si un archivo no cambió desde la última corrida,
se omite. Si cambió, se borran sus chunks viejos en Qdrant y se re-indexa.
Si un archivo se eliminó/movió fuera de la carpeta en Drive, sus chunks se
eliminan de Qdrant automáticamente.

## 4. Automatizar (cron en el Mac Mini de producción)

```bash
# crontab -e
0 */6 * * * cd /ruta/a/mh-ia && docker exec mhia-app python -m app.ingestion.run_ingest >> ./data/ingest.log 2>&1
```

## 5. Relación con las reglas críticas del sistema

- Todo el pipeline (descarga, extracción, chunking, embeddings) corre
  local: Drive -> contenedor `mhia-app` -> Ollama (embeddings) -> Qdrant.
  Nada de esto toca la API de Claude.
- El `metadata.source` guardado en cada chunk (`"Google Drive: <nombre>"`)
  es exactamente lo que `retriever_node.py` usa para construir `citations`,
  y lo que `claude_node.py` exige citar en cada afirmación — así se cierra
  el círculo con la regla de "cero alucinaciones".
