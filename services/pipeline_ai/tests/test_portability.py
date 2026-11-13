"""
Paridad laptop Windows ↔ Mac mini (bloque 1 del plan maestro).

Leen los archivos reales del repo: si alguien vuelve a usar una imagen sin
digest, instala dependencias sin hash, mete una ruta de Windows en el código
o rompe los scripts de la Mac, el CI falla antes de que llegue a producción.
"""

import plistlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
PIPELINE = ROOT / "services" / "pipeline_ai"
MAC = ROOT / "scripts" / "mac"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")

pytestmark = pytest.mark.skipif(
    not (ROOT / "docker-compose.yml").exists(), reason="se ejecuta desde el repo completo"
)


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def _code_lines(path: Path) -> str:
    """Contenido sin comentarios de línea completa (los comentarios pueden advertir
    justamente contra lo que se prohíbe)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith(("#", "<!--")))


def _git_ls(*paths: str) -> str:
    result = subprocess.run(
        ["git", "ls-files", "-s", *paths], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        pytest.skip("sin git")
    return result.stdout


# ── Versiones fijas ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "file",
    [
        "docker-compose.yml",
        "docker-compose.gpu.yml",
        "docker-compose.host-ollama.yml",
        "docker-compose.dev.yml",
    ],
)
def test_compose_images_are_pinned_by_version_and_digest(file):
    for name, svc in (_compose(file).get("services") or {}).items():
        image = svc.get("image")
        if image is None:
            continue
        assert DIGEST.search(image), f"{file}:{name} usa {image} sin @sha256"
        tag = image.split("@")[0].rsplit(":", 1)
        assert len(tag) == 2 and tag[1] != "latest", f"{file}:{name} sin versión legible"


@pytest.mark.parametrize("dockerfile", ["services/pipeline_ai/Dockerfile", "web/Dockerfile"])
def test_dockerfile_base_images_are_pinned_by_digest(dockerfile):
    lines = (ROOT / dockerfile).read_text(encoding="utf-8").splitlines()
    froms = [ln.split()[1] for ln in lines if ln.startswith("FROM ")]
    assert froms
    for image in froms:
        assert DIGEST.search(image), f"{dockerfile}: FROM {image} sin @sha256"


def test_ci_tools_are_pinned():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert ":latest" not in ci
    assert re.search(r"zricethezav/gitleaks:v[\d.]+@sha256:[0-9a-f]{64}", ci)


def test_ci_scans_dependencies_and_images():
    """Bloque 6: pip-audit, npm audit y Trivy rompen el CI con avisos corregibles."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(ci)
    assert {"deps-audit", "images-scan"} <= set(workflow["jobs"])
    # pip-audit fijado con hash y sobre los dos lockfiles.
    assert "--require-hashes -r .github/herramientas/pip-audit.lock" in ci
    assert (
        (ROOT / ".github/herramientas/pip-audit.lock")
        .read_text(encoding="utf-8")
        .count("--hash=sha256:")
    )
    for lock in ("requirements.lock", "requirements-dev.lock"):
        assert f"pip-audit -r {lock} --require-hashes" in ci
    assert "npm audit --omit=dev --audit-level=moderate" in ci
    assert "npm audit --audit-level=high" in ci
    # Trivy fijado por digest, sobre ambas imágenes, y falla de verdad.
    assert re.search(r"aquasec/trivy:[\d.]+@sha256:[0-9a-f]{64}", ci)
    assert "--exit-code 1" in ci and "--severity HIGH,CRITICAL" in ci
    assert "mhia-api:ci mhia-web:ci" in ci
    assert (ROOT / ".trivyignore").exists()
    # Corre también cada semana aunque nadie suba cambios (PyYAML lee la clave "on" como True).
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"]


def test_images_get_security_patches_and_drop_unused_tools():
    api = (PIPELINE / "Dockerfile").read_text(encoding="utf-8")
    # Un apt-get update fallido debe romper el build, no dejar paquetes sin parchar.
    assert "apt-get update --error-on=any" in api and "apt-get upgrade -y" in api
    web = (ROOT / "web/Dockerfile").read_text(encoding="utf-8")
    run_stage = web.split(" AS run")[1]
    assert "rm -rf /usr/local/lib/node_modules/npm" in run_stage
    assert "/usr/local/bin/npx" in run_stage


def test_dependabot_watches_images_actions_and_npm():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u["package-ecosystem"] for u in config["updates"]}
    assert {"docker", "docker-compose", "github-actions", "npm"} <= ecosystems
    assert all(u["target-branch"] == "develop" for u in config["updates"])


def _lock_pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def _direct_requirements(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return names


def test_lockfiles_cover_every_direct_requirement_with_hashes():
    runtime = _lock_pins(PIPELINE / "requirements.lock")
    dev = _lock_pins(PIPELINE / "requirements-dev.lock")
    for name in _direct_requirements(PIPELINE / "requirements.txt"):
        assert name in runtime, f"{name} falta en requirements.lock: corre make lock"
    for name in _direct_requirements(PIPELINE / "requirements-dev.txt"):
        assert name in dev, f"{name} falta en requirements-dev.lock: corre make lock"
    for lock in ("requirements.lock", "requirements-dev.lock"):
        text = (PIPELINE / lock).read_text(encoding="utf-8")
        assert "--hash=sha256:" in text, f"{lock} sin hashes"


def test_runtime_and_dev_locks_agree_on_versions():
    """El CI prueba con requirements-dev.lock y la imagen usa requirements.lock: si
    difieren, lo probado no es lo desplegado."""
    runtime = _lock_pins(PIPELINE / "requirements.lock")
    dev = _lock_pins(PIPELINE / "requirements-dev.lock")
    mismatched = {n: (v, dev[n]) for n, v in runtime.items() if n in dev and dev[n] != v}
    assert not mismatched, f"versiones distintas entre locks: {mismatched}"
    assert set(runtime) <= set(dev)


def test_api_image_installs_only_hashed_lock():
    dockerfile = (PIPELINE / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.lock" in dockerfile
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert "requirements.txt" not in dockerfile


def test_ci_and_windows_venv_install_from_lock():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements-dev.lock" in ci
    dev_ps1 = (ROOT / "scripts/dev.ps1").read_text(encoding="utf-8-sig")
    assert '--require-hashes -r (Join-Path $Pipeline "requirements-dev.lock")' in dev_ps1


def test_qdrant_server_and_client_share_minor_version():
    image = _compose("docker-compose.yml")["services"]["qdrant"]["image"]
    server = re.search(r":v(\d+)\.(\d+)\.", image).groups()
    client = _lock_pins(PIPELINE / "requirements.lock")["qdrant-client"].split(".")[:2]
    assert list(server) == client


# ── Ollama: mismo modo y mismos modelos en ambos equipos ─────────────────


def _models_file() -> tuple[str, dict[str, str]]:
    version, models = "", {}
    for line in (ROOT / "scripts/ollama-models.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split()
        if key == "ollama":
            version = value
        else:
            models[key] = value
    return version, models


def test_models_file_lists_configured_models_with_ids():
    version, models = _models_file()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert all(re.fullmatch(r"[0-9a-f]{12}", v) for v in models.values())
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for var in ("OLLAMA_LLM_MODEL", "OLLAMA_EMBED_MODEL"):
        value = re.search(rf"^{var}=(\S+)", env_example, re.M).group(1)
        tag = value if ":" in value else f"{value}:latest"
        assert tag in models, f"{var}={value} no está en scripts/ollama-models.txt"


def test_container_ollama_matches_native_version():
    digest_image = _compose("docker-compose.gpu.yml")["services"]["ollama"]["image"]
    version, _ = _models_file()
    assert digest_image.startswith(f"ollama/ollama:{version}@")


def test_base_and_host_mode_never_run_ollama_in_a_container_or_need_nvidia():
    for file in ("docker-compose.yml", "docker-compose.host-ollama.yml"):
        text = _code_lines(ROOT / file)
        assert "ollama" not in (_compose(file).get("services") or {})
        assert "nvidia" not in text.lower()


def test_host_mode_reaches_host_ollama_with_overridable_gateway():
    api = _compose("docker-compose.host-ollama.yml")["services"]["mhia-api"]
    assert "OLLAMA_BASE_URL=http://host.docker.internal:11434" in api["environment"]
    assert api["extra_hosts"] == ["host.docker.internal:${MHIA_HOST_GATEWAY:-host-gateway}"]


def test_windows_defaults_to_native_ollama():
    dev_ps1 = (ROOT / "scripts/dev.ps1").read_text(encoding="utf-8-sig")
    assert '[string]$OllamaMode = "host"' in dev_ps1
    assert '"-f", "docker-compose.host-ollama.yml"' in dev_ps1


def test_no_file_binds_ollama_outside_loopback():
    offenders = []
    for path in [*ROOT.glob("docker-compose*.yml"), *MAC.rglob("*"), ROOT / ".env.example"]:
        if path.is_file() and re.search(r"OLLAMA_HOST[=\s\"'<>/a-z]*0\.0\.0\.0", _code_lines(path)):
            offenders.append(path.name)
    assert not offenders


# ── Código y archivos portables ──────────────────────────────────────────


def test_python_code_has_no_windows_paths_or_cuda_assumptions():
    pattern = re.compile(r"[A-Za-z]:\\\\|C:/Users|\bcuda\b|nvidia-smi", re.I)
    offenders = [
        str(p.relative_to(PIPELINE))
        for p in (PIPELINE / "pipeline_ai").rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders


def test_powershell_scripts_have_utf8_bom_for_windows_powershell_51():
    for ps1 in (ROOT / "scripts").glob("*.ps1"):
        assert ps1.read_bytes().startswith(b"\xef\xbb\xbf"), f"{ps1.name} sin BOM UTF-8"


def test_gitattributes_forces_lf_for_scripts_and_marks_binaries():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for rule in ("* text=auto eol=lf", "*.sh text eol=lf", "*.docx binary", "*.png binary"):
        assert rule in attrs


@pytest.mark.parametrize("script", sorted(p.name for p in MAC.glob("*.sh")))
def test_mac_scripts_are_lf_strict_and_executable(script):
    path = MAC / script
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.startswith(b"#!/bin/bash\n")
    assert b"set -euo pipefail" in data or b"set -uo pipefail" in data
    staged = _git_ls(f"scripts/mac/{script}")
    if not staged.strip():
        pytest.skip("aún no está en el índice de git")
    assert staged.startswith("100755"), f"git update-index --chmod=+x scripts/mac/{script}"


def test_mac_scripts_avoid_bash4_features():
    """macOS trae bash 3.2: sin arreglos asociativos, mapfile ni ${var,,}."""
    for path in MAC.glob("*.sh"):
        text = path.read_text(encoding="utf-8")
        for feature in ("declare -A", "mapfile", "readarray", ",,}", "^^}"):
            assert feature not in text, f"{path.name} usa {feature}"


# ── LaunchDaemons de la Mac ──────────────────────────────────────────────

PLISTS = sorted((MAC / "launchd").glob("*.plist"))


def test_expected_launchd_services_exist():
    labels = {p.stem for p in PLISTS}
    assert labels == {
        f"com.machineryhunters.mhia.{s}" for s in ("ollama", "colima", "backup", "health")
    }
    setup = (MAC / "setup.sh").read_text(encoding="utf-8")
    assert 'SERVICES="ollama colima backup health"' in setup


@pytest.mark.parametrize("plist", PLISTS, ids=lambda p: p.stem)
def test_launchd_plists_parse_and_never_run_as_root(plist):
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == plist.stem
    assert data["UserName"] == "__MHIA_USER__"
    assert data["EnvironmentVariables"]["HOME"] == "__MHIA_HOME__"
    assert data["StandardErrorPath"].startswith("__MHIA_HOME__/Library/Logs/mhia/")
    placeholders = set(re.findall(r"__[A-Z_]+__", plist.read_text(encoding="utf-8")))
    assert placeholders <= {"__MHIA_USER__", "__MHIA_HOME__", "__MHIA_REPO__", "__BREW_PREFIX__"}


def test_ollama_daemon_listens_only_on_loopback():
    data = plistlib.loads((MAC / "launchd/com.machineryhunters.mhia.ollama.plist").read_bytes())
    assert data["EnvironmentVariables"]["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert data["ProgramArguments"][-1] == "serve"


def test_scheduled_jobs_call_repo_scripts():
    backup = plistlib.loads((MAC / "launchd/com.machineryhunters.mhia.backup.plist").read_bytes())
    assert backup["ProgramArguments"][1:] == ["__MHIA_REPO__/scripts/mac/mhia.sh", "backup"]
    assert backup["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
    health = plistlib.loads((MAC / "launchd/com.machineryhunters.mhia.health.plist").read_bytes())
    assert health["ProgramArguments"][1:] == ["__MHIA_REPO__/scripts/mac/health.sh"]
    assert health["StartInterval"] == 300


def test_health_alert_only_reads_its_own_line_and_requires_https():
    text = (MAC / "health.sh").read_text(encoding="utf-8")
    assert "grep -E '^MHIA_ALERT_URL='" in text
    assert "source" not in text and '. "$REPO/.env"' not in text
    assert "https://*)" in text


# ── Respaldos ────────────────────────────────────────────────────────────


def test_backups_dir_is_mounted_and_gitignored():
    api = _compose("docker-compose.yml")["services"]["mhia-api"]
    assert "./backups:/app/backups" in [v.split(" ")[0] for v in api["volumes"]]
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "backups/*" in ignored
    for pattern in ("**/data/*.csv", "**/data/*.xlsx"):
        assert pattern in ignored
