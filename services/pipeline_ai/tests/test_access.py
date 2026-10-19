"""RBAC sobre el contenido: qué dominios (y por tanto qué fragmentos) ve cada rol."""

import pytest

from pipeline_ai.privacy.sensitivity import DOMAIN_SENSITIVITY, Sensitivity, sensitivity_for_domain
from pipeline_ai.security.access import allowed_domains, can_view_document
from pipeline_ai.security.roles import Role, can_view

CONFIDENTIAL_DOMAINS = {"ventas", "inventario", "proveedores", "administracion"}


def test_consulta_only_sees_non_confidential_domains():
    assert set(allowed_domains(Role.CONSULTA)) == {"general"}


@pytest.mark.parametrize("role", [Role.LIDER, Role.CONFIGURACION, Role.CONTROL_TOTAL])
def test_roles_with_confidential_access_see_every_known_domain(role):
    assert set(allowed_domains(role)) == {"general"} | CONFIDENTIAL_DOMAINS


@pytest.mark.parametrize("role", [None, "", "root", "admin", "CONSULTA ", "lider\n"])
def test_unknown_or_missing_role_sees_nothing(role):
    assert allowed_domains(role) == ()
    assert can_view_document(role, {"domain": "general"}) is False


def test_role_can_be_given_as_a_string_or_an_enum():
    assert allowed_domains("lider") == allowed_domains(Role.LIDER)


@pytest.mark.parametrize("role", list(Role))
def test_allowed_domains_agree_with_the_sensitivity_rules(role):
    # Nadie ve un dominio que su rol no pueda ver por sensibilidad, y ve todos los que sí puede.
    for domain, level in DOMAIN_SENSITIVITY.items():
        assert (domain in allowed_domains(role)) == can_view(role, level)


def test_every_known_domain_is_below_restricted():
    # Si algún dominio pasara a RESTRICTED, este contrato exigiría revisar quién lo ve.
    assert all(level < Sensitivity.RESTRICTED for level in DOMAIN_SENSITIVITY.values())


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"domain": None},
        {"domain": ""},
        {"domain": 7},
        {"domain": ["general"]},
        {"domain": "rrhh"},  # dominio que no conocemos
        {"domain": "GENERAL"},  # comparación exacta: sin normalizar
        {"domain": " general"},
        {"source": "x", "chunk_id": "y"},
    ],
)
def test_a_fragment_without_a_recognised_domain_is_visible_to_nobody(role, metadata):
    assert can_view_document(role, metadata) is False


def test_documents_are_filtered_by_the_domain_in_their_metadata():
    assert can_view_document(Role.CONSULTA, {"domain": "general"}) is True
    assert can_view_document(Role.CONSULTA, {"domain": "ventas"}) is False
    assert can_view_document(Role.LIDER, {"domain": "ventas"}) is True


def test_unknown_domains_default_to_confidential_in_the_sensitivity_model():
    # Coherencia con privacy.sensitivity: lo desconocido es CONFIDENTIAL, nunca más laxo.
    assert sensitivity_for_domain("rrhh") == Sensitivity.CONFIDENTIAL
