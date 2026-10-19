"""Fixtures compartidas de las pruebas de autenticación."""

from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from pipeline_ai.config import settings
from pipeline_ai.security import auth_store, clock, passwords, secretbox

PASSWORD = "correct-horse-battery"


class FakeClock:
    """Reloj controlable: `clock.now()` devuelve `t`; `advance()` viaja en el tiempo."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Base SQLite temporal, secretos de prueba, argon2 barato y reloj falso."""
    monkeypatch.setattr(settings, "auth_db_path", str(tmp_path / "auth.db"))
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
    yield SimpleNamespace(clock=fake_clock, store=store)
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
