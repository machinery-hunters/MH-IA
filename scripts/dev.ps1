<#
.SYNOPSIS
  Tareas de desarrollo de MH IA en Windows (equivalente a scripts/mac/mhia.sh y al Makefile).

.DESCRIPTION
  Modo por defecto: Ollama NATIVO en Windows (igual que en la Mac mini), con el stack
  en Docker usando docker-compose.host-ollama.yml. Con -OllamaMode docker se usa en su
  lugar Ollama en contenedor con la GPU NVIDIA (docker-compose.gpu.yml).

.EXAMPLE
  .\scripts\dev.ps1 hooks
  .\scripts\dev.ps1 doctor
  .\scripts\dev.ps1 up
  .\scripts\dev.ps1 pull-models
  .\scripts\dev.ps1 test
  .\scripts\dev.ps1 backup
  .\scripts\dev.ps1 restore-drill
  .\scripts\dev.ps1 up -OllamaMode docker
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("doctor", "up", "down", "ps", "logs", "pull-models", "ingest", "venv", "lock", "lock-upgrade", "test", "lint", "fmt", "web-build", "hooks", "backup", "backup-verify", "restore-drill")]
    [string]$Task,

    [ValidateSet("host", "docker")]
    [string]$OllamaMode = "host"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Pipeline = Join-Path $Root "services\pipeline_ai"
$Venv = Join-Path $Pipeline ".venv\Scripts\python.exe"
$ModelsFile = Join-Path $PSScriptRoot "ollama-models.txt"
$OllamaUrl = "http://127.0.0.1:11434"

if ($OllamaMode -eq "host") {
    $Compose = @("-f", "docker-compose.yml", "-f", "docker-compose.host-ollama.yml")
} else {
    $Compose = @("-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml")
}

# Detecta si el lanzador `py` tiene una versión de Python. Con $ErrorActionPreference
# = "Stop", Windows PowerShell 5.1 convierte el stderr de `py -3.12` (cuando no
# está instalado) en una excepción, así que se baja a "Continue" solo aquí.
function Test-PythonVersion {
    param([string]$Version)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        py "-$Version" -c "import sys" *> $null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Invoke-Checked {
    param([scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Falló con código $LASTEXITCODE" }
}

# Lee scripts/ollama-models.txt: versión esperada de Ollama y tag -> id de cada modelo.
function Get-ExpectedModels {
    $expected = @{ Version = $null; Models = [ordered]@{} }
    foreach ($line in Get-Content -Path $ModelsFile -Encoding UTF8) {
        $clean = $line.Trim()
        if (-not $clean -or $clean.StartsWith("#")) { continue }
        $parts = $clean -split "\s+"
        if ($parts[0] -eq "ollama") { $expected.Version = $parts[1] }
        else { $expected.Models[$parts[0]] = $parts[1] }
    }
    return $expected
}

# Compara el Ollama nativo con lo esperado. Devuelve la lista de errores (vacía = OK).
function Test-HostOllama {
    $errors = @()
    $expected = Get-ExpectedModels
    try {
        $version = (Invoke-RestMethod -Uri "$OllamaUrl/api/version" -TimeoutSec 5).version
    } catch {
        return @("Ollama nativo no responde en $OllamaUrl (abre la app de Ollama o ejecuta: ollama serve)")
    }
    if ($version -ne $expected.Version) {
        Write-Warning "Ollama $version instalado; la Mac usa $($expected.Version). Actualiza: winget upgrade Ollama.Ollama"
    } else {
        Write-Host "Ollama $version (OK, igual que la Mac)"
    }
    $installed = @{}
    foreach ($m in (Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 10).models) {
        $installed[$m.name] = $m.digest.Substring(0, 12)
    }
    foreach ($tag in $expected.Models.Keys) {
        $want = $expected.Models[$tag]
        if (-not $installed.ContainsKey($tag)) {
            $errors += "Falta el modelo $tag (ejecuta: .\scripts\dev.ps1 pull-models)"
        } elseif ($installed[$tag] -ne $want) {
            $errors += "El modelo $tag tiene id $($installed[$tag]) y se espera $want (ver scripts/ollama-models.txt)"
        } else {
            Write-Host "Modelo $tag $want (OK)"
        }
    }
    # Ollama no tiene autenticación: nunca debe escuchar fuera de loopback.
    foreach ($scope in @("Process", "User", "Machine")) {
        $bind = [Environment]::GetEnvironmentVariable("OLLAMA_HOST", $scope)
        if ($bind -and $bind -notmatch "^(http://)?(127\.0\.0\.1|localhost)(:\d+)?/?$") {
            $errors += "OLLAMA_HOST=$bind ($scope) expone Ollama fuera de 127.0.0.1: bórralo"
        }
    }
    return $errors
}

Push-Location $Root
try {
    switch ($Task) {
        "doctor" {
            # Valida el entorno SIN levantar nada. Reúne todos los errores y falla al final.
            $problems = @()
            if (-not (Test-Path ".env")) { Write-Warning ".env no existe: Copy-Item .env.example .env" }
            Invoke-Checked { docker compose @Compose config --quiet }
            Write-Host "docker compose config ($OllamaMode): OK"
            $published = docker compose @Compose config --format json | ConvertFrom-Json
            foreach ($svc in $published.services.PSObject.Properties) {
                foreach ($p in @($svc.Value.ports)) {
                    if ($p -and $p.host_ip -ne "127.0.0.1") {
                        $problems += "Puerto publicado fuera de 127.0.0.1 en '$($svc.Name)': $($p.published)"
                    }
                }
            }
            Write-Host "Puertos publicados revisados (solo 127.0.0.1)"
            git check-ignore -q .env
            if ($LASTEXITCODE -ne 0) { $problems += ".env NO está ignorado por git" } else { Write-Host ".env ignorado por git (OK)" }
            $hooksPath = git config core.hooksPath
            if ($hooksPath -ne ".githooks") {
                Write-Warning "Hook pre-push inactivo (protege main/develop de pushes directos): .\scripts\dev.ps1 hooks"
            } else {
                Write-Host "Hook pre-push activo (OK)"
            }
            # .gitattributes fuerza LF en el repo; autocrlf=true solo causa diffs fantasma.
            if ((git config core.autocrlf) -eq "true") {
                Write-Warning "core.autocrlf=true: ejecuta  git config core.autocrlf false  (el repo ya normaliza saltos de línea)"
            }
            if (-not (Test-PythonVersion "3.12")) { Write-Warning "Python 3.12 no está instalado (el CI, la imagen y la Mac usan 3.12)" }
            if ($OllamaMode -eq "host") {
                $problems += Test-HostOllama
                # Si el stack ya está arriba, verifica que el contenedor alcance al Ollama del host.
                $running = docker compose @Compose ps --status running -q mhia-api
                if ($running) {
                    $probe = "import urllib.request;urllib.request.urlopen('http://host.docker.internal:11434/api/version',timeout=5)"
                    docker compose @Compose exec -T mhia-api python -c $probe
                    if ($LASTEXITCODE -ne 0) { $problems += "mhia-api no alcanza al Ollama del host por host.docker.internal" }
                    else { Write-Host "mhia-api -> Ollama del host (OK)" }
                }
            }
            if ($problems.Count -gt 0) {
                foreach ($p in $problems) { Write-Host "ERROR: $p" -ForegroundColor Red }
                throw "doctor encontró $($problems.Count) problema(s)"
            }
            Write-Host "doctor: todo en orden" -ForegroundColor Green
        }
        "hooks" {
            Invoke-Checked { git config core.hooksPath .githooks }
            Write-Host "Hook pre-push activado: push directo a main/develop bloqueado en este clon."
        }
        "up" {
            if ($OllamaMode -eq "host") {
                $errors = Test-HostOllama
                if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Host "ERROR: $_" -ForegroundColor Red }; throw "Ollama nativo no está listo" }
            }
            New-Item -ItemType Directory -Force -Path (Join-Path $Root "backups") | Out-Null
            Invoke-Checked { docker compose @Compose up -d --build }
        }
        "down" { Invoke-Checked { docker compose @Compose down } }
        "ps" { Invoke-Checked { docker compose @Compose ps } }
        "logs" { docker compose @Compose logs -f mhia-api }
        "pull-models" {
            $expected = Get-ExpectedModels
            foreach ($tag in $expected.Models.Keys) {
                if ($OllamaMode -eq "host") { Invoke-Checked { ollama pull $tag } }
                else { Invoke-Checked { docker exec -it mhia-ollama ollama pull $tag } }
            }
        }
        "ingest" { Invoke-Checked { docker compose @Compose exec mhia-api python -m pipeline_ai.ingestion.run_ingest } }
        "backup" {
            Invoke-Checked { docker compose @Compose exec -T mhia-api python -m pipeline_ai.ops.backup create }
            Invoke-Checked { docker compose @Compose exec -T mhia-api python -m pipeline_ai.ops.backup prune --keep 14 }
        }
        "backup-verify" { Invoke-Checked { docker compose @Compose exec -T mhia-api python -m pipeline_ai.ops.backup verify } }
        "restore-drill" { Invoke-Checked { docker compose @Compose exec -T mhia-api python -m pipeline_ai.ops.backup drill } }
        "venv" {
            if (-not (Test-Path $Venv)) {
                $version = $null
                foreach ($candidate in @("3.12", "3.13", "3.11")) {
                    if (Test-PythonVersion $candidate) { $version = $candidate; break }
                }
                if (-not $version) { throw "No hay Python 3.11 o superior (instala 3.12: winget install Python.Python.3.12)" }
                if ($version -ne "3.12") {
                    Write-Warning "Python 3.12 no está instalado: se usa $version. El CI, la imagen Docker y la Mac usan 3.12."
                }
                Invoke-Checked { py "-$version" -m venv (Join-Path $Pipeline ".venv") }
            }
            Invoke-Checked { & $Venv -m pip install --upgrade pip }
            # Mismas versiones exactas (con hash) que el CI y la imagen de la Mac.
            Invoke-Checked { & $Venv -m pip install --require-hashes -r (Join-Path $Pipeline "requirements-dev.lock") }
        }
        { $_ -in @("lock", "lock-upgrade") } {
            if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "Falta uv: winget install --id astral-sh.uv -e" }
            $upgrade = @()
            if ($Task -eq "lock-upgrade") { $upgrade = @("--upgrade") }
            Push-Location $Pipeline
            try {
                Invoke-Checked { uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes -o requirements.lock -q @upgrade }
                Invoke-Checked { uv pip compile requirements.txt requirements-dev.txt -c requirements.lock --universal --python-version 3.12 --generate-hashes -o requirements-dev.lock -q @upgrade }
            } finally { Pop-Location }
            Write-Host "requirements.lock y requirements-dev.lock actualizados: corre .\scripts\dev.ps1 venv y test antes de subirlos."
        }
        "test" { Push-Location $Pipeline; try { Invoke-Checked { & $Venv -m pytest -v } } finally { Pop-Location } }
        "lint" {
            Push-Location $Pipeline
            try {
                Invoke-Checked { & $Venv -m ruff check pipeline_ai tests eval }
                Invoke-Checked { & $Venv -m black --check pipeline_ai tests eval }
            } finally { Pop-Location }
        }
        "fmt" {
            Push-Location $Pipeline
            try {
                Invoke-Checked { & $Venv -m ruff check pipeline_ai tests eval --fix }
                Invoke-Checked { & $Venv -m black pipeline_ai tests eval }
            } finally { Pop-Location }
        }
        "web-build" { Push-Location (Join-Path $Root "web"); try { Invoke-Checked { npm ci }; Invoke-Checked { npm run build } } finally { Pop-Location } }
    }
}
finally {
    Pop-Location
}
