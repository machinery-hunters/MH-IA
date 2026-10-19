"""Outbound gateway: verificación, alias de fuentes, bloqueo y auditoría sin contenido."""

import json
import logging

import pytest

from pipeline_ai.privacy.outbound_gateway import (
    OutboundBlocked,
    OutboundPayload,
    assign_source_aliases,
    prepare_outbound,
)
from pipeline_ai.privacy.sensitivity import Sensitivity

REAL_1 = "Google Drive: Contrato_Consignacion_034.pdf"
REAL_2 = "Google Drive: Balance_2025.xlsx"
CITATIONS = [
    {"source": REAL_1, "chunk_id": "c1", "score": 0.91},
    {"source": REAL_2, "chunk_id": "c2", "score": 0.8},
    {"source": REAL_1, "chunk_id": "c3", "score": 0.7},
]
ALIASES = {"Fuente_1": REAL_1, "Fuente_2": REAL_2}
QUERY = "¿Cuánto debe Cliente_A según Fuente_1?"
CONTEXT = (
    "[Fuente: Fuente_1]\nCliente_A debe Monto_X por el Folio_X\n\n---\n\n"
    "[Fuente: Fuente_2]\nBalance de Cliente_B"
)


def _prepare(**overrides):
    args = {
        "query": QUERY,
        "context": CONTEXT,
        "citations": CITATIONS,
        "sensitivity": Sensitivity.CONFIDENTIAL,
        "anonymized": True,
        "source_aliases": ALIASES,
    }
    args.update(overrides)
    return prepare_outbound(**args)


def _blocked_reasons(**overrides) -> tuple[str, ...]:
    with pytest.raises(OutboundBlocked) as exc:
        _prepare(**overrides)
    return exc.value.reasons


# -- Camino feliz --------------------------------------------------------------


def test_payload_carries_aliases_and_no_real_source_names():
    payload = _prepare()
    blob = "\n".join([payload.query, payload.context, *payload.source_lines])
    assert "Contrato_Consignacion" not in blob and "Balance_2025" not in blob
    # Una línea por fuente, con el mejor score, aunque haya varios chunks.
    assert payload.source_lines == ("- Fuente_1 (score=0.91)", "- Fuente_2 (score=0.80)")


def test_aliases_are_derived_from_citations_when_not_provided():
    payload = _prepare(source_aliases=None)
    assert dict(payload.alias_map) == ALIASES


def test_assign_source_aliases_is_stable_and_deduplicated():
    assert assign_source_aliases(["a", "b", "a", "c"]) == {
        "a": "Fuente_1",
        "b": "Fuente_2",
        "c": "Fuente_3",
    }


def test_restore_relabels_aliases_with_real_names_locally():
    payload = _prepare()
    out = payload.restore("Según [Fuente: Fuente_1] y Fuente_2; Fuente_10 no existe")
    assert REAL_1 in out and REAL_2 in out
    assert "Fuente_10" in out  # alias desconocido: se deja tal cual


def test_repr_does_not_expose_the_alias_map():
    text = repr(_prepare())
    assert "Contrato" not in text and "Balance_2025" not in text


def test_internal_level_does_not_need_anonymization_but_is_still_scanned():
    payload = _prepare(sensitivity=Sensitivity.INTERNAL, anonymized=False)
    assert payload.sensitivity is Sensitivity.INTERNAL
    assert "consulta:telefono" in _blocked_reasons(
        sensitivity=Sensitivity.INTERNAL, anonymized=False, query="mi tel 653 133 2053"
    )


# -- Nombres de fuente ------------------------------------------------------------


def test_real_source_names_left_in_the_text_are_replaced_by_aliases():
    payload = _prepare(
        query="¿Qué dice Contrato_Consignacion_034.pdf?",
        context=CONTEXT
        + "\nVer Google Drive: Contrato_Consignacion_034.pdf y contrato_consignacion_034",
    )
    blob = (payload.query + payload.context).lower()
    assert "contrato_consignacion" not in blob
    assert payload.query == "¿Qué dice Fuente_1?"


def test_accent_variant_of_a_source_name_is_blocked_not_sent():
    citations = [{"source": "Google Drive: Informe_Año.pdf", "chunk_id": "c", "score": 0.9}]
    reasons = _blocked_reasons(
        query="¿Qué dice Fuente_1?",
        context="[Fuente: Fuente_1]\nSegún informe_ano el equipo está vendido",
        citations=citations,
        source_aliases=None,
    )
    assert "contexto:nombre_conocido" in reasons


def test_extra_known_names_are_blocked():
    reasons = _blocked_reasons(known_names=["Acme"], context=CONTEXT + "\nAcme es cliente")
    assert "contexto:nombre_conocido" in reasons


# -- Bloqueos ---------------------------------------------------------------------


def test_pii_in_the_query_blocks():
    assert "consulta:email" in _blocked_reasons(query="¿Qué debe juan@acme.com?")


def test_pii_in_the_context_blocks():
    assert "contexto:rfc" in _blocked_reasons(context=CONTEXT + "\nRFC AAA010101AB1")


def test_confidential_without_verified_anonymization_blocks():
    assert "anonimizacion_no_verificada" in _blocked_reasons(anonymized=False)


def test_restricted_never_leaves_even_if_anonymized():
    assert "sensibilidad_restringida" in _blocked_reasons(sensitivity=Sensitivity.RESTRICTED)


def test_empty_query_or_context_blocks():
    assert "contexto_vacio" in _blocked_reasons(context="   ")
    assert "consulta_vacia" in _blocked_reasons(query="")


def test_blocked_error_carries_only_reasons_never_content():
    reasons = _blocked_reasons(query="¿Qué debe juan.perez@acme.com?")
    assert reasons == ("consulta:email",)
    with pytest.raises(OutboundBlocked) as exc:
        _prepare(query="¿Qué debe juan.perez@acme.com?")
    assert "juan" not in str(exc.value) and "acme" not in str(exc.value)


# -- Candado del constructor ----------------------------------------------------------


def test_payload_cannot_be_forged_outside_the_gateway():
    with pytest.raises(PermissionError):
        OutboundPayload(
            query="x",
            context="y",
            source_lines=(),
            sensitivity=Sensitivity.PUBLIC,
            audit_hash="h",
            alias_map={},
        )


# -- Auditoría -----------------------------------------------------------------------


def test_audit_log_has_hash_and_sizes_but_never_content(caplog):
    caplog.set_level(logging.INFO, logger="mhia.audit")
    payload = _prepare(session_id="s-1")
    message = caplog.records[-1].getMessage()
    record = json.loads(message)
    assert record["event"] == "outbound_prepared"
    assert record["sha256"] == payload.audit_hash
    assert record["sensitivity"] == "CONFIDENTIAL"
    assert record["session_id"] == "s-1"
    assert record["n_sources"] == 2
    for fragment in ("Cliente_A", "Monto_X", "Contrato", "Balance"):
        assert fragment not in message


def test_blocked_attempts_are_audited_without_content(caplog):
    caplog.set_level(logging.INFO, logger="mhia.audit")
    _blocked_reasons(query="¿Qué debe juan.perez@acme.com?")
    message = caplog.records[-1].getMessage()
    record = json.loads(message)
    assert record["event"] == "outbound_blocked"
    assert record["reasons"] == ["consulta:email"]
    assert "juan" not in message and "acme" not in message


def test_hash_is_stable_and_changes_with_the_content():
    first, second = _prepare(), _prepare()
    other = _prepare(context=CONTEXT + "\nOtra línea")
    assert first.audit_hash == second.audit_hash
    assert len(first.audit_hash) == 64
    assert other.audit_hash != first.audit_hash
