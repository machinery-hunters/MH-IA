from app.config import settings


def test_confidential_domains_cover_the_three_critical_categories():
    assert set(settings.confidential_domains) == {"ventas", "inventario", "proveedores"}


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
