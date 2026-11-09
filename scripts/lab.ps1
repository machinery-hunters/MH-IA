# Laboratorio MH IA: prueba de punta a punta con documentos SINTETICOS.
#
#   .\scripts\lab.ps1 up       # levanta un Qdrant desechable en 127.0.0.1:6333 y revisa Ollama
#   .\scripts\lab.ps1 ingest   # indexa services\pipeline_ai\lab\docs en la coleccion del laboratorio
#   .\scripts\lab.ps1 ask      # corre las preguntas de lab\questions.json por el grafo completo
#   .\scripts\lab.ps1 ask -Only q03
#   .\scripts\lab.ps1 down     # borra el Qdrant del laboratorio (y sus datos)
#   .\scripts\lab.ps1 all      # up + ingest + ask
#
# Seguridad: el Qdrant del laboratorio escucha SOLO en 127.0.0.1, no usa volumen (los datos
# mueren con el contenedor) y es distinto del Qdrant del compose normal. Los documentos son
# inventados: no apuntes este laboratorio a datos reales.
# Requiere Ollama corriendo (ollama serve) con llama3.1:8b y nomic-embed-text, y
# ANTHROPIC_API_KEY en .env.

param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'ingest', 'ask', 'down', 'all')]
    [string]$Task = 'all',
    [string]$Only = ''
)

$ErrorActionPreference = 'Continue'
# Salida de Python sin bufer: se ve cada pregunta en cuanto termina, aunque haya pipes.
$env:PYTHONUNBUFFERED = '1'
$root = Split-Path -Parent $PSScriptRoot
$service = Join-Path $root 'services\pipeline_ai'
$py = Join-Path $service '.venv\Scripts\python.exe'
$container = 'mhia-lab-qdrant'
$qdrantImage = 'qdrant/qdrant:v1.19.1'  # misma version que docker-compose.yml y qdrant-client

function Fail([string]$msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Test-Python {
    if (-not (Test-Path $py)) { Fail "No existe el venv: $py (corre .\scripts\dev.ps1 setup)" }
}

function Invoke-Up {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'Docker no esta corriendo.' }

    docker rm -f $container *> $null
    docker run -d --name $container -p 127.0.0.1:6333:6333 $qdrantImage *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'No se pudo levantar Qdrant.' }

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-RestMethod 'http://127.0.0.1:6333/collections' -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { Fail 'Qdrant no respondio en 30 segundos.' }
    Write-Host 'Qdrant del laboratorio: OK (127.0.0.1:6333)' -ForegroundColor Green

    try {
        $names = (Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5).models | ForEach-Object { $_.name }
    } catch {
        Fail 'Ollama no responde en 127.0.0.1:11434. Corre: ollama serve'
    }
    foreach ($model in 'llama3.1:8b', 'nomic-embed-text') {
        if (-not ($names | Where-Object { $_ -like "$model*" })) {
            Fail "Falta el modelo $model. Corre: ollama pull $model"
        }
    }
    Write-Host 'Ollama y modelos: OK' -ForegroundColor Green
}

function Invoke-Ingest {
    Test-Python
    Push-Location $service
    try { & $py 'lab\lab_ingest.py' | Out-Host; $code = $LASTEXITCODE } finally { Pop-Location }
    if ($code -ne 0) { Fail 'La ingesta fallo.' }
}

function Invoke-Ask {
    Test-Python
    Push-Location $service
    try {
        # Out-Host: la salida va a la consola y NO al valor de retorno de la funcion
        # (si no, "exit (Invoke-Ask)" se la traga y solo se ve el stderr).
        if ($Only) { & $py 'lab\lab_ask.py' --only $Only | Out-Host } else { & $py 'lab\lab_ask.py' | Out-Host }
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    return [int]$code
}

function Invoke-Down {
    docker rm -f $container *> $null
    Write-Host 'Qdrant del laboratorio eliminado.' -ForegroundColor Green
}

switch ($Task) {
    'up'     { Invoke-Up }
    'ingest' { Invoke-Ingest }
    'ask'    { exit (Invoke-Ask) }
    'down'   { Invoke-Down }
    'all'    { Invoke-Up; Invoke-Ingest; exit (Invoke-Ask) }
}
