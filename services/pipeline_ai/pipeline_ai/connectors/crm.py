"""
Conector CRM (GoHighLevel) — STUB.

Por construir (post 27-nov): lectura de contactos/oportunidades vía API v2
con un token de integración privada de alcance mínimo (solo lectura), y
lista blanca de campos: el CRM trae nombres, teléfonos y correos de clientes.
"""

from __future__ import annotations

from functools import lru_cache

import httpx

from pipeline_ai.config import settings
from pipeline_ai.connectors.base import ConnectorSpec, RawExtraction
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area


@lru_cache
def _get_client() -> httpx.AsyncClient:
    settings.require("ghl_api_token", "ghl_location_id")
    return httpx.AsyncClient(
        base_url=settings.ghl_base_url,
        headers={
            "Authorization": f"Bearer {settings.ghl_api_token}",
            # Versión de la API de GoHighLevel: confirmar contra su documentación vigente.
            "Version": "2021-07-28",
        },
        timeout=settings.timeout_crm_s,
    )


class CrmConnector:
    spec = ConnectorSpec(
        name="crm",
        display_name="CRM (GoHighLevel)",
        default_sensitivity=Sensitivity.CONFIDENTIAL,
        areas=(Area.COMERCIAL,),
        required_settings=("ghl_api_token", "ghl_location_id"),
        timeout_setting="timeout_crm_s",
        description="Contactos, oportunidades y pipeline comercial.",
    )

    async def extract(self, query: str) -> RawExtraction:
        raise NotImplementedError("Fase 2 post-27-nov: conector GoHighLevel.")
