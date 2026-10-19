"""
redactor - REDACCION determinista de datos sensibles (el código reemplaza, no el modelo).

Por qué existe: en el laboratorio, un LLM local de 8B encargado de "reescribir el
texto anonimizado" dejó pasar RFC, correos y nombres, y a la vez tapó datos
inocuos (horarios, conteos). Reescribir texto es justo lo que un modelo pequeño
hace mal. Este módulo parte la tarea:

  - Lo que tiene forma reconocible (RFC, CURP, correos, teléfonos, CLABE, tarjetas,
    montos, credenciales) lo reemplaza CÓDIGO, con los mismos detectores que luego
    usa el outbound gateway para verificar (privacy/scrubber.py).
  - Lo que no tiene forma (nombres de personas y empresas) lo DETECTA el LLM local
    (solo devuelve una lista; ver nodes/ollama_node.py) y lo reemplaza CÓDIGO.

Marcadores: `[CORREO_1]`, `[RFC_1]`, `[MONTO_2]`, `[PERSONA_1]`, `[EMPRESA_1]`...
La misma entidad recibe el mismo marcador en la consulta y en el contexto, porque
comparten una sola `Redaction`. El mapa marcador -> original vive SOLO en local
(fuera del repr) y sirve para restaurar la respuesta de Claude antes de mostrarla.

Se redacta sobre el texto ORIGINAL (se conserva todo lo que no es dato sensible,
con sus mayúsculas y acentos); la detección corre sobre una versión normalizada y
las posiciones se traducen con `normalize_with_map`.

Esto no es una garantía por sí solo: lo verifica el gateway con el scrubber, y los
nombres que el LLM no detecte siguen siendo un límite conocido (SECURITY.md).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from pipeline_ai.privacy.scrubber import (
    Span,
    find_spans,
    normalize_for_scan,
    normalize_with_map,
)

EntityKind = Literal["persona", "empresa"]

_LABELS = {
    "credencial": "CREDENCIAL",
    "email": "CORREO",
    "curp": "CURP",
    "rfc": "RFC",
    "clabe": "CLABE",
    "tarjeta": "TARJETA",
    "telefono": "TELEFONO",
    "numero_largo": "NUMERO",
    "monto": "MONTO",
    "nombre_conocido": "NOMBRE",
}
_ENTITY_LABELS: dict[str, str] = {"persona": "PERSONA", "empresa": "EMPRESA"}

PLACEHOLDER_RE = re.compile(r"\[[A-Z]+_\d+\]")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"de", "del", "la", "las", "el", "los", "y", "e", "da", "do", "van", "von"})
_LEGAL_TOKENS = frozenset(
    {"sa", "cv", "rl", "sapi", "sab", "srl", "inc", "llc", "ltd", "corp", "sc", "ac"}
    | {"s", "a", "c", "v", "r", "l", "de", "p", "i", "b"}
)
_LEGAL_MARKERS = ("sa", "cv", "rl", "sapi", "sab", "srl", "inc", "llc", "ltd", "corp")
_MIN_NAME_CHARS = 4


@dataclass(frozen=True)
class Entity:
    """Un nombre propio detectado por el LLM local (ya validado contra el texto)."""

    name: str
    kind: EntityKind


@dataclass
class Redaction:
    """Estado de una redacción: asigna marcadores estables y guarda el mapa de
    restauración. Es SOLO local: el mapa no aparece en el repr ni se envía a Claude."""

    _forward: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)
    _reverse: dict[str, str] = field(default_factory=dict, repr=False)
    _counts: dict[str, int] = field(default_factory=dict, repr=False)

    def placeholder(
        self, label: str, original: str, *, key: str | None = None, display: str | None = None
    ) -> str:
        """Marcador para `original`. Mismo `key` (por defecto, el texto canónico) ->
        mismo marcador. `display` es lo que se restaurará (por defecto, `original`)."""
        canonical = key if key is not None else re.sub(r"[\s\-.]", "", normalize_for_scan(original))
        known = self._forward.get((label, canonical))
        if known is not None:
            return known
        number = self._counts.get(label, 0) + 1
        self._counts[label] = number
        token = f"[{label}_{number}]"
        self._forward[(label, canonical)] = token
        self._reverse[token] = (display if display is not None else original).strip()
        return token

    @property
    def mapping(self) -> dict[str, str]:
        """marcador -> original. Copia; solo para uso local."""
        return dict(self._reverse)

    def restore(self, text: str) -> str:
        """Devuelve los originales en lugar de los marcadores. Los marcadores que no
        conoce (p.ej. uno inventado por el modelo) se dejan tal cual."""
        return PLACEHOLDER_RE.sub(lambda m: self._reverse.get(m.group(0), m.group(0)), text)


def _redact_spans(
    text: str,
    redaction: Redaction,
    finder: Callable[[str], list[Span]],
    describe: Callable[[Span, str], tuple[str, str | None, str | None]],
) -> str:
    """Reemplaza en `text` (original) los hallazgos de `finder` (sobre el normalizado).

    `describe(span, original)` -> (etiqueta, clave, display) para el marcador.
    """
    normalized, starts, ends = normalize_with_map(text)
    spans = finder(normalized)
    if not spans:
        return text

    mapped = sorted(
        ((starts[s.start], ends[s.end - 1], s) for s in spans), key=lambda item: (item[0], -item[1])
    )
    merged: list[tuple[int, int, Span]] = []
    for begin, end, span in mapped:
        if merged and begin < merged[-1][1]:  # se solapan: se unen en un solo hallazgo
            first_begin, first_end, first_span = merged[-1]
            merged[-1] = (first_begin, max(first_end, end), first_span)
        else:
            merged.append((begin, end, span))

    pieces: list[str] = []
    cursor = 0
    for begin, end, span in merged:
        pieces.append(text[cursor:begin])
        original = text[begin:end]
        label, key, display = describe(span, original)
        pieces.append(redaction.placeholder(label, original, key=key, display=display))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def redact_sensitive(text: str, redaction: Redaction) -> str:
    """Redacta datos con forma reconocible (correos, RFC, CURP, teléfonos, CLABE,
    tarjetas, montos, credenciales, números largos)."""
    return _redact_spans(
        text,
        redaction,
        lambda normalized: find_spans(normalized),
        lambda span, original: (_LABELS[span.kind], None, None),
    )


# ── Nombres propios (los detecta el LLM local; el reemplazo lo hace el código) ──


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(normalize_for_scan(name)))


def _strip_legal_suffix(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Quita "S.A. de C.V.", "S. de R.L.", "Inc"... del final si de verdad es forma legal."""
    end = len(tokens)
    while end > 0 and tokens[end - 1] in _LEGAL_TOKENS:
        end -= 1
    suffix = "".join(tokens[end:])
    if end > 0 and any(marker in suffix for marker in _LEGAL_MARKERS):
        return tokens[:end]
    return tokens


def _acceptable(tokens: tuple[str, ...]) -> bool:
    return (
        bool(tokens)
        and sum(len(t) for t in tokens) >= _MIN_NAME_CHARS
        and not all(t.isdigit() for t in tokens)
        and tokens[0] not in _STOPWORDS
        and tokens[-1] not in _STOPWORDS
    )


def name_variants(entity: Entity) -> set[tuple[str, ...]]:
    """Formas en que el mismo nombre puede aparecer en el texto (ya tokenizadas y
    normalizadas): completo, sin forma legal y, para personas, subsecuencias de 2+
    palabras ("Laura Mendoza Prieto" -> "Laura Mendoza", "Mendoza Prieto"); para
    empresas, prefijos de 2+ palabras ("Grupo Industrial Ejemplo" -> "Grupo Industrial")."""
    full = _tokens(entity.name)
    core = _strip_legal_suffix(full)
    variants = {full, core}
    for base in {full, core}:
        if len(base) < 3:
            continue
        if entity.kind == "persona":
            for size in range(2, len(base)):
                for begin in range(len(base) - size + 1):
                    variants.add(base[begin : begin + size])
        else:
            for size in range(2, len(base)):
                variants.add(base[:size])
    return {variant for variant in variants if _acceptable(variant)}


_LEGAL_FORM_RE = re.compile(
    r"[,\s]*(?:s\.?\s?a\.?(?:\s?p\.?\s?i\.?|\s?b\.?)?(?:\s+de\s+c\.?\s?v\.?)?"
    r"|s\.?\s?de\s?r\.?\s?l\.?(?:\s+de\s+c\.?\s?v\.?)?|inc\.?|llc|ltd\.?|corp\.?)\s*$",
    re.IGNORECASE,
)


def _display_name(entity: Entity) -> str:
    """Lo que se restaura en lugar del marcador. Para empresas, sin la forma legal
    ("S.A. de C.V."): el texto suele conservarla junto al marcador y así no se duplica."""
    name = entity.name.strip()
    if entity.kind == "empresa":
        stripped = _LEGAL_FORM_RE.sub("", name).strip(" ,")
        if len(stripped) >= _MIN_NAME_CHARS:
            return stripped
    return name


def redact_names(text: str, entities: Iterable[Entity], redaction: Redaction) -> str:
    """Reemplaza por marcadores los nombres detectados (insensible a mayúsculas y
    acentos). Todas las variantes de una misma entidad comparten marcador."""
    lookup: dict[tuple[str, ...], tuple[str, str, str]] = {}
    for entity in sorted(entities, key=lambda e: -len(e.name)):
        label = _ENTITY_LABELS[entity.kind]
        key = " ".join(_tokens(entity.name))
        display = _display_name(entity)
        for variant in name_variants(entity):
            lookup.setdefault(variant, (label, key, display))
    if not lookup:
        return text

    # Entre palabras puede haber espacios, guiones, puntos o comas ("S.A. de C.V.", "Acme, S.A.").
    alternatives = sorted(
        (r"[^a-z0-9]{1,3}".join(re.escape(token) for token in variant) for variant in lookup),
        key=len,
        reverse=True,
    )
    pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")

    def finder(normalized: str) -> list[Span]:
        return [Span("nombre", m.start(), m.end()) for m in pattern.finditer(normalized)]

    def describe(span: Span, original: str) -> tuple[str, str | None, str | None]:
        found = lookup.get(_tokens(original))
        if found is None:  # no debería pasar; ante la duda se redacta igual, con marcador genérico
            return _LABELS["nombre_conocido"], None, None
        label, key, display = found
        return label, key, display

    return _redact_spans(text, redaction, finder, describe)


def known_name_strings(entities: Iterable[Entity]) -> list[str]:
    """Variantes de los nombres detectados, como texto, para que el gateway verifique
    que ninguna quedó en el texto que se envía."""
    names = {" ".join(variant) for entity in entities for variant in name_variants(entity)}
    return sorted(names)
