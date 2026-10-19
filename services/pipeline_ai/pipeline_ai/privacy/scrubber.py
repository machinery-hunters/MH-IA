"""
scrubber - capa DETERMINISTA de detección de datos sensibles.

Se usa en dos sitios, con los MISMOS detectores (así lo que se redacta y lo que
se verifica no pueden divergir):

  - privacy/redactor.py los usa para REDACTAR (reemplazar por marcadores) antes
    de que el texto salga del anonimizador local.
  - privacy/outbound_gateway.py los usa para VERIFICAR, justo antes de Claude.
    Cualquier hallazgo ahí BLOQUEA el envío (falla cerrada): el scrubber no
    "limpia" en silencio, porque eso ocultaría que el redactor falló.

Contrato:
    scan(text, known_names=())        -> ScrubReport   (solo tipo y conteo)
    find_spans(normalized, names)     -> list[Span]    (posiciones, para redactar)
    normalize_with_map(text)          -> (normalizado, inicio_orig, fin_orig)

Qué detecta (kind en el reporte):
    credencial        claves de API, llaves privadas, tokens JWT
    email             correos (incluye variantes "[at]" / "[dot]")
    curp, rfc         identificadores fiscales/personales de México
    clabe             18 dígitos (cuenta CLABE)
    tarjeta           13-19 dígitos que cumplen Luhn
    telefono          10-15 dígitos (con o sin separadores, con o sin +52)
    numero_largo      otras secuencias largas de dígitos (folios, cuentas)
    monto             cantidades con símbolo/moneda, miles con separador, "mil/millones"
    nombre_conocido   nombres de clientes/proveedores/fuentes que se le pasan

Robustez ante evasión simple: el texto se normaliza antes de buscar (NFKD:
dígitos de ancho completo -> ASCII, sin acentos; se quitan caracteres
invisibles de formato como el espacio de ancho cero; minúsculas; espacios
colapsados).

Límites conocidos (documentados en SECURITY.md): no detecta cantidades escritas
en palabras ("un millón doscientos mil"), homoglifos (letras cirílicas que
imitan latinas) ni nombres de personas/empresas que no estén en `known_names`.
Es una red de seguridad adicional, no una prueba de que el texto esté limpio.

Privacidad: el reporte lleva solo el TIPO y el CONTEO de cada hallazgo, nunca
el valor encontrado, para que ni los logs ni los errores puedan filtrarlo.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

# Carácter de relleno con el que se "tachan" los hallazgos ya contados para que
# un patrón posterior no los cuente otra vez.
_MASK = "\x00"


@dataclass(frozen=True)
class Finding:
    kind: str
    count: int


@dataclass(frozen=True)
class ScrubReport:
    findings: tuple[Finding, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(f.kind for f in self.findings)


@dataclass(frozen=True)
class Span:
    """Un hallazgo: tipo y posición [start, end) dentro del texto NORMALIZADO."""

    kind: str
    start: int
    end: int


def normalize_for_scan(text: str) -> str:
    """Forma canónica para buscar: NFKD, sin acentos ni caracteres invisibles,
    minúsculas y espacios colapsados."""
    decomposed = unicodedata.normalize("NFKD", text.replace(_MASK, " "))
    kept = "".join(ch for ch in decomposed if unicodedata.category(ch) not in {"Cf", "Mn"})
    return " ".join(kept.casefold().split())


def normalize_with_map(text: str) -> tuple[str, list[int], list[int]]:
    """Igual que `normalize_for_scan`, pero además devuelve, por cada carácter del
    texto normalizado, dónde empieza y dónde termina en el texto ORIGINAL.

    Sirve para redactar sobre el original: un hallazgo [a, b) en el normalizado
    corresponde a original[starts[a] : ends[b - 1]]. El texto normalizado es
    idéntico al de `normalize_for_scan` (hay un test que lo comprueba).
    """
    source = text.replace(_MASK, " ")
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pending_space = False
    space_start = 0
    space_end = 0

    for index, char in enumerate(source):
        decomposed = unicodedata.normalize("NFKD", char)
        kept = "".join(c for c in decomposed if unicodedata.category(c) not in {"Cf", "Mn"})
        for piece in kept.casefold():
            if piece.isspace():
                if not pending_space:
                    pending_space = True
                    space_start = index
                space_end = index + 1
                continue
            if pending_space:
                if out:  # sin espacio inicial
                    out.append(" ")
                    starts.append(space_start)
                    ends.append(space_end)
                pending_space = False
            out.append(piece)
            starts.append(index)
            ends.append(index + 1)
    return "".join(out), starts, ends


# ── Patrones (se aplican sobre texto ya normalizado: minúsculas, sin acentos) ──

_CREDENTIAL = re.compile(
    r"(?<![a-z0-9])sk-[a-z0-9_\-]{20,}"
    r"|(?<![a-z0-9])akia[0-9a-z]{16}"
    r"|-----begin [a-z ]*private key-----"
    r"|(?<![a-z0-9])gh[pousr]_[a-z0-9]{30,}"
    r"|(?<![a-z0-9])xox[baprs]-[a-z0-9\-]{10,}"
    r"|(?<![a-z0-9])eyj[a-z0-9_\-]{10,}\.[a-z0-9_\-]{10,}\.[a-z0-9_\-]*"
)

_EMAIL = re.compile(
    r"[a-z0-9._%+\-]+\s*(?:@|[\[({]\s*(?:at|arroba)\s*[\])}])\s*[a-z0-9\-]+"
    r"(?:\s*(?:\.|[\[({]\s*(?:dot|punto)\s*[\])}])\s*[a-z0-9\-]+)+"
)

# CURP: 4 letras, 6 dígitos (fecha), sexo, 2 letras (estado), 3 consonantes, homoclave, dígito.
_CURP = re.compile(
    r"(?<![a-z0-9])[a-z]{4}\d{6}[hm][a-z]{2}[b-df-hj-np-tv-z]{3}[a-z0-9]\d(?![a-z0-9])"
)

# RFC: 3 letras (moral) o 4 (física), 6 dígitos (fecha), 3 de homoclave; separadores opcionales.
_RFC = re.compile(r"(?<![a-z0-9])[a-z&]{3,4}[\s\-]?\d{6}[\s\-]?[a-z0-9]{3}(?![a-z0-9])")

_AMOUNT = re.compile(
    r"(?:[$€£]|us\$)\s*\d[\d,.]*"
    r"|(?<![a-z0-9])(?:usd|mxn|eur)\s*\$?\s*\d[\d,.]*"
    r"|\d[\d,.]*\s*(?:millones|millon|mil|usd|mxn|eur|dlls?|dolares|pesos|mdp|mdd)(?![a-z0-9])"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d{1,3}(?:\.\d{3}){2,}(?:,\d+)?"
)

# Secuencias de dígitos con UN separador opcional entre dígitos (espacio, punto,
# guion o ")"), con "+" inicial opcional. Se clasifican por longitud.
_DIGIT_RUN = re.compile(r"\+?\d(?:(?:\)\s?|[\s.\-])?\d)+")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _is_compact_date(digits: str) -> bool:
    """AAAAMMDD plausible (20261127): no se trata como número sensible."""
    if len(digits) != 8:
        return False
    year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:])
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def _classify_digits(raw: str) -> str | None:
    digits = "".join(ch for ch in raw if ch.isdigit())
    count = len(digits)
    separated = any(not (ch.isdigit() or ch == "+") for ch in raw)
    if count == 18:
        return "clabe"
    if 13 <= count <= 19 and _luhn_ok(digits):
        return "tarjeta"
    if 10 <= count <= 15:
        return "telefono"
    if count >= 10:
        return "numero_largo"
    if not separated and count >= 7 and not _is_compact_date(digits):
        return "numero_largo"
    return None  # p.ej. fechas "2026-11-27" o años: no son datos sensibles


def _fixed(kind: str) -> Callable[[str], str | None]:
    return lambda _text: kind


def find_spans(normalized: str, known_names: Iterable[str] = ()) -> list[Span]:
    """Detecta datos sensibles en un texto YA normalizado (`normalize_for_scan` o
    `normalize_with_map`). Devuelve los hallazgos ordenados por posición."""
    working = normalized
    spans: list[Span] = []

    def consume(pattern: re.Pattern[str], classify: Callable[[str], str | None]) -> None:
        nonlocal working

        def replace(match: re.Match[str]) -> str:
            kind = classify(match.group(0))
            if kind is None:
                return match.group(0)
            spans.append(Span(kind, match.start(), match.end()))
            return _MASK * len(match.group(0))

        working = pattern.sub(replace, working)

    # El orden importa: lo más específico primero, para no contarlo dos veces.
    consume(_CREDENTIAL, _fixed("credencial"))
    consume(_EMAIL, _fixed("email"))
    consume(_CURP, _fixed("curp"))
    consume(_RFC, _fixed("rfc"))
    consume(_AMOUNT, _fixed("monto"))
    consume(_DIGIT_RUN, _classify_digits)

    names = sorted(
        {n for n in (normalize_for_scan(raw) for raw in known_names) if len(n) >= 3},
        key=len,
        reverse=True,
    )
    if names:
        pattern = re.compile(
            r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in names) + r")(?![a-z0-9])"
        )
        consume(pattern, _fixed("nombre_conocido"))

    return sorted(spans, key=lambda span: span.start)


def scan(text: str, known_names: Iterable[str] = ()) -> ScrubReport:
    """Busca datos sensibles en `text`. Devuelve solo tipo y conteo (nunca valores)."""
    counts: dict[str, int] = {}
    for span in find_spans(normalize_for_scan(text), known_names):
        counts[span.kind] = counts.get(span.kind, 0) + 1
    return ScrubReport(tuple(Finding(kind, n) for kind, n in sorted(counts.items())))
