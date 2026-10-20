"""Parte léxica de la búsqueda híbrida: tokenización y vectores dispersos."""

import zlib

import pytest

from pipeline_ai.retrieval.lexical import (
    BM25_K1,
    document_sparse_vector,
    query_sparse_vector,
    term_index,
    tokenize,
)


def test_tokenize_ignores_case_and_accents():
    assert tokenize("Cotización") == tokenize("cotizacion") == tokenize("COTIZACIÓN")


def test_tokenize_drops_stopwords_and_punctuation():
    terms = tokenize("¿Cuál es el horario de la oficina de Mexicali?")

    assert set(tokenize("horario oficina Mexicali")) <= set(terms)
    assert not {"cual", "es", "el", "de", "la"} & set(terms)


@pytest.mark.parametrize(
    ("singular", "plural"),
    [
        ("cliente", "clientes"),
        ("motor", "motores"),
        ("tarifa", "tarifas"),
        ("excavadora", "excavadoras"),
        ("proveedor", "proveedores"),
        ("maquina", "maquinas"),
        ("transporte", "transportes"),
    ],
)
def test_singular_and_plural_share_the_same_stem(singular, plural):
    assert tokenize(singular) == tokenize(plural)


def test_identifiers_survive_tokenization():
    # Lo que los embeddings pierden y las palabras clave encuentran: códigos y modelos.
    assert "pc200" in tokenize("Komatsu PC200-8")
    assert "cfn010101ab1" in tokenize("RFC CFN010101AB1")


@pytest.mark.parametrize("text", ["", "   ", "¿¡?!", "el la de"])
def test_tokenize_of_nothing_useful_is_empty(text):
    assert tokenize(text) == []


def test_term_index_is_stable_across_processes_and_fits_u32():
    assert term_index("excavador") == zlib.crc32(b"excavador")
    assert 0 <= term_index("excavador") < 2**32


def test_query_vector_has_unique_sorted_indices_with_unit_weight():
    indices, values = query_sparse_vector("excavadora excavadoras anticipo")

    assert indices == sorted(set(indices))
    assert len(indices) == 2  # singular y plural colapsan en un solo término
    assert values == [1.0, 1.0]


def test_query_without_useful_terms_has_an_empty_vector():
    assert query_sparse_vector("¿qué es lo de la?") == ([], [])


def test_document_vector_indices_are_sorted_unique_and_weights_positive():
    indices, values = document_sparse_vector("anticipo del 30 por ciento anticipo pagado")

    assert indices == sorted(set(indices))
    assert len(indices) == len(values) and all(v > 0 for v in values)


def test_repeating_a_term_raises_its_weight_but_with_saturation():
    [one] = document_sparse_vector("anticipo")[1]
    many = dict(zip(*document_sparse_vector(" ".join(["anticipo"] * 50)), strict=True))[
        term_index("anticip")
    ]

    assert many > one
    assert many < (BM25_K1 + 1) * 1.01  # nunca supera (k1 + 1): saturación de BM25


def test_a_longer_document_gives_the_same_term_less_weight():
    short = dict(zip(*document_sparse_vector("anticipo"), strict=True))[term_index("anticip")]
    long_text = "anticipo " + " ".join(f"palabra{i}" for i in range(300))
    long = dict(zip(*document_sparse_vector(long_text), strict=True))[term_index("anticip")]

    assert long < short


def test_document_and_query_use_the_same_index_for_the_same_term():
    doc_indices, _ = document_sparse_vector("La cotización de la excavadora incluye anticipo.")
    query_indices, _ = query_sparse_vector("¿Cuánto es el anticipo de la excavadora?")

    assert set(query_indices) <= set(doc_indices)


def test_document_without_useful_terms_has_an_empty_vector():
    assert document_sparse_vector("¿¡?!") == ([], [])
