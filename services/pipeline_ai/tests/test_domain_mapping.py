import pytest

from pipeline_ai.ingestion.domain_mapping import (
    _KEYWORDS,
    UNCLASSIFIED,
    infer_domain_from_folder_path,
)
from pipeline_ai.privacy.sensitivity import DOMAIN_SENSITIVITY
from pipeline_ai.security.access import allowed_domains, can_view_document


def test_ventas_folder():
    assert infer_domain_from_folder_path(["Ventas", "2026"]) == "ventas"


def test_inventario_folder_variants():
    assert infer_domain_from_folder_path(["Inventario"]) == "inventario"
    assert infer_domain_from_folder_path(["Subastas Agosto"]) == "inventario"
    assert infer_domain_from_folder_path(["Consignación"]) == "inventario"


def test_proveedores_folder():
    assert infer_domain_from_folder_path(["Proveedores", "Contratos"]) == "proveedores"


def test_sourcing_folder_maps_to_proveedores():
    assert infer_domain_from_folder_path(["Sourcing"]) == "proveedores"


def test_administracion_folder():
    assert infer_domain_from_folder_path(["Administracion"]) == "administracion"
    assert infer_domain_from_folder_path(["Finanzas"]) == "administracion"


def test_proyectos_is_its_own_confidential_domain():
    # Decisión del negocio (20 oct 2026): los proyectos NO son visibles para `consulta`.
    assert infer_domain_from_folder_path(["Proyectos"]) == "proyectos"
    assert infer_domain_from_folder_path(["Proyectos 2026", "Q4"]) == "proyectos"
    assert "proyectos" not in allowed_domains("consulta")
    assert "proyectos" in allowed_domains("lider")


def test_general_requires_an_explicit_folder_name():
    assert infer_domain_from_folder_path(["General"]) == "general"
    assert infer_domain_from_folder_path(["Público"]) == "general"
    # Confirmado con el negocio: logística no contiene datos de clientes ni cifras financieras.
    assert infer_domain_from_folder_path(["Logistica"]) == "general"


@pytest.mark.parametrize(
    "folder",
    ["Recursos Humanos", "Dirección", "Legal", "Marketing", "Varios", "2026", "Archivo"],
)
def test_unrecognized_folders_fail_closed(folder):
    # Antes caían en "general" (visible para `consulta`): un departamento nuevo quedaba expuesto.
    assert infer_domain_from_folder_path([folder]) == UNCLASSIFIED


def test_files_at_the_shared_root_fail_closed():
    assert infer_domain_from_folder_path([]) == UNCLASSIFIED


def test_only_the_first_folder_level_decides():
    assert infer_domain_from_folder_path(["Varios", "Ventas"]) == UNCLASSIFIED
    assert infer_domain_from_folder_path(["Ventas", "Varios"]) == "ventas"


def test_accents_and_case_are_normalized():
    assert infer_domain_from_folder_path(["PROVEEDORES"]) == "proveedores"
    assert infer_domain_from_folder_path(["Consignación de Equipos"]) == "inventario"


def test_unclassified_is_visible_to_nobody():
    assert UNCLASSIFIED not in DOMAIN_SENSITIVITY
    for role in ("consulta", "lider", "configuracion", "control_total"):
        assert UNCLASSIFIED not in allowed_domains(role)
        assert can_view_document(role, {"domain": UNCLASSIFIED}) is False


@pytest.mark.parametrize("domain", list(_KEYWORDS))
def test_every_mapped_domain_has_a_sensitivity(domain):
    # Un dominio que el mapeo puede devolver pero que el RBAC no conoce sería invisible para todos.
    assert domain in DOMAIN_SENSITIVITY
