"""
Prompt y mensajes del nodo de razonamiento, comunes a los dos proveedores.

Claude (`claude_node`) y el modelo local (`local_reasoner`) reciben EXACTAMENTE los mismos
mensajes, construidos solo con el `OutboundPayload` que aprobó la pasarela de salida
(privacy/outbound_gateway.py): consulta y contexto anonimizados y verificados, y fuentes con
alias (Fuente_1...). Nunca `state["query"]`, `raw_context` ni nombres reales de archivo.

Así, en modo local (sin costo) se ejercita el mismo camino de privacidad que con Claude y la
respuesta se restaura igual (`payload.restore`).

REGLA CRITICA #1: el prompt de sistema EXIGE que cada afirmación cite una fuente. Si el modelo
no puede fundamentar algo, debe decirlo explícitamente en vez de inferir.
"""

from __future__ import annotations

import re

from pipeline_ai.privacy.outbound_gateway import OutboundPayload

SYSTEM_PROMPT = """Eres el analista de razonamiento de MH IA para Machinery Hunters.

REGLAS INQUEBRANTABLES:
1. Solo puedes afirmar algo si está respaldado por el CONTEXTO proporcionado.
2. Cada afirmación factual debe ir acompañada de su fuente entre corchetes,
   ej: "El equipo fue enviado a subasta [Fuente: Fuente_1]".
3. Si el contexto no cubre completamente la pregunta, dilo explícitamente:
   indica qué sí puedes responder y qué queda sin fundamento.
4. Nunca inventes cifras, nombres, fechas o folios que no aparezcan en el
   contexto. El contexto está anonimizado con marcadores entre corchetes como
   [EMPRESA_1], [PERSONA_1], [MONTO_2], [CORREO_1]: cópialos TAL CUAL, con sus
   corchetes, cuando los menciones (no los "completes", no los traduzcas ni los
   conviertas en otro valor). El sistema los sustituye por el dato real después.
5. Responde en español, tono profesional, directo.

CONTEXTO RECUPERADO:
{context}

FUENTES DISPONIBLES PARA CITAR:
{sources}
"""

# Bloques de "pensamiento" que algunos modelos locales (Qwen3, gpt-oss) escriben antes de la
# respuesta. No se muestran al usuario.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def build_messages(payload: OutboundPayload) -> list[tuple[str, str]]:
    """Mensajes (rol, texto) para el modelo de razonamiento. Solo usa el payload."""
    return [
        (
            "system",
            SYSTEM_PROMPT.format(context=payload.context, sources="\n".join(payload.source_lines)),
        ),
        ("human", payload.query),
    ]


def as_text(content: object) -> str:
    """El contenido de la respuesta puede ser texto o una lista de bloques."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, str | dict)
        )
    else:
        text = str(content)
    return _THINK_RE.sub("", text).strip()
