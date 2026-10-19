import asyncio

import pytest

from pipeline_ai.agents.subagent import run_subagent
from pipeline_ai.agents.supervisor import consolidate, should_abort
from pipeline_ai.config import settings
from pipeline_ai.connectors.base import ConnectorSpec, RawExtraction
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.sources import SourceResult, SourceStatus

RAW = "Cliente Acme S.A. debe $1,250,000.00 por el contrato 034"


class FakeConnector:
    def __init__(self, behavior="ok", sensitivity=Sensitivity.CONFIDENTIAL, required=()):
        self.behavior = behavior
        self.spec = ConnectorSpec(
            name="fake",
            display_name="Fake",
            default_sensitivity=sensitivity,
            required_settings=required,
            timeout_setting="timeout_erp_s",
        )

    async def extract(self, query: str) -> RawExtraction:
        if self.behavior == "slow":
            await asyncio.sleep(5)
        if self.behavior == "boom":
            raise ValueError(f"fallo con datos: {RAW}")
        return RawExtraction(text=RAW)


async def anonymize_ok(text: str) -> str:
    return "Cliente_A debe Monto_X por el Folio_X"


async def anonymize_boom(text: str) -> str:
    raise RuntimeError(f"ollama caído procesando: {text}")


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(settings, "timeout_erp_s", 0.2)


async def test_ok_confidential_returns_only_anonymized_text():
    r = await run_subagent(FakeConnector(), "q", role="lider", anonymize=anonymize_ok)
    assert r.status is SourceStatus.OK
    assert r.context == "Cliente_A debe Monto_X por el Folio_X"
    assert "Acme" not in r.context and "1,250,000" not in r.context


async def test_internal_level_passes_through_without_anonymizer():
    called = False

    async def spy(text: str) -> str:
        nonlocal called
        called = True
        return "x"

    r = await run_subagent(
        FakeConnector(sensitivity=Sensitivity.INTERNAL), "q", role="consulta", anonymize=spy
    )
    assert r.ok and r.context == RAW and not called


async def test_timeout_is_isolated():
    r = await run_subagent(FakeConnector("slow"), "q", role="lider", anonymize=anonymize_ok)
    assert r.status is SourceStatus.TIMEOUT


async def test_exception_does_not_escape_and_does_not_leak_message():
    r = await run_subagent(FakeConnector("boom"), "q", role="lider", anonymize=anonymize_ok)
    assert r.status is SourceStatus.UNAVAILABLE
    assert r.detail == "ValueError"
    assert "Acme" not in r.detail and r.context == ""


async def test_anonymizer_failure_fails_closed_never_returns_raw():
    r = await run_subagent(FakeConnector(), "q", role="lider", anonymize=anonymize_boom)
    assert r.status is SourceStatus.UNAVAILABLE
    assert r.context == ""
    assert "Acme" not in r.detail and "1,250,000" not in r.detail


async def test_missing_credentials_is_not_configured_and_does_not_connect():
    r = await run_subagent(
        FakeConnector("boom", required=("erpnext_api_key",)),
        "q",
        role="lider",
        anonymize=anonymize_ok,
    )
    # sin credenciales, `boom` ni siquiera se ejecuta
    assert r.status is SourceStatus.NOT_CONFIGURED


async def test_role_without_permission_is_forbidden_before_querying():
    r = await run_subagent(FakeConnector("boom"), "q", role="consulta", anonymize=anonymize_ok)
    assert r.status is SourceStatus.FORBIDDEN


async def test_unknown_role_is_forbidden():
    r = await run_subagent(FakeConnector(), "q", role=None, anonymize=anonymize_ok)
    assert r.status is SourceStatus.FORBIDDEN


def _res(status, context=""):
    return SourceResult(source="s", status=status, context=context)


def test_supervisor_continues_with_partial_sources():
    results = [
        _res(SourceStatus.OK, "dato"),
        _res(SourceStatus.TIMEOUT),
        _res(SourceStatus.NOT_CONFIGURED),
    ]
    ok, unavailable = consolidate(results)
    assert len(ok) == 1 and len(unavailable) == 2
    assert not should_abort(results)


def test_supervisor_aborts_when_no_source_has_evidence():
    assert should_abort([_res(SourceStatus.TIMEOUT), _res(SourceStatus.OK, "   ")])
    assert should_abort([])
