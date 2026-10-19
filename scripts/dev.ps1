<#
.SYNOPSIS
  Tareas de desarrollo de MH IA en Windows (equivalente al Makefile).

.EXAMPLE
  .\scripts\dev.ps1 hooks
  .\scripts\dev.ps1 doctor
  .\scripts\dev.ps1 up
  .\scripts\dev.ps1 pull-models
  .\scripts\dev.ps1 test
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("doctor", "up", "down", "logs", "pull-models", "ingest", "venv", "test", "lint", "fmt", "web-build", "hooks")]
    [string]$Task
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Pipeline = Join-Path $Root "services\pipeline_ai"
$Venv = Join-Path $Pipeline ".venv\Scripts\python.exe"
$Compose = @("-f", "docker-compose.yml", "-f", "docker-compose.gpu.yml")

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

Push-Location $Root
try {
    switch ($Task) {
        "doctor" {
            # Valida el entorno SIN levantar nada.
            if (-not (Test-Path ".env")) { Write-Warning ".env no existe: Copy-Item .env.example .env" }
            Invoke-Checked { docker compose @Compose config --quiet }
            Write-Host "docker compose config: OK"
            $published = docker compose @Compose config --format json | ConvertFrom-Json
            foreach ($svc in $published.services.PSObject.Properties) {
                foreach ($p in @($svc.Value.ports)) {
                    if ($p -and $p.host_ip -ne "127.0.0.1") {
                        throw "Puerto publicado fuera de 127.0.0.1 en '$($svc.Name)': $($p.published)"
                    }
                }
            }
            Write-Host "Puertos publicados: solo en 127.0.0.1 (OK)"
            git check-ignore .env | Out-Null
            if ($LASTEXITCODE -ne 0) { throw ".env NO está ignorado por git" }
            Write-Host ".env ignorado por git (OK)"
            $hooksPath = git config core.hooksPath
            if ($hooksPath -ne ".githooks") {
                Write-Warning "Hook pre-push inactivo (protege main/develop de pushes directos): .\scripts\dev.ps1 hooks"
            } else {
                Write-Host "Hook pre-push activo (OK)"
            }
        }
        "hooks" {
            Invoke-Checked { git config core.hooksPath .githooks }
            Write-Host "Hook pre-push activado: push directo a main/develop bloqueado en este clon."
        }
        "up" { Invoke-Checked { docker compose @Compose up -d --build } }
        "down" { Invoke-Checked { docker compose @Compose down } }
        "logs" { docker compose @Compose logs -f mhia-api }
        "pull-models" {
            Invoke-Checked { docker exec -it mhia-ollama ollama pull llama3.1:8b }
            Invoke-Checked { docker exec -it mhia-ollama ollama pull nomic-embed-text }
        }
        "ingest" { Invoke-Checked { docker exec -it mhia-api python -m pipeline_ai.ingestion.run_ingest } }
        "venv" {
            if (-not (Test-Path $Venv)) {
                $version = $null
                foreach ($candidate in @("3.12", "3.13", "3.11")) {
                    if (Test-PythonVersion $candidate) { $version = $candidate; break }
                }
                if (-not $version) { throw "No hay Python 3.11 o superior (instala 3.12: winget install Python.Python.3.12)" }
                if ($version -ne "3.12") {
                    Write-Warning "Python 3.12 no está instalado: se usa $version. El CI y la imagen Docker usan 3.12."
                }
                Invoke-Checked { py "-$version" -m venv (Join-Path $Pipeline ".venv") }
            }
            Invoke-Checked { & $Venv -m pip install --upgrade pip }
            Invoke-Checked { & $Venv -m pip install -r (Join-Path $Pipeline "requirements.txt") -r (Join-Path $Pipeline "requirements-dev.txt") }
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
