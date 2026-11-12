"""
Endpoints de sesión y de la cuenta propia:

    POST   /api/v1/auth/login                  usuario + contraseña (+ código TOTP)
    POST   /api/v1/auth/totp/enroll            confirma la activación del segundo factor
    POST   /api/v1/auth/logout
    POST   /api/v1/auth/password               cambio de contraseña por la propia persona
    GET    /api/v1/auth/sessions               mis sesiones abiertas
    DELETE /api/v1/auth/sessions/{id}          cerrar una de ellas
    POST   /api/v1/auth/sessions/close-others  cerrar todas menos esta

No existe registro público: los usuarios se crean desde el servidor con
`python -m pipeline_ai.security.manage` (ver SECURITY.md).
"""

from __future__ import annotations

import base64
import io
import logging

import segno
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipeline_ai.api.deps import get_current_principal, session_cookie_name
from pipeline_ai.config import settings
from pipeline_ai.security import auth, secretbox
from pipeline_ai.security.auth_store import DB_ERRORS, SessionInfo
from pipeline_ai.security.roles import Principal, Role, principal_view

log = logging.getLogger("mhia.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_INVALID = "Credenciales inválidas."
_TOO_MANY = "Demasiados intentos. Intenta más tarde."
_SESSION_ID = r"^[0-9a-f]{16}$"
_AUTH_ERRORS = (*DB_ERRORS, auth.AuthConfigError, secretbox.SecretBoxError)


class LoginRequest(BaseModel):
    # Validación server-side: tamaños acotados, el código TOTP son exactamente 6 dígitos.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class EnrollRequest(BaseModel):
    totp_code: str = Field(pattern=r"^\d{6}$")


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class SessionView(BaseModel):
    id: str
    device: str
    created_at: float
    last_seen: float
    expires_at: float
    mfa: bool
    current: bool


def enroll_cookie_name() -> str:
    return "__Host-mhia_enroll" if settings.cookie_secure else "mhia_enroll"


def _unavailable(exc: BaseException, what: str) -> HTTPException:
    log.error("%s no disponible: %s", what, type(exc).__name__)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de autenticación no disponible."
    )


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, _TOO_MANY, headers={"Retry-After": str(retry_after)}
    )


def _device(request: Request) -> str:
    return auth.device_label(request.headers.get("user-agent"))


def _set_cookie(response: Response, name: str, token: str, max_age: int) -> None:
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _delete_cookie(response: Response, name: str) -> None:
    response.delete_cookie(
        key=name, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict"
    )


def _set_session_cookie(response: Response, token: str) -> None:
    _set_cookie(response, session_cookie_name(), token, settings.session_absolute_s)


def _me(info: SessionInfo) -> dict:
    return principal_view(
        Principal(
            user_id=info.user_id,
            role=Role(info.role),
            username=info.username,
            memberships=info.memberships,
        )
    )


def _qr_data_uri(uri: str) -> str:
    """QR del URI otpauth como SVG embebido (la CSP permite `img-src data:`). Se genera en
    el servidor: el secreto no pasa por ninguna librería del navegador ni servicio externo."""
    buffer = io.BytesIO()
    # Con xmlns: como <img> el SVG es un documento aparte y sin el espacio de nombres no se pinta.
    segno.make(uri, error="m").save(
        buffer, kind="svg", scale=5, border=2, dark="#000", light="#fff", xmldecl=False, svgns=True
    )
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> object:
    try:
        result = auth.authenticate(
            body.username, body.password, body.totp_code, device=_device(request)
        )
    except _AUTH_ERRORS as exc:
        raise _unavailable(exc, "Login") from None

    if result.status == "ok" and result.token and result.user:
        _set_session_cookie(response, result.token)
        return _me(result.user)
    if result.status == "locked":
        raise _too_many(result.retry_after)
    if result.status == "mfa_required":
        # Solo se llega aquí con la contraseña correcta: la GUI pide el código y reenvía.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Se requiere el código de verificación.", "mfa_required": True},
        )
    if result.status == "mfa_enrollment" and result.enrollment:
        # Contraseña correcta y ventana de activación abierta: la GUI muestra el QR y pide el
        # primer código. El token de la activación va en una cookie HttpOnly aparte.
        enrollment = result.enrollment
        reply = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": "Activa tu segundo factor para continuar.",
                "mfa_enrollment": {
                    "secret": enrollment.secret,
                    "otpauth_uri": enrollment.otpauth_uri,
                    "qr": _qr_data_uri(enrollment.otpauth_uri),
                    "expires_in": enrollment.expires_in,
                },
            },
        )
        _set_cookie(reply, enroll_cookie_name(), enrollment.token, enrollment.expires_in)
        return reply
    if result.status == "mfa_not_enrolled":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Tu cuenta requiere segundo factor y no tienes una activación pendiente. "
            "Pide al administrador que te habilite la activación.",
        )
    if result.status == "unavailable":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de autenticación no disponible."
        )
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, _INVALID)


@router.post("/totp/enroll")
def enroll_totp(body: EnrollRequest, request: Request, response: Response) -> object:
    token = request.cookies.get(enroll_cookie_name())
    try:
        result = auth.complete_enrollment(token, body.totp_code, device=_device(request))
    except _AUTH_ERRORS as exc:
        raise _unavailable(exc, "Activación de segundo factor") from None

    if result.status == "ok" and result.token and result.user:
        _delete_cookie(response, enroll_cookie_name())
        _set_session_cookie(response, result.token)
        return _me(result.user)
    if result.status == "locked":
        raise _too_many(result.retry_after)
    if result.status == "invalid":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "El código no coincide. Revisa que la hora de tu teléfono sea automática.",
        )
    if result.status == "unavailable":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de autenticación no disponible."
        )
    raise HTTPException(
        status.HTTP_410_GONE,
        "La activación caducó. Vuelve a iniciar sesión para empezar de nuevo.",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(session_cookie_name())
    try:
        auth.logout(token)
    except (*DB_ERRORS, auth.AuthConfigError) as exc:
        # Aun si la base falla, se borra la cookie del navegador.
        log.error("Logout con error: %s", type(exc).__name__)
    _delete_cookie(response, session_cookie_name())


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_current_principal)],
)
def change_password(body: PasswordChangeRequest, request: Request, response: Response) -> None:
    token = request.cookies.get(session_cookie_name())
    try:
        result = auth.change_password(
            token, body.current_password, body.new_password, device=_device(request)
        )
    except (*DB_ERRORS, auth.AuthConfigError) as exc:
        raise _unavailable(exc, "Cambio de contraseña") from None

    if result.status == "ok" and result.token:
        # Las demás sesiones quedaron cerradas; esta sigue con una cookie nueva.
        _set_session_cookie(response, result.token)
        return
    if result.status == "locked":
        raise _too_many(result.retry_after)
    if result.status == "invalid":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta.")
    if result.status == "policy":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, result.message)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado.")


# -- Mis sesiones -------------------------------------------------------------------------


def current_session(request: Request) -> SessionInfo:
    """La sesión de la cookie (con su id público), validada como en get_current_principal."""
    try:
        info = auth.resolve_session(request.cookies.get(session_cookie_name()))
    except (*DB_ERRORS, auth.AuthConfigError) as exc:
        raise _unavailable(exc, "Sesiones") from None
    if info is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "No autenticado.",
            headers={"WWW-Authenticate": "Session"},
        )
    return info


_CURRENT_SESSION = Depends(current_session)
_SESSION_PATH = Path(pattern=_SESSION_ID)


@router.get("/sessions", response_model=list[SessionView])
def list_sessions(info: SessionInfo = _CURRENT_SESSION) -> list[SessionView]:
    try:
        rows = auth.list_sessions(info)
    except DB_ERRORS as exc:
        raise _unavailable(exc, "Sesiones") from None
    return [
        SessionView(
            id=row.session_id,
            device=row.device or "Equipo desconocido",
            created_at=row.created_at,
            last_seen=row.last_seen,
            expires_at=row.expires_at,
            mfa=row.mfa,
            current=row.session_id == info.session_id,
        )
        for row in rows
    ]


@router.post("/sessions/close-others")
def close_other_sessions(info: SessionInfo = _CURRENT_SESSION) -> dict:
    try:
        closed = auth.close_other_sessions(info)
    except DB_ERRORS as exc:
        raise _unavailable(exc, "Sesiones") from None
    return {"closed": closed}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def close_session(
    response: Response,
    session_id: str = _SESSION_PATH,
    info: SessionInfo = _CURRENT_SESSION,
) -> None:
    try:
        closed = auth.close_session(info, session_id)
    except DB_ERRORS as exc:
        raise _unavailable(exc, "Sesiones") from None
    if not closed:
        # Igual para "no existe" y "es de otra persona": no se revela nada.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sesión no encontrada.")
    if session_id == info.session_id:
        _delete_cookie(response, session_cookie_name())
