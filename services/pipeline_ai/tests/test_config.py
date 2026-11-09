import pytest
from cryptography.fernet import Fernet

from pipeline_ai.config import settings


def test_confidential_domains_cover_the_critical_categories():
    # Originalmente ventas/inventario/proveedores; se agregó "administracion"
    # (datos financieros/contables) al definir la estructura real de
    # carpetas de Drive por departamento — ver pipeline_ai/config.py.
    assert set(settings.confidential_domains) == {
        "ventas",
        "inventario",
        "proveedores",
        "administracion",
        "proyectos",
    }


def test_settings_require_raises_with_clear_message_when_missing():
    import pytest

    original = settings.anthropic_api_key
    try:
        settings.anthropic_api_key = ""
        with pytest.raises(RuntimeError, match="anthropic_api_key"):
            settings.require("anthropic_api_key")
    finally:
        settings.anthropic_api_key = original


def test_settings_require_passes_when_present():
    original = settings.anthropic_api_key
    try:
        settings.anthropic_api_key = "sk-ant-fake-for-test"
        settings.require("anthropic_api_key")  # no debe lanzar
    finally:
        settings.anthropic_api_key = original


def _prod(monkeypatch, **overrides):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-fake")
    monkeypatch.setattr(settings, "api_session_secret", "x" * 32)
    monkeypatch.setattr(settings, "api_totp_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "auth_backend", "postgres")
    monkeypatch.setattr(settings, "postgres_password", "p" * 24)
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)


def test_debug_is_off_by_default():
    assert settings.debug is False


def test_production_guard_passes_with_all_secrets(monkeypatch):
    _prod(monkeypatch)
    settings.require_production_secrets()  # no debe lanzar


def test_production_guard_fails_without_session_secret(monkeypatch):
    import pytest

    _prod(monkeypatch, api_session_secret="")
    with pytest.raises(RuntimeError, match="api_session_secret"):
        settings.require_production_secrets()


def test_production_guard_rejects_short_session_secret(monkeypatch):
    import pytest

    _prod(monkeypatch, api_session_secret="corto")
    with pytest.raises(RuntimeError, match="32"):
        settings.require_production_secrets()


def test_production_guard_rejects_debug(monkeypatch):
    import pytest

    _prod(monkeypatch, debug=True)
    with pytest.raises(RuntimeError, match="DEBUG"):
        settings.require_production_secrets()


def test_production_guard_fails_without_totp_key(monkeypatch):
    _prod(monkeypatch, api_totp_key="")
    with pytest.raises(RuntimeError, match="api_totp_key"):
        settings.require_production_secrets()


def test_production_guard_rejects_an_invalid_totp_key_without_echoing_it(monkeypatch):
    _prod(monkeypatch, api_totp_key="no-es-una-clave-fernet")
    with pytest.raises(RuntimeError, match="Fernet") as excinfo:
        settings.require_production_secrets()
    assert "no-es-una-clave-fernet" not in str(excinfo.value)


def test_production_guard_requires_secure_cookies(monkeypatch):
    _prod(monkeypatch, cookie_secure=False)
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        settings.require_production_secrets()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth_backend": "sqlite"}, "AUTH_BACKEND"),
        ({"postgres_password": "corta"}, "POSTGRES_PASSWORD"),
        ({"postgres_password": ""}, "POSTGRES_PASSWORD"),
    ],
)
def test_production_guard_requires_postgres_with_a_strong_password(monkeypatch, overrides, message):
    _prod(monkeypatch, **overrides)

    with pytest.raises(RuntimeError, match=message) as exc:
        settings.require_production_secrets()
    assert "corta" not in str(exc.value)


def test_production_guard_is_noop_in_development(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "api_session_secret", "")
    settings.require_production_secrets()  # no debe lanzar
