# Build the BanShi Windows installer (BanShi-Setup.exe) with Ollama and a model bundled.
#
# Run on Windows from a copy of the repo on a local drive (installer/build.sh
# does the copy from WSL and calls this). Needs Python 3.12 and Inno Setup 6.
# Big downloads (Ollama, model weights, WebView2 bootstrapper) are cached in
# <Work>\cache, so rebuilding after a code change only redoes the fast steps.
#
# Kept ASCII-only on purpose: Windows PowerShell 5 reads BOM-less scripts in
# the system ANSI code page, which would garble any Chinese text here.
param(
    [string]$Model = "qwen3:4b-instruct",
    [string]$Work = "$env:USERPROFILE\banshi-build"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest/Expand-Archive are 10x slower with the progress bar

$Src = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $Work "cache"
$Stage = Join-Path $Work "stage\BanShi"
$Out = Join-Path $Work "out"
New-Item -ItemType Directory -Force -Path $Cache, $Out | Out-Null

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

function Get-Download($url, $dest) {
    if (Test-Path $dest) { return }
    Write-Host "downloading $url"
    & curl.exe -fL --retry 3 -o "$dest.part" $url; Check "download $url"
    Move-Item "$dest.part" $dest
}

# ---- tools
$Python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $Python)) { $Python = (Get-Command py -ErrorAction Stop).Source }
$Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $Iscc)) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }

# ---- 1. BanShi.exe (PyInstaller, one-folder: starts much faster than --onefile)
Step "Building BanShi.exe"
$Venv = Join-Path $Work "venv"
if (-not (Test-Path "$Venv\Scripts\python.exe")) { & $Python -m venv $Venv; Check "venv" }
& "$Venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check `
    -r "$Src\requirements.txt" -r "$Src\requirements-desktop.txt"; Check "pip install"
& "$Venv\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --windowed --name BanShi `
    --distpath "$Work\pyi-dist" --workpath "$Work\pyi-work" --specpath "$Work\pyi-work" `
    --add-data "$Src\templates;templates" --add-data "$Src\static;static" `
    "$Src\desktop.py"; Check "pyinstaller"

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
& robocopy "$Work\pyi-dist\BanShi" $Stage /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy app failed" }

# ---- 2. Ollama runtime
Step "Preparing Ollama"
$OllamaZip = Join-Path $Cache "ollama-windows-amd64.zip"
Get-Download "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip" $OllamaZip
$OllamaDir = Join-Path $Cache "ollama"
if (-not (Test-Path "$OllamaDir\ollama.exe")) {
    Expand-Archive -Force $OllamaZip $OllamaDir
    # cuda_v12 already covers RTX 20-50 series on far more driver versions;
    # dropping v13 (~660MB) keeps the whole setup under the ~4GB single-exe limit.
    Remove-Item -Recurse -Force "$OllamaDir\lib\ollama\cuda_v13" -ErrorAction SilentlyContinue
}
& robocopy $OllamaDir "$Stage\ollama" /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy ollama failed" }

# ---- 3. Model weights, pulled once with that same Ollama into a private store
Step "Preparing model $Model"
$ModelCache = Join-Path $Cache ("models-" + ($Model -replace '[:/]', '_'))
$name, $tag = $Model.Split(":")
if (-not $tag) { $tag = "latest" }
$Manifest = "$ModelCache\manifests\registry.ollama.ai\library\$name\$tag"
if (-not (Test-Path $Manifest)) {
    $env:OLLAMA_MODELS = $ModelCache
    $env:OLLAMA_HOST = "127.0.0.1:11499"
    $server = Start-Process -FilePath "$OllamaDir\ollama.exe" -ArgumentList "serve" -PassThru -WindowStyle Hidden
    try {
        Start-Sleep -Seconds 3
        & "$OllamaDir\ollama.exe" pull $Model; Check "ollama pull"
    } finally {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        Remove-Item Env:OLLAMA_MODELS, Env:OLLAMA_HOST
    }
}
& robocopy $ModelCache "$Stage\models" /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy model failed" }
# desktop.py reads this to know which model name to ask the bundled Ollama for
[IO.File]::WriteAllText("$Stage\models\bundled-model.txt", $Model, (New-Object Text.UTF8Encoding $false))

# ---- 4. WebView2 bootstrapper (only run by the installer on PCs that lack it)
$WebView2 = Join-Path $Cache "MicrosoftEdgeWebview2Setup.exe"
Get-Download "https://go.microsoft.com/fwlink/p/?LinkId=2124703" $WebView2

# ---- 5. Installer
Step "Compiling installer"
$Version = Get-Date -Format "yyyy.M.d"
& $Iscc /Q "/DSourceDir=$Stage" "/DOutputDir=$Out" "/DAppVersion=$Version" "/DWebView2Setup=$WebView2" `
    "$Src\installer\BanShi.iss"; Check "ISCC"

$Setup = Get-Item "$Out\BanShi-Setup.exe"
Write-Host ("`nDone: {0} ({1:N2} GB)" -f $Setup.FullName, ($Setup.Length / 1GB)) -ForegroundColor Green
