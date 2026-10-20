"""
lexical

Parte "palabras clave" de la busqueda hibrida. Convierte texto en un vector disperso (indice =
termino, valor = peso) que Qdrant guarda junto al vector denso. En Qdrant el vector disperso
se declara con `Modifier.IDF`: el servidor multiplica cada termino por su IDF al consultar, asi
que aqui solo se calcula la parte de frecuencia del termino (saturacion + longitud de BM25).

Por que sirve: los embeddings densos pierden identificadores exactos (modelos de maquina,
numeros de serie, nombres propios, RFC); las palabras clave los encuentran sin ambiguedad.

Todo es deterministico y local: no usa modelos ni red. Indexacion y consulta DEBEN usar la
misma `tokenize`, o los indices no coinciden.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter

# Parametros BM25 estandar.
BM25_K1 = 1.2
BM25_B = 0.75
# Longitud media de un chunk en terminos. Qdrant no la expone al calcular el IDF, asi que se
# fija una constante razonable para chunk_size=1000 caracteres (~170 palabras, ~100 terminos
# tras quitar stopwords). Un error aqui solo suaviza o endurece la normalizacion por longitud.
AVG_DOC_TERMS = 100

_STOPWORDS = frozenset(
    "a al algo ante cada con contra cual cuales cuando cuanto cuantos cuanta cuantas de del "
    "desde dame donde e el ella ellos en entre es esa ese eso esta estan este esto fue ha hay "
    "la las le les lo los me mi mis muy ni no nos o para pero por que se si sin sobre su sus "
    "te tu un una uno unos unas y ya son ser tiene tienen como the of and to in".split()
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _stem(token: str) -> str:
    """Raíz tosca pero consistente: singular y plural dan la MISMA raíz ("cliente"/"clientes" ->
    "client", "motor"/"motores" -> "motor", "tarifa"/"tarifas" -> "tarif"). No busca ser
    lingüísticamente correcta, solo que indexación y consulta coincidan."""
    if len(token) > 4 and token.endswith("es"):
        token = token[:-2]
    elif len(token) > 3 and token.endswith("s"):
        token = token[:-1]
    if len(token) > 4 and token[-1] in "aeiou":
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Minúsculas, sin acentos, sin stopwords y con raíz tosca (ver `_stem`)."""
    terms: list[str] = []
    for token in _TOKEN.findall(_strip_accents(text.lower())):
        if len(token) < 2 or token in _STOPWORDS:
            continue
        terms.append(_stem(token))
    return terms


def term_index(term: str) -> int:
    """Indice estable (u32) de un termino. crc32 es deterministico entre procesos y versiones."""
    return zlib.crc32(term.encode("utf-8"))


def document_sparse_vector(text: str) -> tuple[list[int], list[float]]:
    """Pesos de documento: frecuencia saturada de BM25 con normalizacion por longitud."""
    terms = tokenize(text)
    if not terms:
        return [], []
    length = len(terms)
    weights: Counter[int] = Counter()
    for term, freq in Counter(terms).items():
        denominator = freq + BM25_K1 * (1 - BM25_B + BM25_B * length / AVG_DOC_TERMS)
        weights[term_index(term)] += freq * (BM25_K1 + 1) / denominator
    indices = sorted(weights)
    return indices, [weights[i] for i in indices]


def query_sparse_vector(text: str) -> tuple[list[int], list[float]]:
    """Consulta: cada termino distinto pesa 1 (el IDF lo aplica Qdrant)."""
    indices = sorted({term_index(t) for t in tokenize(text)})
    return indices, [1.0] * len(indices)
