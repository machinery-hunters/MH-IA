"""
outbound_gateway - ÚNICO punto por el que algo sale de la red local hacia Claude.

    prepare_outbound(query=..., context=..., citations=..., sensitivity=..., ...)
        -> OutboundPayload                 (o lanza OutboundBlocked)

Qué hace, en orden:
  1. Verifica que se pueda enviar: sensibilidad RESTRICTED nunca sale; si el
     nivel exige anonimizar, el anonimizador local tiene que haber corrido
     (`anonymized=True`); consulta y contexto no pueden estar vacíos (sin
     evidencia no se llama a Claude: cero alucinaciones).
  2. Sustituye los nombres de fuente por alias estables (Fuente_1, Fuente_2...).
     El mapa alias -> nombre real SOLO vive en local (en el payload, fuera de
     su repr) y sirve para re-etiquetar la respuesta con `restore()`. Lo mismo
     ocurre con el mapa de marcadores ([EMPRESA_1], [MONTO_2]...) que dejó el
     anonimizador: `restore()` devuelve el dato real en la respuesta, en local.
  3. Pasa la consulta y el contexto por el scrubber determinista. Cualquier
     hallazgo BLOQUEA el envío (falla cerrada): se lanza OutboundBlocked con los
     motivos (solo tipos, nunca valores).
  4. Registra auditoría con el HASH (SHA-256) del payload, nunca el contenido.

Garantías que cubren los tests:
  - Los nombres reales de archivo no salen, ni aunque el anonimizador los deje
    en el texto (se sustituyen) ni con variantes de acentos (se bloquean).
  - Nada sale sin pasar por aquí: `claude_node` solo acepta un OutboundPayload y
    este solo se puede construir desde `prepare_outbound`.

Límites: el hash es SHA-256 simple (no HMAC); sirve para correlacionar y probar
integridad, no para ocultar consultas muy cortas frente a quien ya tenga el log.
El candado del constructor evita errores de código, no a un atacante con acceso
al proceso.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pipeline_ai.privacy.redactor import PLACEHOLDER_RE
from pipeline_ai.privacy.scrubber import scan
from pipeline_ai.privacy.sensitivity import Sensitivity, requires_anonymization
from pipeline_ai.sources import Citation

audit_log = logging.getLogger("mhia.audit")

# Candado del constructor: solo prepare_outbound tiene este objeto.
_ISSUER = object()

_ALIAS_RE = re.compile(r"\bFuente_\d+\b")


class OutboundBlocked(Exception):  # noqa: N818 - nombre del dominio, no un error genérico
    """El envío a Claude se bloqueó. `reasons` lleva solo tipos/motivos, jamás contenido."""

    def __init__(self, reasons: Iterable[str]):
        self.reasons: tuple[str, ...] = tuple(reasons)
        super().__init__(", ".join(self.reasons))


@dataclass(frozen=True)
class OutboundPayload:
    """Lo ÚNICO que puede llegar a Claude. Solo lo construye `prepare_outbound`."""

    query: str
    context: str
    source_lines: tuple[str, ...]
    sensitivity: Sensitivity
    audit_hash: str
    # alias -> nombre real. SOLO local: nunca se envía y no aparece en repr.
    alias_map: Mapping[str, str] = field(repr=False)
    # marcador ([EMPRESA_1], [MONTO_2]...) -> dato real. SOLO local, igual que alias_map.
    placeholder_map: Mapping[str, str] = field(default_factory=dict, repr=False)
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise PermissionError("OutboundPayload solo se construye con prepare_outbound().")

    def restore(self, text: str) -> str:
        """Devuelve los datos reales en la respuesta de Claude (se hace en local, antes de
        mostrarla al usuario): los marcadores vuelven a ser el dato original y los alias
        de fuente vuelven a ser el nombre real del documento. Lo desconocido se deja igual."""
        text = PLACEHOLDER_RE.sub(lambda m: self.placeholder_map.get(m.group(0), m.group(0)), text)
        return _ALIAS_RE.sub(lambda m: self.alias_map.get(m.group(0), m.group(0)), text)


def assign_source_aliases(sources: Iterable[str]) -> dict[str, str]:
    """nombre real -> alias estable (Fuente_1, Fuente_2...), por orden de aparición."""
    aliases: dict[str, str] = {}
    for source in sources:
        if source not in aliases:
            aliases[source] = f"Fuente_{len(aliases) + 1}"
    return aliases


def _name_variants(source: str) -> set[str]:
    """Formas en que un nombre de fuente puede aparecer en el texto: completo,
    sin el prefijo "Google Drive: " y sin extensión."""
    tail = source.split(": ", 1)[-1]
    variants = {source, tail}
    for item in list(variants):
        stem, dot, ext = item.rpartition(".")
        if dot and stem and 1 <= len(ext) <= 5 and " " not in ext:
            variants.add(stem)
    return {v.strip() for v in variants if len(v.strip()) >= 4}


def _resolve_aliases(
    citations: list[Citation], provided: Mapping[str, str] | None
) -> dict[str, str]:
    """alias -> real. Respeta los alias que ya asignó el retriever y completa los que falten."""
    alias_to_real = dict(provided or {})
    known_real = set(alias_to_real.values())
    counter = len(alias_to_real) + 1
    for citation in citations:
        source = citation["source"]
        if source in known_real:
            continue
        while f"Fuente_{counter}" in alias_to_real:
            counter += 1
        alias_to_real[f"Fuente_{counter}"] = source
        known_real.add(source)
    return alias_to_real


def _apply_aliases(text: str, real_to_alias: Mapping[str, str]) -> str:
    replacements = sorted(
        (
            (variant, alias)
            for real, alias in real_to_alias.items()
            for variant in _name_variants(real)
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    for variant, alias in replacements:
        text = re.sub(re.escape(variant), alias, text, flags=re.IGNORECASE)
    return text


def _digest(query: str, context: str, source_lines: Iterable[str]) -> str:
    canonical = json.dumps(
        {"query": query, "context": context, "sources": list(source_lines)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _audit(
    event: str,
    *,
    digest: str,
    sensitivity: Sensitivity,
    session_id: str,
    n_sources: int,
    query_chars: int,
    context_chars: int,
    reasons: Iterable[str] = (),
) -> None:
    """Auditoría SIN contenido: solo hash, tamaños, nivel y motivos."""
    audit_log.info(
        json.dumps(
            {
                "event": event,
                "ts": datetime.now(UTC).isoformat(),
                "sha256": digest,
                "sensitivity": sensitivity.name,
                "session_id": session_id,
                "n_sources": n_sources,
                "query_chars": query_chars,
                "context_chars": context_chars,
                "reasons": list(reasons),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def prepare_outbound(
    *,
    query: str,
    context: str,
    citations: Iterable[Citation],
    sensitivity: Sensitivity,
    anonymized: bool,
    source_aliases: Mapping[str, str] | None = None,
    known_names: Iterable[str] = (),
    placeholder_map: Mapping[str, str] | None = None,
    session_id: str = "",
) -> OutboundPayload:
    """Verifica, aliasa y audita. Lanza OutboundBlocked si algo no se puede enviar.

    `anonymized` debe ser True solo si el anonimizador local corrió y produjo
    una salida válida; pasar texto crudo con anonymized=True es un error del
    llamador que el scrubber igualmente intenta atrapar.
    """
    citation_list = list(citations)
    alias_to_real = _resolve_aliases(citation_list, source_aliases)
    real_to_alias = {real: alias for alias, real in alias_to_real.items()}

    safe_query = _apply_aliases(query, real_to_alias)
    safe_context = _apply_aliases(context, real_to_alias)

    best_score: dict[str, float] = {}
    for citation in citation_list:
        alias = real_to_alias[citation["source"]]
        best_score[alias] = max(best_score.get(alias, 0.0), float(citation["score"]))
    source_lines = tuple(
        f"- {alias} (score={score:.2f})"
        for alias, score in sorted(best_score.items(), key=lambda item: int(item[0].split("_")[1]))
    )

    digest = _digest(safe_query, safe_context, source_lines)

    reasons: list[str] = []
    if sensitivity >= Sensitivity.RESTRICTED:
        reasons.append("sensibilidad_restringida")
    if requires_anonymization(sensitivity) and not anonymized:
        reasons.append("anonimizacion_no_verificada")
    if not safe_query.strip():
        reasons.append("consulta_vacia")
    if not safe_context.strip():
        reasons.append("contexto_vacio")

    names = {v for real in alias_to_real.values() for v in _name_variants(real)}
    names.update(known_names)
    for label, text in (("consulta", safe_query), ("contexto", safe_context)):
        reasons.extend(f"{label}:{kind}" for kind in scan(text, names).kinds)

    audit_fields = {
        "digest": digest,
        "sensitivity": sensitivity,
        "session_id": session_id,
        "n_sources": len(best_score),
        "query_chars": len(safe_query),
        "context_chars": len(safe_context),
    }
    if reasons:
        _audit("outbound_blocked", reasons=reasons, **audit_fields)
        raise OutboundBlocked(reasons)

    _audit("outbound_prepared", **audit_fields)
    return OutboundPayload(
        query=safe_query,
        context=safe_context,
        source_lines=source_lines,
        sensitivity=sensitivity,
        audit_hash=digest,
        alias_map=dict(alias_to_real),
        placeholder_map=dict(placeholder_map or {}),
        _issuer=_ISSUER,
    )
