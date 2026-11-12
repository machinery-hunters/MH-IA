"""
API de MH IA (FastAPI). Es la frontera entre la GUI (Next.js) y el pipeline.

/health, login/logout con sesión por cookie (api/auth_routes.py), /api/v1/me y
/api/v1/connectors con RBAC real, y /api/v1/query, que ejecuta el grafo de privacidad
(router -> retriever con filtro por rol -> anonimizador -> gateway -> Claude) en nombre del
usuario de la sesión.
La GUI nunca importa el pipeline: solo habla con esta API.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pipeline_ai.api.auth_routes import router as auth_router
from pipeline_ai.api.deps import require_permission
from pipeline_ai.api.origin import request_origin_ok
from pipeline_ai.api.query_service import QueryFailedError, QueryTimeoutError, run_query
from pipeline_ai.api.rate_limit import SlidingWindowLimiter
from pipeline_ai.config import settings
from pipeline_ai.connectors.registry import get_connectors
from pipeline_ai.security.auth_store import close_store, get_store
from pipeline_ai.security.roles import Permission, Principal, principal_view

log = logging.getLogger("mhia.query")

# Dependencias de autorización como singletons de módulo (patrón recomendado
# por FastAPI y exigido por la regla B008 de ruff).
_CAN_QUERY = Depends(require_permission(Permission.QUERY))
_CAN_CONFIGURE = Depends(require_permission(Permission.CONFIGURE_CONNECTORS))


class QueryRequest(BaseModel):
    # Validación server-side siempre: tamaño acotado, sin confiar en la GUI.
    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Una consulta de solo espacios no tiene sentido y gastaría una pasada del pipeline.
        stripped = value.strip()
        if not stripped:
            raise ValueError("La consulta no puede estar vacía.")
        return stripped


class QueryResponse(BaseModel):
    # answered: respuesta fundamentada (con fuentes). no_evidence: nada visible para este rol
    # o nada relevante (misma respuesta en ambos casos). blocked: el gateway de privacidad
    # no autorizó el envío; no se invocó al servicio externo.
    status: Literal["answered", "no_evidence", "blocked"]
    answer: str
    sources: list[str]
    # Quién redactó la respuesta (solo con status=answered): "local" = modelo de la red local
    # (modo sin costo, o Claude no disponible), "claude" = API de Anthropic.
    reasoner: Literal["local", "claude"] | None = None


def create_app(allowed_hosts: Sequence[str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Falla explícita al arrancar si falta un secreto crítico en producción o si
        # REASONING_MODE no es válido (con claude, sin ANTHROPIC_API_KEY).
        settings.require_reasoning_mode()
        settings.require_production_secrets()
        # Abre la base de usuarios al arrancar: crea/migra el esquema (y, con Postgres, importa
        # una sola vez el auth.db anterior). Si la base no responde, la API no arranca.
        get_store()
        yield
        close_store()

    app = FastAPI(
        title="MH IA",
        lifespan=lifespan,
        # Documentación interactiva solo con DEBUG (apagado por defecto).
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.debug else None,
    )
    hosts = list(allowed_hosts or settings.api_allowed_hosts)

    @app.middleware("http")
    async def csrf_and_cache_headers(request: Request, call_next) -> Response:
        # CSRF (2ª capa tras SameSite=Strict): el Origin de un POST debe ser un host nuestro.
        if not request_origin_ok(
            request.method,
            request.headers.get("origin"),
            request.headers.get("sec-fetch-site"),
            hosts,
        ):
            return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
        response = await call_next(request)
        # Nada de la API (sesión, datos, errores) debe quedar en cachés del navegador o proxies.
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # TrustedHost se añade DESPUÉS: queda como capa más externa y rechaza antes cualquier Host raro.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Sin detalles internos: solo vivo/no vivo.
        return {"status": "ok"}

    @app.get("/api/v1/me")
    async def me(
        principal: Principal = _CAN_QUERY,
    ) -> dict[str, object]:
        # La GUI construye su navegación a partir de esto; no hardcodea roles.
        return principal_view(principal)

    @app.get("/api/v1/connectors")
    async def list_connectors(
        _: Principal = _CAN_CONFIGURE,
    ) -> list[dict[str, object]]:
        # Estado de configuración, NUNCA valores de credenciales.
        out: list[dict[str, object]] = []
        for connector in get_connectors().values():
            spec = connector.spec
            out.append(
                {
                    "name": spec.name,
                    "display_name": spec.display_name,
                    "sensitivity": spec.default_sensitivity.name.lower(),
                    "configured": all(getattr(settings, f, "") for f in spec.required_settings),
                }
            )
        return out

    # Consultas simultáneas acotadas (Ollama corre en un solo equipo). Mínimo 1: un valor
    # inválido en el entorno no puede dejar la API sin atender consultas.
    query_slots = asyncio.Semaphore(max(1, settings.query_max_concurrency))
    query_limiter = SlidingWindowLimiter(settings.query_rate_limit, settings.query_rate_window_s)

    @app.post("/api/v1/query", response_model=QueryResponse)
    async def query(
        body: QueryRequest,
        principal: Principal = _CAN_QUERY,
    ) -> QueryResponse:
        # El rol sale de la sesión (principal), nunca del cuerpo de la petición.
        retry_after = query_limiter.acquire(principal.user_id)
        if retry_after:
            log.warning("query_rate_limited user_id=%s", principal.user_id)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Hiciste demasiadas consultas seguidas. Espera un momento.",
                headers={"Retry-After": str(retry_after)},
            )
        try:
            outcome = await run_query(principal, body.query, slots=query_slots)
        except QueryTimeoutError:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "La consulta tardó demasiado. Intenta de nuevo.",
            ) from None
        except QueryFailedError:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "No se pudo completar la consulta. Intenta de nuevo.",
            ) from None
        return QueryResponse(
            status=outcome.status,
            answer=outcome.answer,
            sources=outcome.sources,
            reasoner=outcome.reasoner,
        )

    return app


app = create_app()
