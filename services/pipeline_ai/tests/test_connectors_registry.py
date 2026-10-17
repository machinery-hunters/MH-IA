import pytest

from pipeline_ai.config import Settings
from pipeline_ai.connectors.registry import get_connectors
from pipeline_ai.privacy.sensitivity import Sensitivity


def test_four_connectors_registered():
    assert set(get_connectors()) == {"drive", "erp", "crm", "firestore"}


def test_every_spec_references_real_settings_attributes():
    """Si alguien renombra una variable de config sin actualizar el spec,
    el sub-agente quedaría NOT_CONFIGURED para siempre sin avisar."""
    for connector in get_connectors().values():
        spec = connector.spec
        for field_name in spec.required_settings:
            assert hasattr(Settings, field_name), f"{spec.name}: {field_name}"
        assert hasattr(Settings, spec.timeout_setting), spec.name


def test_erp_crm_firestore_are_confidential_by_default():
    for name in ("erp", "crm", "firestore"):
        assert get_connectors()[name].spec.default_sensitivity >= Sensitivity.CONFIDENTIAL


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["drive", "erp", "crm", "firestore"])
async def test_stub_extract_is_not_implemented_yet(name):
    with pytest.raises(NotImplementedError):
        await get_connectors()[name].extract("consulta")


def test_lazy_clients_validate_at_use_not_at_import(monkeypatch):
    """Patrón @lru_cache + settings.require(): importar es seguro; usar sin
    credenciales falla con un mensaje claro."""
    from pipeline_ai.config import settings
    from pipeline_ai.connectors import crm, erp

    monkeypatch.setattr(settings, "erpnext_api_key", "")
    monkeypatch.setattr(settings, "ghl_api_token", "")
    erp._get_client.cache_clear()
    crm._get_client.cache_clear()

    with pytest.raises(RuntimeError, match="erpnext_api_key"):
        erp._get_client()
    with pytest.raises(RuntimeError, match="ghl_api_token"):
        crm._get_client()
