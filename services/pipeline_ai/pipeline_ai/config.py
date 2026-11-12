import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


REASONING_MODES = ("local", "claude")
# Niveles válidos de MFA_REQUIRED_LEVEL (nombres de privacy.sensitivity.Sensitivity).
MFA_LEVELS = ("INTERNAL", "CONFIDENTIAL", "RESTRICTED")


class Settings:
    # ── Entorno ──────────────────────────────────────────────────────────
    # "production" activa require_production_secrets() al arrancar la API.
    environment: str = os.getenv("MHIA_ENV", "development")
    # DEBUG apagado por defecto. Con True se exponen /docs y trazas más ricas.
    debug: bool = _env_bool("MHIA_DEBUG", False)

    # ── Razonamiento ─────────────────────────────────────────────────────
    # Quién redacta la respuesta final (plan maestro, sección 8.1):
    #   local  -> modelo de Ollama (por defecto): sin costo y sin salida de red.
    #   claude -> API de Anthropic (se paga por uso); si falla, responde el modelo local.
    # La pasarela de salida y el anonimizador corren igual en los dos modos.
    reasoning_mode: str = os.getenv("REASONING_MODE", "local").strip().lower()

    # ── Claude ───────────────────────────────────────────────────────────
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    # Tope por llamada a la API; al excederse responde el modelo local. Corto a propósito: la
    # caída a local tiene que caber en QUERY_TIMEOUT_S.
    claude_timeout_s: float = float(os.getenv("CLAUDE_TIMEOUT_S", "30"))

    # ── Ollama ───────────────────────────────────────────────────────────
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_llm_model: str = os.getenv("OLLAMA_LLM_MODEL", "llama3.1:8b")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    # Modelo que redacta la respuesta en modo local. En la Mac, el "modelo grande" del plan
    # (sección 3.3) una vez medido; vacío = el mismo modelo de trabajo (OLLAMA_LLM_MODEL).
    # Debe estar en scripts/ollama-models.txt (los doctores lo revisan).
    ollama_reasoner_model: str = os.getenv("OLLAMA_REASONER_MODEL", "").strip() or ollama_llm_model
    reasoner_timeout_s: float = float(os.getenv("REASONER_TIMEOUT_S", "90"))
    reasoner_num_ctx: int = int(os.getenv("REASONER_NUM_CTX", "8192"))
    reasoner_max_tokens: int = int(os.getenv("REASONER_MAX_TOKENS", "1024"))

    # ── Qdrant ───────────────────────────────────────────────────────────
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "mhia_drive_docs")
    # Piso absoluto de score. Con nomic-embed-text NO separa relevante de irrelevante (en el
    # laboratorio hay negativos de 0.71 y positivos de 0.57): solo descarta lo claramente ajeno.
    retrieval_score_threshold: float = float(os.getenv("RETRIEVAL_SCORE_THRESHOLD", "0.6"))
    # Corte relativo (minimización de datos): solo se conservan los fragmentos cuyo score está
    # a menos de `margen` del mejor. 0 = desactivado (solo piso absoluto).
    retrieval_score_margin: float = float(os.getenv("RETRIEVAL_SCORE_MARGIN", "0.06"))
    # Juez de relevancia local (Ollama): decide qué fragmentos candidatos responden de verdad la
    # pregunta. Es el criterio principal; el piso/margen de arriba queda como respaldo si el
    # juez falla. Cada consulta lanza hasta `candidates` juicios (de a `concurrency`).
    retrieval_judge_enabled: bool = _env_bool("RETRIEVAL_JUDGE_ENABLED", True)
    retrieval_candidates: int = int(os.getenv("RETRIEVAL_CANDIDATES", "8"))
    # Máximo de fragmentos aprobados que viajan al contexto (minimización de datos).
    retrieval_max_context: int = int(os.getenv("RETRIEVAL_MAX_CONTEXT", "4"))
    retrieval_judge_timeout_s: float = float(os.getenv("RETRIEVAL_JUDGE_TIMEOUT_S", "30"))
    retrieval_judge_concurrency: int = int(os.getenv("RETRIEVAL_JUDGE_CONCURRENCY", "2"))

    # ── Anonimizador local ───────────────────────────────────────────────
    # Tope de tiempo (s) por llamada al LLM local que detecta nombres propios. Si se excede,
    # la anonimización falla cerrada: no se envía nada a Claude.
    anonymizer_timeout_s: float = float(os.getenv("ANONYMIZER_TIMEOUT_S", "45"))
    anonymizer_num_ctx: int = int(os.getenv("ANONYMIZER_NUM_CTX", "4096"))
    # Términos que NO se redactan aunque el LLM los marque como nombres: la propia empresa y
    # marcas públicas de maquinaria (no identifican a un cliente y los necesita el razonamiento).
    anonymizer_keep_terms: tuple[str, ...] = _env_list(
        "ANONYMIZER_KEEP_TERMS",
        "Machinery Hunters,MH,Caterpillar,CAT,John Deere,Komatsu,Bobcat,Dynapac,Volvo,Hitachi,"
        "Liebherr,JCB,Doosan,Kubota,Terex,Sany,XCMG,Case,Hyundai",
    )

    # ── Conectores (cada uno se valida en el punto de uso, nunca al importar) ──
    # ERP (Frappe/ERPNext) — pendiente del proveedor externo
    erpnext_base_url: str = os.getenv("ERPNEXT_BASE_URL", "")
    erpnext_api_key: str = os.getenv("ERPNEXT_API_KEY", "")
    erpnext_api_secret: str = os.getenv("ERPNEXT_API_SECRET", "")

    # CRM (GoHighLevel)
    ghl_base_url: str = os.getenv("GHL_BASE_URL", "https://services.leadconnectorhq.com")
    ghl_api_token: str = os.getenv("GHL_API_TOKEN", "")
    ghl_location_id: str = os.getenv("GHL_LOCATION_ID", "")

    # Firestore (SourcingBot / portafolio aprobado)
    firestore_project_id: str = os.getenv("FIRESTORE_PROJECT_ID", "")
    firestore_credentials_file: str = os.getenv(
        "FIRESTORE_CREDENTIALS_FILE", "/app/credentials/firestore_service_account.json"
    )

    # Supabase (SourcingBot)
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    # Google Drive (ingesta + conector)
    google_service_account_file: str = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE", "/app/credentials/service_account.json"
    )
    google_drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "1000"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "150"))
    sync_manifest_path: str = os.getenv("SYNC_MANIFEST_PATH", "/app/data/sync_manifest.json")

    # ── Timeouts por sub-agente (segundos) ───────────────────────────────
    # Un sub-agente que excede su timeout se marca TIMEOUT y NO bloquea a los demás.
    timeout_drive_s: float = float(os.getenv("TIMEOUT_DRIVE_S", "15"))
    timeout_erp_s: float = float(os.getenv("TIMEOUT_ERP_S", "10"))
    timeout_crm_s: float = float(os.getenv("TIMEOUT_CRM_S", "10"))
    timeout_firestore_s: float = float(os.getenv("TIMEOUT_FIRESTORE_S", "10"))
    timeout_global_s: float = float(os.getenv("TIMEOUT_GLOBAL_S", "30"))
    # POST /api/v1/query: tope total por consulta (router + recuperación + anonimizador local +
    # razonamiento, incluida la caída de Claude al modelo local) y consultas simultáneas.
    # Ollama corre en un solo equipo: sin tope, una ráfaga lo satura y las demás consultas se
    # acumulan.
    query_timeout_s: float = float(os.getenv("QUERY_TIMEOUT_S", "120"))
    query_max_concurrency: int = int(os.getenv("QUERY_MAX_CONCURRENCY", "2"))
    # Límite por persona: como máximo QUERY_RATE_LIMIT consultas en QUERY_RATE_WINDOW_S
    # segundos (ventana deslizante, en memoria del proceso de la API). 0 lo desactiva.
    query_rate_limit: int = int(os.getenv("QUERY_RATE_LIMIT", "10"))
    query_rate_window_s: float = float(os.getenv("QUERY_RATE_WINDOW_S", "60"))
    # Cuántos identificadores de fragmento se guardan por consulta en el registro de auditoría
    # (el total siempre queda).
    audit_max_chunks: int = int(os.getenv("AUDIT_MAX_CHUNKS", "20"))

    # ── API / acceso ─────────────────────────────────────────────────────
    # Secreto para firmar sesiones. OBLIGATORIO (>= 32 caracteres) en producción.
    api_session_secret: str = os.getenv("API_SESSION_SECRET", "")
    # Clave Fernet (base64 urlsafe de 32 bytes) que cifra en reposo los secretos TOTP.
    # OBLIGATORIA en producción. Generar: python -m pipeline_ai.security.manage gen-key
    api_totp_key: str = os.getenv("API_TOTP_KEY", "")

    # ── Autenticación ───────────────────────────────────────────────────
    # Motor de la base de usuarios, áreas, sesiones y registro de cambios:
    #   postgres -> el de los despliegues con Docker (el compose lo fija).
    #   sqlite   -> pruebas y desarrollo sin Docker (archivo AUTH_DB_PATH).
    auth_backend: str = os.getenv("AUTH_BACKEND", "sqlite").strip().lower()
    # Archivo SQLite. Con Postgres, si existe y Postgres está vacío, se migra solo una vez.
    # Su carpeta es además el "directorio de datos" (volumen ./data, fuera de git).
    auth_db_path: str = os.getenv("AUTH_DB_PATH", "/app/data/auth.db")
    # Postgres: servicio interno del compose, sin puerto publicado.
    postgres_host: str = os.getenv("POSTGRES_HOST", "postgres")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db: str = os.getenv("POSTGRES_DB", "mhia")
    postgres_user: str = os.getenv("POSTGRES_USER", "mhia")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    # Esquema de Postgres (las pruebas usan uno desechable por caso).
    postgres_schema: str = os.getenv("POSTGRES_SCHEMA", "public")
    # Sesión: caduca por inactividad y, pase lo que pase, por antigüedad absoluta.
    session_idle_s: int = int(os.getenv("SESSION_IDLE_S", "1800"))
    session_absolute_s: int = int(os.getenv("SESSION_ABSOLUTE_S", "43200"))
    # Fuerza bruta: tras N fallos seguidos de un usuario se bloquea su login `lock_s` segundos.
    login_max_failures: int = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
    login_lock_s: int = int(os.getenv("LOGIN_LOCK_S", "900"))
    # Segundo factor obligatorio para quien tenga alguna área con este nivel o más
    # (INTERNAL, CONFIDENTIAL o RESTRICTED). control_total lo exige siempre.
    mfa_required_level: str = os.getenv("MFA_REQUIRED_LEVEL", "CONFIDENTIAL").strip().upper()
    # Ventana en la que la persona puede activar su segundo factor desde la GUI. La abre el
    # administrador (create-user, reset-password, set-areas, allow-totp-enroll...): fuera de
    # ella, una contraseña robada no basta para registrar otro teléfono.
    totp_enroll_window_s: int = int(os.getenv("TOTP_ENROLL_WINDOW_S", "259200"))
    # Minutos para escanear el QR y confirmar el primer código tras la contraseña.
    totp_enroll_ttl_s: int = int(os.getenv("TOTP_ENROLL_TTL_S", "600"))
    # Cookie `Secure`: obligatoria en producción (HTTPS del tailnet). En desarrollo local
    # (http://localhost) se puede apagar con API_COOKIE_SECURE=false.
    cookie_secure: bool = _env_bool("API_COOKIE_SECURE", os.getenv("MHIA_ENV") == "production")
    # Hosts que la API acepta en la cabecera Host (anti Host-header attacks).
    # Incluye el nombre del tailnet en producción (ver .env.example).
    api_allowed_hosts: tuple[str, ...] = _env_list(
        "API_ALLOWED_HOSTS", "localhost,127.0.0.1,mhia-api"
    )

    # Dominios de datos que SIEMPRE se consideran confidenciales.
    # Originalmente: ventas, inventario (Subasta, Consignación, etc.) y
    # proveedores. Se agregó "administracion" (datos financieros/contables)
    # tras definir la estructura real de carpetas de Drive por departamento
    # (carpeta "Administracion" con contenido financiero sensible).
    # La fuente de verdad futura es privacy.sensitivity.DOMAIN_SENSITIVITY.
    confidential_domains: tuple[str, ...] = (
        "ventas",
        "inventario",
        "proveedores",
        "administracion",
        "proyectos",
    )

    def require(self, *fields: str) -> None:
        """Valida en el punto de uso (no al importar) que ciertas variables
        obligatorias estén presentes. Permite importar pipeline_ai.config y construir
        el grafo en tests/CI sin credenciales reales, y falla con un mensaje
        claro solo cuando el nodo que sí las necesita se ejecuta de verdad."""
        missing = [f for f in fields if not getattr(self, f, "")]
        if missing:
            raise RuntimeError(
                f"Faltan variables de entorno requeridas: {', '.join(missing)}. "
                f"Revisa tu archivo .env (ver .env.example)."
            )

    def require_reasoning_mode(self) -> str:
        """Valida REASONING_MODE (se llama al arrancar la API y en cada razonamiento). Un valor
        desconocido es un error de configuración: no se adivina el proveedor."""
        if self.reasoning_mode not in REASONING_MODES:
            raise RuntimeError(
                f"REASONING_MODE debe ser {' o '.join(REASONING_MODES)} "
                f"(valor actual: {self.reasoning_mode!r})."
            )
        if self.reasoning_mode == "claude":
            self.require("anthropic_api_key")
        return self.reasoning_mode

    def require_mfa_level(self) -> str:
        """Valida MFA_REQUIRED_LEVEL al arrancar: un valor desconocido no se adivina."""
        if self.mfa_required_level not in MFA_LEVELS:
            raise RuntimeError(
                f"MFA_REQUIRED_LEVEL debe ser {', '.join(MFA_LEVELS)} "
                f"(valor actual: {self.mfa_required_level!r})."
            )
        return self.mfa_required_level

    def require_production_secrets(self) -> None:
        """Se llama al ARRANCAR la API (no al importar). En producción falla
        explícitamente si falta un secreto crítico o si DEBUG está activo."""
        if self.environment != "production":
            return
        if self.debug:
            raise RuntimeError("MHIA_DEBUG debe ser false cuando MHIA_ENV=production.")
        self.require("api_session_secret", "api_totp_key")
        self.require_reasoning_mode()  # con claude exige además ANTHROPIC_API_KEY
        self.require_mfa_level()
        if len(self.api_session_secret) < 32:
            raise RuntimeError("API_SESSION_SECRET debe tener al menos 32 caracteres.")
        if not self.cookie_secure:
            raise RuntimeError("API_COOKIE_SECURE debe ser true cuando MHIA_ENV=production.")
        if self.auth_backend != "postgres":
            raise RuntimeError("AUTH_BACKEND debe ser postgres cuando MHIA_ENV=production.")
        if len(self.postgres_password) < 24:
            raise RuntimeError("POSTGRES_PASSWORD debe tener al menos 24 caracteres.")
        try:
            from cryptography.fernet import Fernet

            Fernet(self.api_totp_key.encode())
        except Exception as exc:  # noqa: BLE001 - mensaje claro sin eco de la clave
            raise RuntimeError(
                "API_TOTP_KEY no es una clave Fernet válida "
                "(genera una con: python -m pipeline_ai.security.manage gen-key)."
            ) from exc


settings = Settings()
