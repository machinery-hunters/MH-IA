"""Acceso por área (rol × área): qué fragmentos ve cada combinación de membresías.

Incluye una prueba de EQUIVALENCIA contra un Qdrant real en memoria: el filtro que se manda
a la base y la verificación en Python deben dar exactamente el mismo resultado sobre una
matriz de fragmentos (nuevos, anteriores, con etiqueta, corruptos)."""

import itertools
import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.access import access_filter, can_view_document
from pipeline_ai.security.areas import (
    AREA_MIN_SENSITIVITY,
    LEGACY_DOMAIN_TO_AREA,
    Area,
    InvalidMembership,
    can_access,
    format_memberships,
    fragment_area,
    fragment_sensitivity,
    from_state,
    parse_memberships,
    to_state,
    visible_areas,
)

I, C, R = Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED  # noqa: E741


# ── Modelo ───────────────────────────────────────────────────────────────


def test_minimum_sensitivity_per_area():
    assert AREA_MIN_SENSITIVITY[Area.GENERAL] is I
    assert AREA_MIN_SENSITIVITY[Area.RH] is R
    assert all(AREA_MIN_SENSITIVITY[a] is C for a in Area if a not in {Area.GENERAL, Area.RH})
    assert set(AREA_MIN_SENSITIVITY) == set(Area)


def test_legacy_domains_map_to_areas():
    assert LEGACY_DOMAIN_TO_AREA == {
        "ventas": Area.COMERCIAL,
        "proveedores": Area.SOURCING,
        "administracion": Area.FINANZAS,
        "proyectos": Area.PROYECTOS,
        "inventario": Area.INVENTARIO,
        "general": Area.GENERAL,
    }


def test_parse_and_format_memberships_round_trip():
    parsed = parse_memberships(" comercial:confidential, general:INTERNAL ,rh:RESTRICTED")
    assert parsed == {Area.COMERCIAL: C, Area.GENERAL: I, Area.RH: R}
    assert format_memberships(parsed) == "comercial:CONFIDENTIAL, rh:RESTRICTED, general:INTERNAL"
    assert parse_memberships("") == {}


@pytest.mark.parametrize(
    "text",
    ["ventas:CONFIDENTIAL", "comercial", "comercial:SECRETO", "comercial:C,comercial:I", "rh:9"],
)
def test_invalid_memberships_are_rejected(text):
    with pytest.raises(InvalidMembership):
        parse_memberships(text)


def test_state_round_trip_drops_anything_invalid():
    assert from_state(to_state({Area.TALLER: C})) == {Area.TALLER: C}
    garbage = {"taller": 2, "ventas": 2, "rh": 7, "finanzas": "CONFIDENTIAL", 3: 1, "general": None}
    assert from_state(garbage) == {Area.TALLER: C, Area.FINANZAS: C}
    for raw in (None, [], "comercial:2", 5):
        assert from_state(raw) == {}


def test_visible_areas_ignore_levels_below_the_area_minimum():
    memberships = {Area.INVENTARIO: I, Area.RH: C, Area.GENERAL: I, Area.COMERCIAL: R}
    assert visible_areas(memberships) == {Area.GENERAL: I, Area.COMERCIAL: R}


def test_can_access_for_connectors():
    assert can_access({Area.FINANZAS: C}, (Area.INVENTARIO, Area.FINANZAS), C)
    assert not can_access({Area.FINANZAS: C}, (Area.INVENTARIO, Area.FINANZAS), R)
    assert not can_access({Area.COMERCIAL: R}, (Area.INVENTARIO,), I)
    assert not can_access({Area.COMERCIAL: R}, (), I)


# ── Fragmentos ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("metadata", "area", "level"),
    [
        ({"area": "comercial"}, Area.COMERCIAL, C),
        ({"area": "general", "sensitivity": 1}, Area.GENERAL, I),
        ({"area": "general", "sensitivity": 3}, Area.GENERAL, R),  # la etiqueta sube
        ({"area": "comercial", "sensitivity": 0}, Area.COMERCIAL, C),  # nunca baja
        ({"area": "rh"}, Area.RH, R),
        ({"domain": "ventas"}, Area.COMERCIAL, C),  # fragmento anterior
        ({"domain": "general"}, Area.GENERAL, I),
        ({"area": "finanzas", "sensitivity": 9}, Area.FINANZAS, R),  # etiqueta corrupta
        ({"area": "finanzas", "sensitivity": True}, Area.FINANZAS, C),
    ],
)
def test_fragment_area_and_sensitivity(metadata, area, level):
    assert fragment_area(metadata) is area
    assert fragment_sensitivity(metadata) is level


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"area": None},
        {"area": ""},
        {"area": 7},
        {"area": ["general"]},
        {"area": "GENERAL"},  # comparación exacta: sin normalizar
        {"area": "rrhh"},
        {"domain": "rrhh"},
        {"domain": "comercial"},  # vocabulario nuevo en la clave anterior: no
        {"area": "sin_clasificar", "domain": "general"},  # área inválida no cae a domain
        {"source": "x", "chunk_id": "y"},
    ],
)
def test_a_fragment_without_a_recognised_area_is_visible_to_nobody(metadata):
    everything = {area: R for area in Area}
    assert can_view_document(everything, metadata) is False


def test_documents_are_filtered_by_area_and_level():
    jefe_comercial = {Area.GENERAL: I, Area.COMERCIAL: C, Area.INVENTARIO: C}
    assert can_view_document(jefe_comercial, {"area": "comercial"})
    assert can_view_document(jefe_comercial, {"domain": "inventario"})
    assert can_view_document(jefe_comercial, {"area": "general"})
    assert not can_view_document(jefe_comercial, {"area": "finanzas"})
    assert not can_view_document(jefe_comercial, {"area": "comercial", "sensitivity": 3})
    assert not can_view_document(jefe_comercial, {"area": "general", "sensitivity": 2})
    rh = {Area.RH: R}
    assert can_view_document(rh, {"area": "rh"})
    assert not can_view_document({Area.RH: C}, {"area": "rh"})


def test_no_memberships_see_nothing_and_build_no_filter():
    assert access_filter({}) is None
    assert access_filter({Area.RH: C, Area.INVENTARIO: I}) is None
    assert not can_view_document({}, {"area": "general"})


# ── Equivalencia con Qdrant (en memoria) ─────────────────────────────────

FRAGMENTS = (
    [
        {"area": a.value, **({"sensitivity": s} if s is not None else {})}
        for a in Area
        for s in (None, 1, 2, 3)
    ]
    + [{"domain": d} for d in (*LEGACY_DOMAIN_TO_AREA, "rrhh", "comercial")]
    + [
        {"area": "comercial", "domain": "general"},  # área manda sobre el domain residual
        {"area": "sin_clasificar", "domain": "general"},
        {},
    ]
)

MEMBERSHIP_CASES = [
    {},
    {Area.GENERAL: I},
    {Area.GENERAL: I, Area.COMERCIAL: C, Area.INVENTARIO: C},
    {Area.GENERAL: I, Area.MARKETING: C, Area.INVENTARIO: I},
    {Area.RH: R, Area.GENERAL: I},
    {Area.FINANZAS: R},
    {Area.FINANZAS: C, Area.GENERAL: R},
    {area: C for area in Area} | {Area.FINANZAS: R, Area.RH: R, Area.GENERAL: I},
    {area: R for area in Area},
]


@pytest.fixture(scope="module")
def qdrant():
    client = QdrantClient(":memory:")
    client.create_collection(
        "t", vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE)
    )
    client.upsert(
        "t",
        [
            qmodels.PointStruct(
                id=str(uuid.UUID(int=i)), vector=[1.0, 0.5], payload={"metadata": md, "i": i}
            )
            for i, md in enumerate(FRAGMENTS)
        ],
    )
    return client


@pytest.mark.parametrize(
    "memberships", MEMBERSHIP_CASES, ids=lambda m: format_memberships(m) or "nada"
)
def test_qdrant_filter_matches_python_check_exactly(qdrant, memberships):
    expected = {i for i, md in enumerate(FRAGMENTS) if can_view_document(memberships, md)}
    flt = access_filter(memberships)
    if flt is None:
        assert expected == set()
        return
    points, _ = qdrant.scroll("t", scroll_filter=flt, limit=1000, with_payload=True)
    assert {p.payload["i"] for p in points} == expected


def test_matrix_covers_both_allowed_and_denied_fragments():
    # Guarda contra una prueba de equivalencia trivial (todo visible o nada visible).
    counts = [sum(can_view_document(m, md) for md in FRAGMENTS) for m in MEMBERSHIP_CASES if m]
    assert min(counts) >= 1 and max(counts) < len(FRAGMENTS)
    assert len(list(itertools.chain(FRAGMENTS))) > 50
