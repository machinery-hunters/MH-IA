"""
Conector ERP (Frappe/ERPNext) — STUB.

Estado: el proveedor externo aún no entrega el ERP. El conector queda
preparado y NO bloqueante: sin credenciales, el sub-agente responde
NOT_CONFIGURED y el resto de fuentes sigue funcionando.

Por construir (post 27-nov): consultas de solo lectura vía API REST de
Frappe con un usuario de SOLO LECTURA, lista blanca de DocTypes y campos.
"""

from __future__ import annotations

from functools import lru_cache

import httpx

from pipeline_ai.config import settings
from pipeline_ai.connectors.base import ConnectorSpec, RawExtraction
from pipeline_ai.privacy.sensitivity import Sensitivity


@lru_cache
def _get_client() -> httpx.AsyncClient:
    # Carga perezosa: nada se valida al importar (testeable sin credenciales).
    settings.require("erpnext_base_url", "erpnext_api_key", "erpnext_api_secret")
    return httpx.AsyncClient(
        base_url=settings.erpnext_base_url,
        # Esquema de autenticación de Frappe (token key:secret): confirmar
        # contra la versión real instalada por el proveedor.
        headers={
            "Authorization": f"token {settings.erpnext_api_key}:{settings.erpnext_api_secret}"
        },
        timeout=settings.timeout_erp_s,
    )


class ErpConnector:
    spec = ConnectorSpec(
        name="erp",
        display_name="ERP (Frappe/ERPNext)",
        default_sensitivity=Sensitivity.CONFIDENTIAL,
        required_settings=("erpnext_base_url", "erpnext_api_key", "erpnext_api_secret"),
        timeout_setting="timeout_erp_s",
        description="Registros financieros y operativos del ERP.",
    )

    async def extract(self, query: str) -> RawExtraction:
        raise NotImplementedError("Fase 2 post-27-nov: depende de la entrega del ERP.")
