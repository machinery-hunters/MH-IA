from pipeline_ai.privacy.sensitivity import (
    DOMAIN_SENSITIVITY,
    Sensitivity,
    effective_sensitivity,
    requires_anonymization,
    sensitivity_for_domain,
)


def test_effective_sensitivity_is_the_maximum_signal():
    assert (
        effective_sensitivity(Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL)
        == Sensitivity.CONFIDENTIAL
    )
    assert (
        effective_sensitivity(Sensitivity.RESTRICTED, Sensitivity.PUBLIC) == Sensitivity.RESTRICTED
    )


def test_no_signals_fails_closed():
    assert effective_sensitivity() == Sensitivity.CONFIDENTIAL


def test_unknown_or_missing_domain_fails_closed():
    assert sensitivity_for_domain("recursos-humanos") == Sensitivity.CONFIDENTIAL
    assert sensitivity_for_domain(None) == Sensitivity.CONFIDENTIAL


def test_known_domains():
    assert sensitivity_for_domain("general") == Sensitivity.INTERNAL
    for domain in ("ventas", "inventario", "proveedores", "administracion"):
        assert sensitivity_for_domain(domain) >= Sensitivity.CONFIDENTIAL


def test_confidential_and_above_require_anonymization():
    assert not requires_anonymization(Sensitivity.INTERNAL)
    assert requires_anonymization(Sensitivity.CONFIDENTIAL)
    assert requires_anonymization(Sensitivity.RESTRICTED)


def test_settings_confidential_domains_match_sensitivity_map():
    """Las dos fuentes de verdad actuales no deben divergir."""
    from pipeline_ai.config import settings

    from_map = {d for d, s in DOMAIN_SENSITIVITY.items() if requires_anonymization(s)}
    assert from_map == set(settings.confidential_domains)
