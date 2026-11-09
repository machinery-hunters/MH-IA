"""
Almacén de autenticación: usuarios, membresías por área, sesiones, intentos fallidos y el
registro de cambios de permisos.

Dos motores con el MISMO comportamiento (las pruebas corren contra ambos):
  - Postgres (`AUTH_BACKEND=postgres`): el de los despliegues con Docker (laptop y Mac
    mini). Servicio `postgres` del compose, sin puerto publicado, solo en la red interna.
  - SQLite (`AUTH_BACKEND=sqlite`, por defecto fuera de Docker): pruebas y desarrollo sin
    infraestructura. Archivo en ./data, permisos 0600 donde el sistema los soporta.

Al pasar a Postgres, si su base está vacía y existe el auth.db anterior, se migra solo
(usuarios, áreas y registro; las sesiones no: todos vuelven a iniciar sesión) y el archivo
se renombra para que no se vuelva a importar.

Reglas:
- Las sesiones se guardan SOLO como HMAC del token (clave: API_SESSION_SECRET). Quien lea
  la base no puede reconstruir una cookie válida.
- Todo SQL es parametrizado.
- El estado de fuerza bruta se indexa por hash del nombre de usuario, existente o no, para que
  el bloqueo no revele qué usuarios existen.
- `admin_audit` es de solo inserción: un disparador rechaza UPDATE y DELETE (protege contra
  errores, no contra quien administra la base; ver SECURITY.md).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from pipeline_ai.config import settings
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area, from_state

log = logging.getLogger("mhia.auth_store")

# Errores de base de datos de cualquiera de los dos motores (la API responde 503 genérico).
DB_ERRORS: tuple[type[Exception], ...] = (sqlite3.Error, psycopg.Error)

# Versión del esquema. 1: membresías por área. 2: registro de cambios de permisos.
SCHEMA_VERSION = 2
EXPORT_FORMAT = 1

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

# Columnas que viajan en un respaldo / migración (sesiones e intentos NO: son efímeros).
_EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": (
        "id",
        "username",
        "password_hash",
        "role",
        "totp_secret_enc",
        "totp_enabled",
        "totp_last_step",
        "active",
        "created_at",
        "password_changed_at",
    ),
    "memberships": ("user_id", "area", "level"),
    "admin_audit": ("id", "at", "actor", "action", "target", "detail"),
}

_SQLITE_SCHEMA = """
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

_SQLITE_AUDIT_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS admin_audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    at     REAL NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
)""",
    """CREATE TRIGGER IF NOT EXISTS admin_audit_no_update BEFORE UPDATE ON admin_audit
BEGIN SELECT RAISE(ABORT, 'admin_audit es de solo insercion'); END""",
    """CREATE TRIGGER IF NOT EXISTS admin_audit_no_delete BEFORE DELETE ON admin_audit
BEGIN SELECT RAISE(ABORT, 'admin_audit es de solo insercion'); END""",
)

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    role                TEXT NOT NULL,
    totp_secret_enc     TEXT,
    totp_enabled        INTEGER NOT NULL DEFAULT 0,
    totp_last_step      BIGINT,
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          DOUBLE PRECISION NOT NULL,
    password_changed_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mfa        INTEGER NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    last_seen  DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
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
    locked_until DOUBLE PRECISION NOT NULL,
    updated      DOUBLE PRECISION NOT NULL
);
"""

_PG_AUDIT = """
CREATE TABLE IF NOT EXISTS admin_audit (
    id     BIGSERIAL PRIMARY KEY,
    at     DOUBLE PRECISION NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE OR REPLACE FUNCTION mhia_admin_audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'admin_audit es de solo insercion';
END;
$$;
DROP TRIGGER IF EXISTS admin_audit_no_change ON admin_audit;
CREATE TRIGGER admin_audit_no_change BEFORE UPDATE OR DELETE ON admin_audit
    FOR EACH ROW EXECUTE FUNCTION mhia_admin_audit_append_only();
DROP TRIGGER IF EXISTS admin_audit_no_truncate ON admin_audit;
CREATE TRIGGER admin_audit_no_truncate BEFORE TRUNCATE ON admin_audit
    FOR EACH STATEMENT EXECUTE FUNCTION mhia_admin_audit_append_only();
"""

# Cada cuánto se refresca `last_seen` (evita una escritura por petición).
_TOUCH_EVERY_S = 30
# Los fallos se "olvidan" tras este múltiplo del tiempo de bloqueo sin nuevos intentos.
_DECAY_FACTOR = 16
_MAX_BACKOFF = 8
# Candado de Postgres para crear/migrar el esquema una sola vez aunque arranquen varios
# procesos a la vez (API y CLI).
_PG_LOCK_ID = 0x4D48_4941  # "MHIA"


class UserExistsError(ValueError):
    pass


class ImportDataError(ValueError):
    """Datos de un respaldo o migración con forma inesperada."""


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


@dataclass(frozen=True)
class AdminEvent:
    id: int
    at: float
    actor: str
    action: str
    target: str | None
    detail: dict[str, Any]


def _user(row: Mapping[str, Any]) -> User:
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


class _PgConn:
    """Adaptador mínimo: el SQL compartido usa `?`; psycopg usa `%s`."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple | list = ()) -> psycopg.Cursor:
        return self._conn.execute(sql.replace("?", "%s"), params)


class AuthStore:
    """Comportamiento común. Los motores solo aportan conexión, esquema y versión."""

    backend = "base"
    _integrity_errors: tuple[type[Exception], ...] = ()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Conexión en una transacción (commit al salir bien, rollback si falla)."""
        raise NotImplementedError
        yield  # pragma: no cover

    def close(self) -> None:
        """Libera conexiones abiertas (Postgres)."""

    def _reset_audit(self, conn: Any) -> None:
        raise NotImplementedError

    def _after_audit_import(self, conn: Any) -> None:
        """Ajustes tras insertar ids explícitos en admin_audit (secuencia en Postgres)."""

    # -- Usuarios ------------------------------------------------------------------------

    def create_user(self, username: str, password_hash: str, role: str, *, now: float) -> User:
        user_id = str(uuid.uuid4())
        try:
            with self.connection() as conn:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, created_at,"
                    " password_changed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, username, password_hash, role, now, now),
                )
        except self._integrity_errors as exc:
            raise UserExistsError(username) from exc
        found = self.get_user(user_id)
        assert found is not None
        return found

    def get_user(self, user_id: str) -> User | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user(row) if row else None

    def get_user_by_username(self, username: str) -> User | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(r) for r in rows]

    def set_password_hash(self, user_id: str, password_hash: str, *, now: float) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
                (password_hash, now, user_id),
            )

    def set_role(self, user_id: str, role: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

    def set_active(self, user_id: str, active: bool) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))

    # -- Membresías por área --------------------------------------------------------------

    def get_memberships(self, user_id: str) -> dict[Area, Sensitivity]:
        with self.connection() as conn:
            return self._memberships(conn, user_id)

    @staticmethod
    def _memberships(conn: Any, user_id: str) -> dict[Area, Sensitivity]:
        rows = conn.execute(
            "SELECT area, level FROM memberships WHERE user_id = ?", (user_id,)
        ).fetchall()
        # Filas inválidas (área o nivel desconocidos) se descartan: nunca conceden acceso.
        return from_state({row["area"]: row["level"] for row in rows})

    def set_memberships(self, user_id: str, memberships: Mapping[Area, Sensitivity]) -> None:
        """Reemplaza TODAS las membresías del usuario en una sola transacción."""
        with self.connection() as conn:
            conn.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
            for area, level in memberships.items():
                conn.execute(
                    "INSERT INTO memberships (user_id, area, level) VALUES (?, ?, ?)",
                    (user_id, Area(area).value, int(Sensitivity(level))),
                )

    # -- TOTP ----------------------------------------------------------------------------

    def set_totp_secret(self, user_id: str, secret_enc: str) -> None:
        """Guarda un secreto nuevo SIN activarlo (se activa al confirmar un código)."""
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET totp_secret_enc = ?, totp_enabled = 0, totp_last_step = NULL"
                " WHERE id = ?",
                (secret_enc, user_id),
            )

    def enable_totp(self, user_id: str, step: int) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET totp_enabled = 1, totp_last_step = ? WHERE id = ?",
                (step, user_id),
            )

    def consume_totp_step(self, user_id: str, step: int) -> bool:
        """Marca el paso como usado de forma atómica. False si ya estaba usado (repetición)."""
        with self.connection() as conn:
            cur = conn.execute(
                "UPDATE users SET totp_last_step = ? WHERE id = ?"
                " AND (totp_last_step IS NULL OR totp_last_step < ?)",
                (step, user_id, step),
            )
            return cur.rowcount == 1

    # -- Fuerza bruta --------------------------------------------------------------------

    def locked_for(self, key: str, *, now: float) -> float:
        """Segundos restantes de bloqueo (0 si no está bloqueado)."""
        with self.connection() as conn:
            row = conn.execute("SELECT locked_until FROM throttle WHERE key = ?", (key,)).fetchone()
        if row is None:
            return 0.0
        return max(0.0, row["locked_until"] - now)

    def register_failure(self, key: str, *, now: float, max_failures: int, lock_s: int) -> None:
        with self.connection() as conn:
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
        with self.connection() as conn:
            conn.execute("DELETE FROM throttle WHERE key = ?", (key,))

    # -- Sesiones ------------------------------------------------------------------------

    def create_session(
        self, token_hash: str, user_id: str, *, mfa: bool, now: float, absolute_s: int
    ) -> None:
        with self.connection() as conn:
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
        with self.connection() as conn:
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
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: str) -> int:
        with self.connection() as conn:
            return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount

    def delete_all_sessions(self) -> int:
        with self.connection() as conn:
            return conn.execute("DELETE FROM sessions").rowcount

    # -- Registro de cambios de permisos ---------------------------------------------------

    def record_admin_event(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        detail: Mapping[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> None:
        """Deja constancia de un cambio de usuarios o permisos. Nunca guarda contraseñas,
        secretos ni datos del negocio: solo quién, qué, a quién y el antes/después."""
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO admin_audit (at, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
                (
                    time.time() if now is None else now,
                    actor,
                    action,
                    target,
                    json.dumps(dict(detail or {}), ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_admin_events(self, limit: int = 50) -> list[AdminEvent]:
        """Los `limit` eventos más recientes, del más nuevo al más viejo."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id, at, actor, action, target, detail FROM admin_audit"
                " ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        events = []
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except ValueError:
                detail = {"_ilegible": True}
            events.append(
                AdminEvent(
                    id=int(row["id"]),
                    at=float(row["at"]),
                    actor=row["actor"],
                    action=row["action"],
                    target=row["target"],
                    detail=detail if isinstance(detail, dict) else {"valor": detail},
                )
            )
        return events

    # -- Exportar / importar (respaldo y migración) ----------------------------------------

    def export_data(self) -> dict[str, Any]:
        """Usuarios, áreas y registro en un dict serializable a JSON. Sin sesiones ni
        intentos fallidos: tras restaurar, todos vuelven a iniciar sesión."""
        data: dict[str, Any] = {"format": EXPORT_FORMAT, "schema_version": SCHEMA_VERSION}
        with self.connection() as conn:
            for table, columns in _EXPORT_COLUMNS.items():
                order = "id" if table != "memberships" else "user_id, area"
                rows = conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}"  # noqa: S608
                ).fetchall()
                data[table] = [{c: row[c] for c in columns} for row in rows]
        return data

    def import_data(self, data: Mapping[str, Any], *, replace: bool) -> dict[str, int]:
        """Carga lo que `export_data` produjo. Con replace=True borra antes TODO (incluidas
        sesiones y el registro); sin replace exige una base sin usuarios."""
        with self.connection() as conn:
            return self._import_into(conn, data, replace=replace)

    def _import_into(self, conn: Any, data: Mapping[str, Any], *, replace: bool) -> dict[str, int]:
        if not isinstance(data, Mapping) or data.get("format") != EXPORT_FORMAT:
            raise ImportDataError("formato de exportación no soportado")
        tables: dict[str, list[dict[str, Any]]] = {}
        for table, columns in _EXPORT_COLUMNS.items():
            rows = data.get(table, [])
            if not isinstance(rows, list) or not all(
                isinstance(r, Mapping) and set(r) == set(columns) for r in rows
            ):
                raise ImportDataError(f"la tabla {table} no tiene las columnas esperadas")
            tables[table] = [dict(r) for r in rows]
        if replace:
            for table in ("sessions", "throttle", "memberships", "users"):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - nombres fijos
            self._reset_audit(conn)
        else:
            existing = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            if existing:
                raise ImportDataError("la base de destino ya tiene usuarios")
        for table, columns in _EXPORT_COLUMNS.items():
            placeholders = ", ".join("?" for _ in columns)
            for row in tables[table]:
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
                    tuple(row[c] for c in columns),
                )
        if tables["admin_audit"]:
            self._after_audit_import(conn)
        return {table: len(rows) for table, rows in tables.items()}

    def counts(self) -> dict[str, int]:
        with self.connection() as conn:
            return {
                table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[
                    "n"
                ]  # noqa: S608
                for table in _EXPORT_COLUMNS
            }


class SqliteAuthStore(AuthStore):
    backend = "sqlite"
    _integrity_errors = (sqlite3.IntegrityError,)

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SQLITE_SCHEMA)
            self._migrate(conn)
        try:
            os.chmod(self.path, 0o600)  # best-effort: en Windows casi no tiene efecto
        except OSError:
            pass

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
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
        if version < 2:
            for statement in _SQLITE_AUDIT_STATEMENTS:
                conn.execute(statement)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _reset_audit(self, conn: sqlite3.Connection) -> None:
        # Los disparadores impiden borrar filas: se rehace la tabla (solo en restauración).
        conn.execute("DROP TABLE IF EXISTS admin_audit")
        for statement in _SQLITE_AUDIT_STATEMENTS:
            conn.execute(statement)


class PostgresAuthStore(AuthStore):
    backend = "postgres"
    _integrity_errors = (psycopg.errors.UniqueViolation,)

    def __init__(
        self,
        *,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
        schema: str = "public",
        legacy_sqlite: Path | None = None,
        connect_timeout: int = 5,
    ) -> None:
        self.schema = schema
        kwargs: dict[str, Any] = {
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "password": password,
            "connect_timeout": connect_timeout,
            "application_name": "mhia-auth",
            "row_factory": dict_row,
        }
        if schema != "public":
            kwargs["options"] = f"-c search_path={schema}"
        # check: valida cada conexión al sacarla del pool (tras reiniciar Postgres, la API se
        # recupera sola en vez de fallar con una conexión muerta).
        self._pool = ConnectionPool(
            kwargs=kwargs,
            min_size=1,
            max_size=4,
            timeout=connect_timeout,
            check=ConnectionPool.check_connection,
            open=False,
        )
        self._pool.open(wait=True, timeout=connect_timeout)
        try:
            self._init_schema(legacy_sqlite)
        except BaseException:
            self._pool.close()
            raise

    @contextmanager
    def connection(self) -> Iterator[_PgConn]:
        with self._pool.connection() as conn:  # commit al salir bien, rollback si falla
            yield _PgConn(conn)

    def close(self) -> None:
        self._pool.close()

    def _init_schema(self, legacy_sqlite: Path | None) -> None:
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_PG_LOCK_ID,))
            conn.execute(_PG_SCHEMA)
            row = conn.execute("SELECT version FROM schema_meta").fetchone()
            version = row["version"] if row else 0
            if version < 2:
                conn.execute(_PG_AUDIT)
            if row is None:
                conn.execute("INSERT INTO schema_meta (version) VALUES (%s)", (SCHEMA_VERSION,))
            elif version < SCHEMA_VERSION:
                conn.execute("UPDATE schema_meta SET version = %s", (SCHEMA_VERSION,))
            migrated = self._import_legacy_sqlite(_PgConn(conn), legacy_sqlite)
        if migrated is not None:
            self._archive_legacy(legacy_sqlite)  # type: ignore[arg-type]

    def _import_legacy_sqlite(self, conn: _PgConn, legacy: Path | None) -> dict[str, int] | None:
        """Migración única desde el auth.db anterior, en la misma transacción y bajo el
        candado: si dos procesos arrancan a la vez, solo uno importa."""
        if legacy is None or not legacy.is_file():
            return None
        if conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
            log.warning(
                "Existe %s pero Postgres ya tiene usuarios: no se importa (revísalo a mano).",
                legacy.name,
            )
            return None
        source = SqliteAuthStore(legacy)  # aplica antes sus propias migraciones
        counts = self._import_into(conn, source.export_data(), replace=False)
        conn.execute(
            "INSERT INTO admin_audit (at, actor, action, target, detail) VALUES (?, ?, ?, ?, ?)",
            (
                time.time(),
                "sistema",
                "migracion_desde_sqlite",
                None,
                json.dumps(counts, sort_keys=True),
            ),
        )
        log.info("Migrado %s a Postgres: %s", legacy.name, counts)
        return counts

    @staticmethod
    def _archive_legacy(legacy: Path) -> None:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        try:
            legacy.rename(legacy.with_name(f"{legacy.name}.migrado-{stamp}"))
            for suffix in ("-wal", "-shm"):
                extra = legacy.with_name(legacy.name + suffix)
                if extra.exists():
                    extra.unlink()
        except OSError as exc:
            # Ya está en Postgres; con usuarios allí no se vuelve a importar.
            log.warning("No se pudo renombrar %s tras migrarlo: %s", legacy.name, exc)

    def _reset_audit(self, conn: _PgConn) -> None:
        # Los disparadores impiden DELETE y TRUNCATE: se rehace la tabla (solo en restauración).
        conn.execute("DROP TABLE IF EXISTS admin_audit")
        conn._conn.execute(_PG_AUDIT)

    def _after_audit_import(self, conn: _PgConn) -> None:
        conn.execute(
            "SELECT setval(pg_get_serial_sequence('admin_audit', 'id'),"
            " (SELECT MAX(id) FROM admin_audit))"
        )


_stores: dict[tuple, AuthStore] = {}


def _store_key() -> tuple:
    if settings.auth_backend == "postgres":
        return (
            "postgres",
            settings.postgres_host,
            settings.postgres_port,
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_schema,
        )
    return ("sqlite", str(settings.auth_db_path))


def get_store() -> AuthStore:
    """Una instancia por configuración (los ajustes se leen en cada llamada)."""
    key = _store_key()
    store = _stores.get(key)
    if store is None:
        if key[0] == "postgres":
            settings.require("postgres_password")
            store = PostgresAuthStore(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password,
                schema=settings.postgres_schema,
                legacy_sqlite=Path(settings.auth_db_path),
            )
        else:
            store = SqliteAuthStore(settings.auth_db_path)
        _stores[key] = store
    return store


def close_store() -> None:
    """Cierra y olvida el almacén de la configuración actual (pruebas, apagado)."""
    store = _stores.pop(_store_key(), None)
    if store is not None:
        store.close()
