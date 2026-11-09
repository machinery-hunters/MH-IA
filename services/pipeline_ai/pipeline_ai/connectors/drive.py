"""
Conector Google Drive — STUB del sub-agente.

Hoy la recuperación sobre Drive vive en nodes/retriever_node.py (Qdrant) y
la ingesta en ingestion/. Este conector es el adaptador al contrato nuevo:
en la fase de desarrollo envolverá esa misma lógica de recuperación SIN
reescribirla. Drive es el único sistema que ya funciona hoy.

Sensibilidad por defecto INTERNAL: la sensibilidad real es POR DOCUMENTO
(carpeta -> dominio, ingestion/domain_mapping.py) y solo puede subir.
"""

from __future__ import annotations

from pipeline_ai.connectors.base import ConnectorSpec, RawExtraction
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area


class DriveConnector:
    spec = ConnectorSpec(
        name="drive",
        display_name="Google Drive",
        default_sensitivity=Sensitivity.INTERNAL,
        areas=(Area.GENERAL,),
        required_settings=("google_drive_folder_id",),
        timeout_setting="timeout_drive_s",
        description="Documentos por departamento indexados en Qdrant.",
    )

    async def extract(self, query: str) -> RawExtraction:
        raise NotImplementedError("Fase 1 post-27-nov: adaptar retriever_node al contrato.")
