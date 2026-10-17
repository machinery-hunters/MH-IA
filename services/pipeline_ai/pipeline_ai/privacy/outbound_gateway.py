"""
outbound_gateway (STUB — se construye después del 27 de noviembre de 2026)

ÚNICO punto por el que algo sale de la red local hacia la API de Claude.
Hoy claude_node.py arma el mensaje por su cuenta (y manda la query cruda y
los nombres de archivo de las citas: ver SECURITY.md, "No resuelto").

Contrato:
    prepare_outbound(query, results, sensitivity) -> OutboundPayload
  1. Anonimiza la QUERY (no solo el contexto) cuando sensitivity >= CONFIDENTIAL.
  2. Sustituye los nombres de fuente de las citas por alias estables
     (Fuente_1, Fuente_2) y conserva el mapa solo en local para re-etiquetar
     la respuesta.
  3. Pasa el contexto por privacy.scrubber; si quedan hallazgos, BLOQUEA.
  4. Registra auditoría con HASH del payload, nunca el contenido.
  5. Es la única función autorizada para llamar al nodo Claude.
"""

from __future__ import annotations


def prepare_outbound(*args, **kwargs):
    raise NotImplementedError("Fase 1 post-27-nov: outbound gateway (ver docs/ROADMAP.md)")
