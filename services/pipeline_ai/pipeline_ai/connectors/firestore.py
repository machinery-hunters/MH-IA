"""
Conector Firestore / Supabase (SourcingBot, portafolio aprobado) — STUB.

Por construir (post 27-nov): lectura de la colección maquinaria_aprobada
(Firestore) y/o tablas de SourcingBot (Supabase) con credenciales de solo
lectura. El cliente se instancia de forma perezosa; la dependencia
(firebase-admin / supabase) se agrega a requirements.txt cuando se construya.
"""

from __future__ import annotations

from pipeline_ai.connectors.base import ConnectorSpec, RawExtraction
from pipeline_ai.privacy.sensitivity import Sensitivity


class FirestoreConnector:
    spec = ConnectorSpec(
        name="firestore",
        display_name="Firestore / Supabase (SourcingBot)",
        default_sensitivity=Sensitivity.CONFIDENTIAL,
        required_settings=("firestore_project_id",),
        timeout_setting="timeout_firestore_s",
        description="Portafolio de maquinaria aprobado y datos de SourcingBot.",
    )

    async def extract(self, query: str) -> RawExtraction:
        raise NotImplementedError("Fase 2 post-27-nov: conector Firestore/Supabase.")
