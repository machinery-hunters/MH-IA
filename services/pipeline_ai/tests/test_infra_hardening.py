"""
Regresiones de seguridad de infraestructura (hallazgos del diagnóstico).

Leen los archivos reales del repo (compose, Dockerfile, .gitignore): si
alguien vuelve a publicar Qdrant/Ollama al host, o a pasarle el .env a la
GUI, el CI se rompe.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = [
    "docker-compose.yml",
    "docker-compose.gpu.yml",
    "docker-compose.mac.yml",
    "docker-compose.dev.yml",
]

pytestmark = pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").exists(), reason="se ejecuta desde el repo completo"
)


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def _all_services() -> list[tuple[str, str, dict]]:
    out = []
    for f in COMPOSE_FILES:
        for svc_name, svc in (_load(f).get("services") or {}).items():
            out.append((f, svc_name, svc))
    return out


@pytest.mark.parametrize(("file", "name", "svc"), _all_services())
def test_published_ports_only_on_loopback(file, name, svc):
    for port in svc.get("ports", []):
        assert str(port).startswith("127.0.0.1:"), f"{file}:{name} publica {port} fuera de loopback"


@pytest.mark.parametrize(("file", "name", "svc"), _all_services())
def test_qdrant_and_ollama_never_publish_ports(file, name, svc):
    if name in {"qdrant", "ollama"}:
        assert not svc.get("ports"), f"{file}:{name} no debe publicar puertos"


def test_web_gets_no_backend_secrets_and_no_backend_network():
    web = _load("docker-compose.yml")["services"]["web"]
    assert "env_file" not in web
    assert web["networks"] == ["edge"]


def test_backend_services_are_not_on_the_edge_network_except_api():
    services = _load("docker-compose.yml")["services"]
    assert "edge" not in services["qdrant"]["networks"]
    assert set(services["mhia-api"]["networks"]) == {"backend", "edge"}


def test_api_dockerfile_does_not_run_as_root():
    dockerfile = (ROOT / "services/pipeline_ai/Dockerfile").read_text(encoding="utf-8")
    user_lines = [ln for ln in dockerfile.splitlines() if ln.startswith("USER ")]
    assert user_lines and user_lines[-1].split()[1] not in {"root", "0"}


def test_secrets_are_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "credentials/*.json" in ignored


def test_env_example_has_no_real_looking_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "sk-ant-xxxxx" in text  # placeholder explícito
    for line in text.splitlines():
        if line.startswith(("API_SESSION_SECRET=", "GHL_API_TOKEN=", "SUPABASE_SERVICE_KEY=")):
            assert (
                line.split("=", 1)[1] == ""
            ), f"valor no vacío en .env.example: {line.split('=')[0]}"
