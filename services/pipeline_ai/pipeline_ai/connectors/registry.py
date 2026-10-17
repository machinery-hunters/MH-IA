"""Registro de conectores. Agregar un conector = una línea aquí + su spec."""

from __future__ import annotations

from pipeline_ai.connectors.base import Connector
from pipeline_ai.connectors.crm import CrmConnector
from pipeline_ai.connectors.drive import DriveConnector
from pipeline_ai.connectors.erp import ErpConnector
from pipeline_ai.connectors.firestore import FirestoreConnector


def get_connectors() -> dict[str, Connector]:
    connectors: list[Connector] = [
        DriveConnector(),
        ErpConnector(),
        CrmConnector(),
        FirestoreConnector(),
    ]
    return {c.spec.name: c for c in connectors}
