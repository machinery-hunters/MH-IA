import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Claude
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Ollama
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_llm_model: str = os.getenv("OLLAMA_LLM_MODEL", "llama3.1:8b")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    # Qdrant
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "mhia_drive_docs")
    retrieval_score_threshold: float = float(os.getenv("RETRIEVAL_SCORE_THRESHOLD", "0.72"))

    # ERPNext (Fase 2)
    erpnext_base_url: str = os.getenv("ERPNEXT_BASE_URL", "")
    erpnext_api_key: str = os.getenv("ERPNEXT_API_KEY", "")
    erpnext_api_secret: str = os.getenv("ERPNEXT_API_SECRET", "")

    # Ingesta Google Drive (Fase 1)
    google_service_account_file: str = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE", "/app/credentials/service_account.json"
    )
    google_drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "1000"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    sync_manifest_path: str = os.getenv("SYNC_MANIFEST_PATH", "/app/data/sync_manifest.json")

    # Dominios de datos que SIEMPRE se consideran confidenciales
    # (regla 2: ventas, inventario -subasta/consignación-, proveedores)
    confidential_domains: tuple[str, ...] = ("ventas", "inventario", "proveedores")

    def require(self, *fields: str) -> None:
        """Valida en el punto de uso (no al importar) que ciertas variables
        obligatorias estén presentes. Permite importar app.config y construir
        el grafo en tests/CI sin credenciales reales, y falla con un mensaje
        claro solo cuando el nodo que sí las necesita se ejecuta de verdad."""
        missing = [f for f in fields if not getattr(self, f, "")]
        if missing:
            raise RuntimeError(
                f"Faltan variables de entorno requeridas: {', '.join(missing)}. "
                f"Revisa tu archivo .env (ver .env.example)."
            )


settings = Settings()
