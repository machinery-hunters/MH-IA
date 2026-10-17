"""
scrubber (STUB — se construye después del 27 de noviembre de 2026)

Capa DETERMINISTA de depuración que corre DESPUÉS del anonimizador LLM:
regex para RFC, CURP, correos, teléfonos, CLABE/tarjetas y montos, más una
lista de nombres de clientes/proveedores tomada de los propios conectores.

Por qué existe: el anonimizador basado en LLM (Ollama, 8B) es probabilístico
y puede dejar pasar un dato. Esta capa no depende de ningún modelo, así que
su cobertura es auditable y testeable.

Contrato:
    scrub(text) -> ScrubReport(text_limpio, hallazgos_restantes)
Si `hallazgos_restantes` no está vacío tras anonimizar, el outbound gateway
debe BLOQUEAR el envío a Claude (falla cerrada), no enviarlo con una
advertencia.
"""

from __future__ import annotations


def scrub(text: str) -> str:
    raise NotImplementedError("Fase 1 post-27-nov: scrubber determinista (ver docs/ROADMAP.md)")
