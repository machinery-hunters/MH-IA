"""Fixtures compartidas de las pruebas de autenticación."""

import os
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from pipeline_ai.config import settings
from pipeline_ai.security import auth_store, clock, passwords, secretbox

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _judge_off_by_default(monkeypatch):
    """Las pruebas no deben llamar a Ollama por accidente: el juez de relevancia local viene
    encendido en producción y cada prueba que lo necesita lo enciende (y lo sustituye)."""
    monkeypatch.setattr(settings, "retrieval_judge_enabled", False)


class FakeClock:
    """Reloj controlable: `clock.now()` devuelve `t`; `advance()` viaja en el tiempo."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# Postgres de pruebas (en CI, un contenedor de servicio). Si está, cada prueba que usa la base
# corre contra los DOS motores; si no, solo contra SQLite. En CI es obligatorio: así la
# cobertura de Postgres no puede desaparecer en silencio.
PG_TEST = {
    "host": os.getenv("MHIA_TEST_POSTGRES_HOST", ""),
    "port": int(os.getenv("MHIA_TEST_POSTGRES_PORT", "5432")),
    "dbname": os.getenv("MHIA_TEST_POSTGRES_DB", "mhia"),
    "user": os.getenv("MHIA_TEST_POSTGRES_USER", "mhia"),
    "password": os.getenv("MHIA_TEST_POSTGRES_PASSWORD", ""),
}
if os.getenv("CI") and not PG_TEST["host"]:
    raise RuntimeError("En CI las pruebas exigen MHIA_TEST_POSTGRES_HOST (servicio Postgres).")
BACKENDS = ["sqlite", "postgres"] if PG_TEST["host"] else ["sqlite"]


@pytest.fixture
def pg_schema(monkeypatch):
    """Configura un esquema desechable de Postgres y lo borra al terminar."""
    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = {k: v for k, v in PG_TEST.items()}
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    monkeypatch.setattr(settings, "auth_backend", "postgres")
    monkeypatch.setattr(settings, "postgres_host", PG_TEST["host"])
    monkeypatch.setattr(settings, "postgres_port", PG_TEST["port"])
    monkeypatch.setattr(settings, "postgres_db", PG_TEST["dbname"])
    monkeypatch.setattr(settings, "postgres_user", PG_TEST["user"])
    monkeypatch.setattr(settings, "postgres_password", PG_TEST["password"])
    monkeypatch.setattr(settings, "postgres_schema", schema)
    yield schema
    auth_store.close_store()
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture(params=BACKENDS)
def auth_env(request, tmp_path, monkeypatch):
    """Base temporal (SQLite o un esquema de Postgres), secretos de prueba, argon2 barato y
    reloj falso."""
    monkeypatch.setattr(settings, "auth_db_path", str(tmp_path / "auth.db"))
    monkeypatch.setattr(settings, "auth_backend", "sqlite")
    if request.param == "postgres":
        request.getfixturevalue("pg_schema")
    monkeypatch.setattr(settings, "api_session_secret", "s" * 40)
    monkeypatch.setattr(settings, "api_totp_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "login_max_failures", 5)
    monkeypatch.setattr(settings, "login_lock_s", 900)
    monkeypatch.setattr(settings, "session_idle_s", 1800)
    monkeypatch.setattr(settings, "session_absolute_s", 43200)
    monkeypatch.setattr(
        passwords, "_HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    passwords._dummy_hash.cache_clear()
    fake_clock = FakeClock()
    monkeypatch.setattr(clock, "now", fake_clock)
    store = auth_store.get_store()
    yield SimpleNamespace(clock=fake_clock, store=store, backend=request.param)
    passwords._dummy_hash.cache_clear()


@pytest.fixture
def make_user(auth_env):
    def _make(username="ana", role="lider", password=PASSWORD, totp_secret=None, active=True):
        user = auth_env.store.create_user(
            username, passwords.hash_password(password), role, now=clock.now()
        )
        if totp_secret:
            auth_env.store.set_totp_secret(user.id, secretbox.encrypt(totp_secret))
            auth_env.store.enable_totp(user.id, 0)
        if not active:
            auth_env.store.set_active(user.id, False)
        return user

    return _make


TABLES = ("users", "sessions", "memberships", "throttle", "admin_audit")


def db_count(store, table: str) -> int:
    with store.connection() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608


def dump_db(store) -> str:
    """Todo el contenido de la base como texto, para comprobar qué se guarda en claro."""
    with store.connection() as conn:
        return "\n".join(
            repr(dict(row))
            for table in TABLES
            for row in conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
        )
