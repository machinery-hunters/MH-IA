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
    "docker-compose.host-ollama.yml",
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
def test_qdrant_postgres_and_ollama_never_publish_ports(file, name, svc):
    if name in {"qdrant", "postgres", "ollama"}:
        assert not svc.get("ports"), f"{file}:{name} no debe publicar puertos"


def test_web_gets_no_backend_secrets_and_no_backend_network():
    web = _load("docker-compose.yml")["services"]["web"]
    assert "env_file" not in web
    assert web["networks"] == ["edge"]


def test_backend_services_are_not_on_the_edge_network_except_api():
    services = _load("docker-compose.yml")["services"]
    assert "edge" not in services["qdrant"]["networks"]
    assert services["postgres"]["networks"] == ["backend"]
    assert set(services["mhia-api"]["networks"]) == {"backend", "edge"}


def test_postgres_gets_only_its_own_variables_and_the_api_uses_it():
    services = _load("docker-compose.yml")["services"]
    pg = services["postgres"]
    assert "env_file" not in pg  # nunca recibe ANTHROPIC_API_KEY ni el resto del .env
    names = {item.split("=", 1)[0] for item in pg["environment"]}
    assert names == {"POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_INITDB_ARGS"}
    assert "scram-sha-256" in next(i for i in pg["environment"] if "INITDB" in i)
    assert "@sha256:" in pg["image"]
    api_env = services["mhia-api"]["environment"]
    assert "AUTH_BACKEND=postgres" in api_env
    assert services["mhia-api"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_api_dockerfile_does_not_run_as_root():
    dockerfile = (ROOT / "services/pipeline_ai/Dockerfile").read_text(encoding="utf-8")
    user_lines = [ln for ln in dockerfile.splitlines() if ln.startswith("USER ")]
    assert user_lines and user_lines[-1].split()[1] not in {"root", "0"}


def test_env_example_starts_in_the_free_local_reasoning_mode():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "REASONING_MODE=local" in lines


def test_secrets_are_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
    assert "credentials/*.json" in ignored


def test_env_example_has_no_real_looking_secrets():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    secrets = (
        "ANTHROPIC_API_KEY=",
        "API_SESSION_SECRET=",
        "GHL_API_TOKEN=",
        "SUPABASE_SERVICE_KEY=",
        "POSTGRES_PASSWORD=",
    )
    for line in text.splitlines():
        if line.startswith(secrets):
            assert (
                line.split("=", 1)[1] == ""
            ), f"valor no vacío en .env.example: {line.split('=')[0]}"


# ── Hook local pre-push (protección de main/develop) ─────────────────────
import shutil  # noqa: E402
import subprocess  # noqa: E402

HOOK = ROOT / ".githooks" / "pre-push"
needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="requiere sh")


def _run_hook(remote_ref: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = {k: v for k, v in os.environ.items() if k != "MHIA_ALLOW_PROTECTED_PUSH"}
    env.update(env_extra or {})
    line = f"refs/heads/work abc123 {remote_ref} def456\n"
    return subprocess.run(
        ["sh", str(HOOK), "origin", "https://example.invalid/repo.git"],
        input=line,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_pre_push_hook_file_uses_lf_line_endings():
    assert b"\r" not in HOOK.read_bytes()


@needs_sh
@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/heads/develop"])
def test_pre_push_blocks_direct_push_to_protected_branches(ref):
    result = _run_hook(ref)
    assert result.returncode == 1
    assert "bloqueado" in result.stderr


@needs_sh
@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/feature/conector-firestore",
        "refs/heads/security/outbound-gateway",
        "refs/heads/maintenance",  # prefijo parecido, no es main
        "refs/tags/v0.2.0",
    ],
)
def test_pre_push_allows_work_branches_and_tags(ref):
    assert _run_hook(ref).returncode == 0


@needs_sh
def test_pre_push_override_requires_explicit_env_var():
    assert _run_hook("refs/heads/main", {"MHIA_ALLOW_PROTECTED_PUSH": "1"}).returncode == 0
    assert _run_hook("refs/heads/main", {"MHIA_ALLOW_PROTECTED_PUSH": "0"}).returncode == 1


@needs_sh
def test_pre_push_blocks_deleting_protected_branches():
    result = subprocess.run(
        ["sh", str(HOOK), "origin", "url"],
        input="(delete) 0000000000000000000000000000000000000000 refs/heads/main def456\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1


def test_pre_push_hook_is_executable_in_git_index():
    """En macOS/Linux git ignora un hook sin bit de ejecución (caso Mac mini).
    Se activa con: git update-index --chmod=+x .githooks/pre-push"""
    result = subprocess.run(
        ["git", "ls-files", "-s", ".githooks/pre-push"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        pytest.skip("el hook aún no está en el índice de git (o no hay git)")
    assert result.stdout.startswith(
        "100755"
    ), "falta: git update-index --chmod=+x .githooks/pre-push"
