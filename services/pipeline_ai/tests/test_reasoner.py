"""Nodo de razonamiento: REASONING_MODE, caída de Claude al modelo local y auditoría."""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline_ai.nodes.claude_node as claude_mod
import pipeline_ai.nodes.local_reasoner as local_mod
from pipeline_ai.config import settings
from pipeline_ai.nodes.local_reasoner import LocalReasonerError, reason_locally
from pipeline_ai.nodes.reasoner_node import reasoner_node
from pipeline_ai.nodes.reasoning_prompt import as_text
from pipeline_ai.privacy.outbound_gateway import prepare_outbound
from pipeline_ai.privacy.sensitivity import Sensitivity

REAL = "Google Drive: Contrato_Consignacion_034.pdf"
REPLY = "[EMPRESA_1] debe [MONTO_1] [Fuente: Fuente_1]"


class FakeModel:
    def __init__(self, reply=REPLY, *, error=None, delay=0.0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content=self.reply)


def _payload():
    return prepare_outbound(
        query="¿Cuánto debe [EMPRESA_1]?",
        context="[Fuente: Fuente_1]\n[EMPRESA_1] debe [MONTO_1]",
        citations=[{"source": REAL, "chunk_id": "c1", "score": 0.9}],
        sensitivity=Sensitivity.CONFIDENTIAL,
        anonymized=True,
        placeholder_map={"[EMPRESA_1]": "Acme S.A.", "[MONTO_1]": "$1,250,000.00"},
    )


@pytest.fixture
def models(monkeypatch):
    claude, local = FakeModel(), FakeModel()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-fake-para-pruebas")
    monkeypatch.setattr(claude_mod, "_get_claude", lambda: claude)
    monkeypatch.setattr(local_mod, "_get_local_reasoner", lambda: local)
    return SimpleNamespace(claude=claude, local=local)


# -- Modo por defecto -----------------------------------------------------------------------


def test_local_is_the_default_mode(tmp_path):
    """Sin REASONING_MODE ni OLLAMA_REASONER_MODEL: modelo local, el mismo modelo de trabajo.
    En un proceso aparte (y desde una carpeta sin .env) para leer los valores por defecto."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("REASONING_MODE", "OLLAMA_REASONER_MODEL", "OLLAMA_LLM_MODEL")
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    code = (
        "from pipeline_ai.config import settings as s;"
        "print(s.reasoning_mode, s.ollama_reasoner_model == s.ollama_llm_model)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["local", "True"]


async def test_local_mode_never_calls_claude(monkeypatch, models):
    monkeypatch.setattr(settings, "reasoning_mode", "local")

    result = await reasoner_node({"outbound": _payload(), "session_id": "s1"})

    assert models.claude.calls == []
    assert len(models.local.calls) == 1
    assert result["reasoner"] == "local"
    # Se restaura igual que con Claude: dato real y nombre real del archivo.
    assert result["final_answer"] == f"Acme S.A. debe $1,250,000.00 [Fuente: {REAL}]"


async def test_claude_mode_uses_claude_and_not_the_local_model(monkeypatch, models):
    monkeypatch.setattr(settings, "reasoning_mode", "claude")

    result = await reasoner_node({"outbound": _payload(), "session_id": "s1"})

    assert len(models.claude.calls) == 1
    assert models.local.calls == []
    assert result["reasoner"] == "claude"


async def test_both_providers_receive_the_same_messages(monkeypatch, models):
    payload = _payload()
    monkeypatch.setattr(settings, "reasoning_mode", "claude")
    await reasoner_node({"outbound": payload})
    monkeypatch.setattr(settings, "reasoning_mode", "local")
    await reasoner_node({"outbound": payload})

    assert models.claude.calls == models.local.calls


# -- Caída de Claude al modelo local ------------------------------------------------------------


class CreditExhausted(Exception):
    pass


async def test_claude_failure_falls_back_to_the_local_model(monkeypatch, models, caplog):
    monkeypatch.setattr(settings, "reasoning_mode", "claude")
    models.claude.error = CreditExhausted("Your credit balance is too low: [EMPRESA_1]")

    with caplog.at_level(logging.INFO):
        result = await reasoner_node({"outbound": _payload(), "session_id": "s1"})

    assert len(models.claude.calls) == 1 and len(models.local.calls) == 1
    assert result["reasoner"] == "local"
    assert result["aborted"] is False
    assert "Acme S.A." in result["final_answer"]
    logs = caplog.text
    assert "fallback_from=CreditExhausted" in logs
    # Solo el tipo: el mensaje de la API no se registra.
    assert "credit balance" not in logs and "EMPRESA_1" not in logs


async def test_missing_api_key_in_claude_mode_also_falls_back(monkeypatch, models):
    monkeypatch.setattr(settings, "reasoning_mode", "claude")
    models.claude.error = RuntimeError("Faltan variables de entorno requeridas")

    result = await reasoner_node({"outbound": _payload()})

    assert result["reasoner"] == "local"


async def test_if_both_providers_fail_the_error_carries_no_content(monkeypatch, models):
    monkeypatch.setattr(settings, "reasoning_mode", "claude")
    models.claude.error = CreditExhausted("sin crédito")
    models.local.error = ConnectionError("ollama caído leyendo Acme S.A.")

    with pytest.raises(LocalReasonerError) as excinfo:
        await reasoner_node({"outbound": _payload()})

    assert "Acme" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


# -- Modelo local ---------------------------------------------------------------------------


async def test_a_slow_local_model_times_out(monkeypatch, models):
    monkeypatch.setattr(settings, "reasoner_timeout_s", 0.05)
    models.local.delay = 1.0

    with pytest.raises(LocalReasonerError, match="tiempo"):
        await reason_locally(_payload())


async def test_an_empty_local_answer_is_an_error(models):
    models.local.reply = "<think>no sé</think>   "

    with pytest.raises(LocalReasonerError, match="vacía"):
        await reason_locally(_payload())


def test_thinking_blocks_are_not_shown_to_the_user():
    raw = (
        "<think>\nEl usuario pregunta por [EMPRESA_1]...\n</think>\n\nRespuesta [Fuente: Fuente_1]"
    )
    assert as_text(raw) == "Respuesta [Fuente: Fuente_1]"
    assert as_text([{"type": "text", "text": "<THINK>x</THINK>Hola"}]) == "Hola"


def test_the_local_reasoner_uses_the_configured_model(monkeypatch):
    monkeypatch.setattr(settings, "ollama_reasoner_model", "qwen3:30b-a3b")
    monkeypatch.setattr(settings, "reasoner_num_ctx", 8192)
    local_mod._get_local_reasoner.cache_clear()
    try:
        llm = local_mod._get_local_reasoner()
        assert llm.model == "qwen3:30b-a3b"
        assert llm.num_ctx == 8192
        assert llm.base_url == settings.ollama_base_url
    finally:
        local_mod._get_local_reasoner.cache_clear()


# -- Configuración y auditoría -------------------------------------------------------------


@pytest.mark.parametrize("value", ["cloud", "", "anthropic"])
async def test_an_unknown_mode_fails_instead_of_guessing(monkeypatch, models, value):
    monkeypatch.setattr(settings, "reasoning_mode", value)

    with pytest.raises(RuntimeError, match="REASONING_MODE"):
        await reasoner_node({"outbound": _payload()})
    assert models.claude.calls == [] and models.local.calls == []


def test_claude_mode_requires_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "reasoning_mode", "claude")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    with pytest.raises(RuntimeError, match="anthropic_api_key"):
        settings.require_reasoning_mode()


def test_local_mode_does_not_require_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "reasoning_mode", "local")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    assert settings.require_reasoning_mode() == "local"


async def test_reasoning_is_audited_with_the_payload_hash_and_no_text(monkeypatch, models, caplog):
    monkeypatch.setattr(settings, "reasoning_mode", "local")
    payload = _payload()

    with caplog.at_level(logging.INFO, logger="mhia.audit"):
        await reasoner_node({"outbound": payload, "session_id": "sesion-7"})

    lines = [r.getMessage() for r in caplog.records if r.name == "mhia.audit"]
    done = [line for line in lines if line.startswith("reasoning_done")]
    assert len(done) == 1
    assert "session=sesion-7" in done[0] and f"audit_hash={payload.audit_hash}" in done[0]
    assert "provider=local" in done[0] and "fallback_from=-" in done[0]
    assert "Acme" not in done[0] and "EMPRESA" not in done[0] and "Contrato" not in done[0]
