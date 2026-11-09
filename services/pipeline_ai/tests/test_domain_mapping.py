import pytest

from pipeline_ai.ingestion.domain_mapping import (
    _KEYWORDS,
    UNCLASSIFIED,
    infer_area_from_folder_path,
)
from pipeline_ai.security.access import can_view_document
from pipeline_ai.security.areas import Area

infer = infer_area_from_folder_path


@pytest.mark.parametrize(
    ("folder", "area"),
    [
        (["Ventas", "2026"], "comercial"),
        (["Comercial"], "comercial"),
        (["Inventario"], "inventario"),
        (["Subastas Agosto"], "inventario"),
        (["Consignación"], "inventario"),
        (["Proveedores", "Contratos"], "sourcing"),
        (["Sourcing"], "sourcing"),
        (["Websourcing"], "sourcing"),
        (["Administracion"], "finanzas"),
        (["Finanzas"], "finanzas"),
        (["Recursos Humanos"], "rh"),
        (["RH"], "rh"),
        (["Nómina"], "rh"),  # antes era administracion: ahora RH (RESTRICTED)
        (["Dirección"], "direccion"),
        (["Proyectos 2026", "Q4"], "proyectos"),
        (["Logística"], "logistica"),  # antes era general
        (["Marketing"], "marketing"),
        (["Taller"], "taller"),
        (["General"], "general"),
        (["Público"], "general"),
    ],
)
def test_department_folders_map_to_areas(folder, area):
    assert infer(folder) == area


def test_more_sensitive_areas_win_when_a_folder_mentions_two():
    assert infer(["Nómina Finanzas"]) == "rh"
    assert infer(["Dirección General"]) == "direccion"
    assert infer(["Finanzas Comercial"]) == "finanzas"


def test_word_prefix_matching_does_not_confuse_inventario_with_ventas():
    assert infer(["Inventario"]) == "inventario"


@pytest.mark.parametrize("folder", ["Legal", "Varios", "2026", "Archivo", "Compartido"])
def test_unrecognized_folders_fail_closed(folder):
    assert infer([folder]) == UNCLASSIFIED


def test_files_at_the_shared_root_fail_closed():
    assert infer([]) == UNCLASSIFIED


def test_only_the_first_folder_level_decides():
    assert infer(["Varios", "Ventas"]) == UNCLASSIFIED
    assert infer(["Ventas", "Varios"]) == "comercial"


def test_accents_and_case_are_normalized():
    assert infer(["PROVEEDORES"]) == "sourcing"
    assert infer(["Consignación de Equipos"]) == "inventario"


def test_unclassified_is_visible_to_nobody():
    everything = {area: level for area, level in ((a, 3) for a in Area)}
    assert can_view_document(everything, {"area": UNCLASSIFIED}) is False


@pytest.mark.parametrize("area", list(_KEYWORDS))
def test_every_mapped_area_is_a_real_area(area):
    # Un área que el mapeo puede devolver pero que el modelo no conoce sería invisible.
    assert Area(area)


def test_every_area_has_a_folder_keyword():
    assert set(_KEYWORDS) == {a.value for a in Area}
