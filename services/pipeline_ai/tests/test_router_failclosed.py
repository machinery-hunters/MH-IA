from types import SimpleNamespace

import pytest

from pipeline_ai.config import settings
from pipeline_ai.nodes import router


class FakeLLM:
    def __init__(self, content):
        self.content = content

    async def ainvoke(self, _prompt):
        return SimpleNamespace(content=self.content)


async def _run(monkeypatch, content):
    monkeypatch.setattr(router, "_get_router_llm", lambda: FakeLLM(content))
    return await router.router_node({"query": "algo"})


def test_fail_closed_domain_is_confidential():
    assert router._FAIL_CLOSED_DOMAIN in settings.confidential_domains
    assert router._FAIL_CLOSED_DOMAIN in router._VALID_DOMAINS


@pytest.mark.parametrize(
    "content",
    [
        "esto no es json",
        "[]",
        "null",
        '{"data_domain": "recursos_humanos"}',  # JSON válido, dominio inventado
        '{"data_domain": ""}',
        '{"data_domain": 42}',
        '{"otra_clave": "x"}',  # JSON válido sin la clave esperada
    ],
)
async def test_invalid_classifier_output_is_treated_as_confidential(monkeypatch, content):
    result = await _run(monkeypatch, content)
    assert result["is_confidential"] is True


async def test_valid_general_domain_is_not_confidential(monkeypatch):
    result = await _run(monkeypatch, '{"data_domain": "general", "reason": "x"}')
    assert result["data_domain"] == "general"
    assert result["is_confidential"] is False
