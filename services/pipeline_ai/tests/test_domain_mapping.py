from pipeline_ai.ingestion.domain_mapping import infer_domain_from_folder_path


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


def test_logistica_and_proyectos_default_to_general():
    # Confirmado con el negocio: estas carpetas no contienen datos de
    # clientes ni cifras financieras, se mantienen en "general" a propósito.
    assert infer_domain_from_folder_path(["Logistica"]) == "general"
    assert infer_domain_from_folder_path(["Proyectos"]) == "general"


def test_unrecognized_folder_defaults_to_general():
    assert infer_domain_from_folder_path(["Recursos Humanos"]) == "general"


def test_empty_path_defaults_to_general():
    assert infer_domain_from_folder_path([]) == "general"


def test_accents_and_case_are_normalized():
    assert infer_domain_from_folder_path(["PROVEEDORES"]) == "proveedores"
    assert infer_domain_from_folder_path(["Consignación de Equipos"]) == "inventario"
