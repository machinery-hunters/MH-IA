"""
API de MH IA (FastAPI). Es la frontera entre la GUI (Next.js) y el pipeline.

/health, login/logout con sesión por cookie (api/auth_routes.py), /api/v1/me y
/api/v1/connectors con RBAC real, y /api/v1/query como 501 hasta que se construya el flujo
multi-agente.
La GUI nunca importa el pipeline: solo habla con esta API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pipeline_ai.api.auth_routes import router as auth_router
from pipeline_ai.api.deps import require_permission
from pipeline_ai.api.origin import request_origin_ok
from pipeline_ai.config import settings
from pipeline_ai.connectors.registry import get_connectors
from pipeline_ai.security.roles import Permission, Principal

# Dependencias de autorización como singletons de módulo (patrón recomendado
# por FastAPI y exigido por la regla B008 de ruff).
_CAN_QUERY = Depends(require_permission(Permission.QUERY))
_CAN_CONFIGURE = Depends(require_permission(Permission.CONFIGURE_CONNECTORS))


class QueryRequest(BaseModel):
    # Validación server-side siempre: tamaño acotado, sin confiar en la GUI.
    query: str = Field(min_length=1, max_length=2000)


def create_app(allowed_hosts: Sequence[str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Falla explícita al arrancar si falta un secreto crítico en producción.
        settings.require_production_secrets()
        yield

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
        return {
            "user_id": principal.user_id,
            "username": principal.username,
            "role": principal.role.value,
            "permissions": sorted(p.value for p in principal.permissions),
        }

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

    @app.post("/api/v1/query")
    async def query(
        body: QueryRequest,
        _: Principal = _CAN_QUERY,
    ) -> None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Consulta multi-agente pendiente (ver docs/ROADMAP.md).",
        )

    return app


app = create_app()
