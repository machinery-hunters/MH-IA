"""
Almacén de autenticación (SQLite): usuarios, membresías por área, sesiones y contador de
intentos fallidos.

SQLite basta aquí: un solo servidor, pocos usuarios, sin infraestructura extra. El archivo
vive en el volumen ./data (fuera de git), con permisos 0600 donde el sistema los soporta.

Reglas:
- Las sesiones se guardan SOLO como HMAC del token (clave: API_SESSION_SECRET). Quien lea
  auth.db no puede reconstruir una cookie válida.
- Todo SQL es parametrizado.
- El estado de fuerza bruta se indexa por hash del nombre de usuario, existente o no, para que
  el bloqueo no revele qué usuarios existen.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pipeline_ai.config import settings
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area, from_state

# Versión del esquema (PRAGMA user_version). 1: membresías por área.
SCHEMA_VERSION = 1

# Migración 0 -> 1: cada usuario existente recibe las áreas equivalentes a lo que su rol
# podía ver con el modelo anterior (por dominio), para no cambiar su acceso de golpe.
# Excepción deliberada (plan maestro §2.5.1): control_total ya no lee datos por su rol.
_C = Sensitivity.CONFIDENTIAL
_LEGACY_CONFIDENTIAL = {
    Area.GENERAL: Sensitivity.INTERNAL,
    Area.COMERCIAL: _C,
    Area.INVENTARIO: _C,
    Area.SOURCING: _C,
    Area.FINANZAS: _C,
    Area.PROYECTOS: _C,
}
LEGACY_ROLE_MEMBERSHIPS: dict[str, dict[Area, Sensitivity]] = {
    "consulta": {Area.GENERAL: Sensitivity.INTERNAL},
    "lider": _LEGACY_CONFIDENTIAL,
    "configuracion": _LEGACY_CONFIDENTIAL,
    "control_total": {},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    role                TEXT NOT NULL,
    totp_secret_enc     TEXT,
    totp_enabled        INTEGER NOT NULL DEFAULT 0,
    totp_last_step      INTEGER,
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL,
    password_changed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mfa        INTEGER NOT NULL,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS memberships (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    area    TEXT NOT NULL,
    level   INTEGER NOT NULL,
    PRIMARY KEY (user_id, area)
);
CREATE TABLE IF NOT EXISTS throttle (
    key          TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL,
    locked_until REAL NOT NULL,
    updated      REAL NOT NULL
);
"""

# Cada cuánto se refresca `last_seen` (evita una escritura por petición).
_TOUCH_EVERY_S = 30
# Los fallos se "olvidan" tras este múltiplo del tiempo de bloqueo sin nuevos intentos.
_DECAY_FACTOR = 16
_MAX_BACKOFF = 8


class UserExistsError(ValueError):
    pass


@dataclass(frozen=True)
class User:
    id: str
    username: str
    password_hash: str
    role: str
    totp_secret_enc: str | None
    totp_enabled: bool
    totp_last_step: int | None
    active: bool


@dataclass(frozen=True)
class SessionInfo:
    user_id: str
    username: str
    role: str
    mfa: bool
    memberships: dict[Area, Sensitivity] = field(default_factory=dict)


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=row["role"],
        totp_secret_enc=row["totp_secret_enc"],
        totp_enabled=bool(row["totp_enabled"]),
        totp_last_step=row["totp_last_step"],
        active=bool(row["active"]),
    )


class AuthStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._migrate(conn)
        try:
            os.chmod(self.path, 0o600)  # best-effort: en Windows casi no tiene efecto
        except OSError:
            pass

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:  # commit al salir bien, rollback si hay excepción
                yield conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            users = conn.execute(
                "SELECT id, role FROM users WHERE id NOT IN (SELECT user_id FROM memberships)"
            ).fetchall()
            for user in users:
                for area, level in LEGACY_ROLE_MEMBERSHIPS.get(user["role"], {}).items():
                    conn.execute(
                        "INSERT INTO memberships (user_id, area, level) VALUES (?, ?, ?)",
                        (user["id"], area.value, int(level)),
                    )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- Usuarios ------------------------------------------------------------------------

    def create_user(self, username: str, password_hash: str, role: str, *, now: float) -> User:
        user_id = str(uuid.uuid4())
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, created_at,"
                    " password_changed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, username, password_hash, role, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise UserExistsError(username) from exc
        found = self.get_user(user_id)
        assert found is not None
        return found

    def get_user(self, user_id: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def get_user_by_username(self, username: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(r) for r in rows]

    def set_password_hash(self, user_id: str, password_hash: str, *, now: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
                (password_hash, now, user_id),
            )

    def set_role(self, user_id: str, role: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

    # -- Membresías por área --------------------------------------------------------------

    def get_memberships(self, user_id: str) -> dict[Area, Sensitivity]:
        with self._conn() as conn:
            return self._memberships(conn, user_id)

    @staticmethod
    def _memberships(conn: sqlite3.Connection, user_id: str) -> dict[Area, Sensitivity]:
        rows = conn.execute(
            "SELECT area, level FROM memberships WHERE user_id = ?", (user_id,)
        ).fetchall()
        # Filas inválidas (área o nivel desconocidos) se descartan: nunca conceden acceso.
        return from_state({row["area"]: row["level"] for row in rows})

    def set_memberships(self, user_id: str, memberships: dict[Area, Sensitivity]) -> None:
        """Reemplaza TODAS las membresías del usuario en una sola transacción."""
        with self._conn() as conn:
            conn.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
            for area, level in memberships.items():
                conn.execute(
                    "INSERT INTO memberships (user_id, area, level) VALUES (?, ?, ?)",
                    (user_id, Area(area).value, int(Sensitivity(level))),
                )

    def set_active(self, user_id: str, active: bool) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))

    # -- TOTP ----------------------------------------------------------------------------

    def set_totp_secret(self, user_id: str, secret_enc: str) -> None:
        """Guarda un secreto nuevo SIN activarlo (se activa al confirmar un código)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET totp_secret_enc = ?, totp_enabled = 0, totp_last_step = NULL"
                " WHERE id = ?",
                (secret_enc, user_id),
            )

    def enable_totp(self, user_id: str, step: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET totp_enabled = 1, totp_last_step = ? WHERE id = ?",
                (step, user_id),
            )

    def consume_totp_step(self, user_id: str, step: int) -> bool:
        """Marca el paso como usado de forma atómica. False si ya estaba usado (repetición)."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET totp_last_step = ? WHERE id = ?"
                " AND (totp_last_step IS NULL OR totp_last_step < ?)",
                (step, user_id, step),
            )
            return cur.rowcount == 1

    # -- Fuerza bruta --------------------------------------------------------------------

    def locked_for(self, key: str, *, now: float) -> float:
        """Segundos restantes de bloqueo (0 si no está bloqueado)."""
        with self._conn() as conn:
            row = conn.execute("SELECT locked_until FROM throttle WHERE key = ?", (key,)).fetchone()
        if row is None:
            return 0.0
        return max(0.0, row["locked_until"] - now)

    def register_failure(self, key: str, *, now: float, max_failures: int, lock_s: int) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT failures, updated FROM throttle WHERE key = ?", (key,)
            ).fetchone()
            failures = 1
            if row is not None and now - row["updated"] <= lock_s * _DECAY_FACTOR:
                failures = row["failures"] + 1
            locked_until = 0.0
            if failures >= max_failures:
                backoff = min(2 ** (failures - max_failures), _MAX_BACKOFF)
                locked_until = now + lock_s * backoff
            conn.execute(
                "INSERT INTO throttle (key, failures, locked_until, updated) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET failures = excluded.failures,"
                " locked_until = excluded.locked_until, updated = excluded.updated",
                (key, failures, locked_until, now),
            )
            # Limpieza oportunista de claves viejas (los nombres inexistentes también escriben).
            conn.execute(
                "DELETE FROM throttle WHERE updated < ?", (now - lock_s * _DECAY_FACTOR * 2,)
            )

    def clear_failures(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM throttle WHERE key = ?", (key,))

    # -- Sesiones ------------------------------------------------------------------------

    def create_session(
        self, token_hash: str, user_id: str, *, mfa: bool, now: float, absolute_s: int
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, mfa, created_at, last_seen,"
                " expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, user_id, int(mfa), now, now, now + absolute_s),
            )
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    def lookup_session(self, token_hash: str, *, now: float, idle_s: int) -> SessionInfo | None:
        """Sesión vigente o None. Borra la sesión si caducó (absoluta o por inactividad) o si
        el usuario fue desactivado. El rol y las membresías se leen de la base EN CADA
        petición: un cambio de permisos rige desde la siguiente consulta."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT s.user_id, s.mfa, s.last_seen, s.expires_at, u.username, u.role, u.active"
                " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now or now - row["last_seen"] > idle_s or not row["active"]:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            if now - row["last_seen"] > _TOUCH_EVERY_S:
                conn.execute(
                    "UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash)
                )
            return SessionInfo(
                user_id=row["user_id"],
                username=row["username"],
                role=row["role"],
                mfa=bool(row["mfa"]),
                memberships=self._memberships(conn, row["user_id"]),
            )

    def delete_session(self, token_hash: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: str) -> int:
        with self._conn() as conn:
            return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount

    def delete_all_sessions(self) -> int:
        with self._conn() as conn:
            return conn.execute("DELETE FROM sessions").rowcount


_stores: dict[str, AuthStore] = {}


def get_store() -> AuthStore:
    """Una instancia por ruta de base de datos (settings.auth_db_path se lee en cada llamada)."""
    path = str(settings.auth_db_path)
    store = _stores.get(path)
    if store is None:
        store = _stores[path] = AuthStore(path)
    return store
