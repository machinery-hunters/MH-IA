"""
Estado central de MH IA.

Diseño clave para las reglas críticas:
- `is_confidential`: marcado por el router local (Ollama). Determina si el
  payload puede salir hacia Claude o debe quedarse/anonimizarse localmente.
- `retrieved_docs` + `citations`: si quedan vacíos tras la recuperación,
  el guardrail de "cero alucinaciones" aborta el flujo antes de llegar a
  cualquier nodo generador de respuesta.
- `anonymized_context`: es el ÚNICO campo que puede viajar hacia el nodo
  Claude cuando is_confidential=True. `raw_context` nunca cruza esa frontera.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Literal, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage


class Citation(TypedDict):
    source: str  # p.ej. "Google Drive: Contrato_Consignacion_034.pdf"
    chunk_id: str
    score: float


class MHIAState(TypedDict, total=False):
    # ── Entrada / conversación ──────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add]
    query: str
    session_id: str

    # ── Clasificación / enrutamiento ────────────────────────────────────
    # decidido por el router_node (ejecutado siempre en Ollama, nunca en Claude)
    is_confidential: bool
    route: Literal["local_only", "retrieve_then_reason", "clarify"]
    data_domain: Literal["ventas", "inventario", "proveedores", "general"]

    # ── Recuperación (Qdrant / futuro ERPNext) ──────────────────────────
    retrieved_docs: list[Document]
    citations: list[Citation]
    retrieval_ok: bool  # False si no hubo hits por encima del umbral

    # ── Privacidad: frontera dura entre contexto crudo y anonimizado ────
    raw_context: str  # nunca se envía a Claude
    anonymized_context: str  # lo único permitido hacia Claude si es confidencial

    # ── Salida ───────────────────────────────────────────────────────────
    final_answer: str
    aborted: bool
    abort_reason: str
