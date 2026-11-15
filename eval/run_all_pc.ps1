<#
.SYNOPSIS
  Run every OmniTrack evaluation on the demo PC (RTX 3060 Ti), then build the report figures.

.DESCRIPTION
  Run from the repository root:

      .\eval\run_all_pc.ps1                      # everything, cheapest first
      .\eval\run_all_pc.ps1 -Install             # first time: pip install eval\requirements.txt
      .\eval\run_all_pc.ps1 -Only reid,caviar    # a subset
      .\eval\run_all_pc.ps1 -Skip throughput     # everything except some steps

  Uses backend\.venv\Scripts\python.exe. Each step logs to eval\results\logs\<step>.log and
  writes eval\results\<id>.json. A failed step is reported and the run continues; the
  summary at the end lists what passed, failed or was skipped. Nothing is invented: a step
  that cannot run (missing dataset, Postgres down, no running backend) writes no result,
  and the report shows that metric as "not measured".

  Steps (cheapest first). Times are rough estimates for the RTX 3060 Ti + Ryzen 5 5600,
  not measurements. Paths are under backend\storage\, which is not in git: copy it from
  the dev box or fetch the public sets with eval\fetch_datasets.py.

    step            time     needs
    detect          3-6 min  demo_videos\caviar (videos + GT xml); yolo11m/yolo11s/yolov8n weights
    track           5-10 min demo_videos\caviar; pip trackeval (eval\requirements.txt)
    reid            2-4 min  datasets\market1501             (fetch_datasets.py market1501)
    caviar          2-5 min  datasets\caviar_reid_crops (or demo_videos\caviar to build it)
    crowd           2-4 min  demo_videos\mall\mall_dataset
    emotion         1-3 min  datasets\fer2013                (fetch_datasets.py fer2013)
    demographics    2-5 min  datasets\fairface\validation-0.25.parquet (copy from the dev box)
    fire            8-15 min datasets\dfire (fetch_datasets.py dfire) + demo_videos\caviar negatives
    synopsis        10-20min demo_videos\caviar (all corridor sequences, ~24 min of video)
    search_latency  5-10 min Postgres + pgvector running (scripts\start-infra.ps1), backend\.env
    throughput      ~3 min   backend running on -BackendUrl with 4 file cameras streaming
    adversarial     3-8 min  datasets\coco_val2017_person (downloads the 500-image subset if missing);
                             pip adversarial-robustness-toolbox (eval\requirements.txt)
    report_figures  2-4 min  the results above; writes docs\report\figures\ + docs\report\FIGURES.md
#>
[CmdletBinding()]
param(
    [string[]]$Only = @(),
    [string[]]$Skip = @(),
    [int]$Threads = 6,
    [string]$BackendUrl = "http://localhost:8000",
    [switch]$Install
)

$ErrorActionPreference = "Continue"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Repo
$Py = Join-Path $Repo "backend\.venv\Scripts\python.exe"
$Logs = Join-Path $Repo "eval\results\logs"
$Storage = Join-Path $Repo "backend\storage"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

if (-not (Test-Path $Py)) {
    Write-Host "backend\.venv\Scripts\python.exe not found. Create the venv first (README quick start)." -ForegroundColor Red
    exit 1
}
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($Install) {
    & $Py -m pip install -r (Join-Path $Repo "eval\requirements.txt")
    if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed (trackeval needs git on PATH)" -ForegroundColor Red; exit 1 }
}

$cuda = & $Py -c "import torch; print('cuda' if torch.cuda.is_available() else 'cpu', torch.__version__, torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
Write-Host "torch: $cuda"
if ("$cuda" -notmatch "^cuda") {
    Write-Host "WARNING: CUDA is not available - these numbers would be CPU numbers. Install the CUDA torch build (backend\requirements.txt header) or press Ctrl+C." -ForegroundColor Yellow
    Start-Sleep -Seconds 10
}

$caviarArgs = @()
if (Test-Path (Join-Path $Repo "backend\model_weight\osnet_ain_x1_0_omnitrack.pth")) {
    # Fine-tuned weights from backend\scripts\train_reid.py: measure both, shipped one first.
    $shipped = & $Py -c "import sys; sys.path.insert(0, 'backend'); from app.config import settings; print(settings.REID_WEIGHTS_PATH)"
    $shipped = "$(@($shipped)[-1])".Trim()
    $caviarArgs = @("--weights", $shipped, "--weights", "osnet_ain_x1_0_omnitrack.pth")
}

# name, script, extra args, required paths (relative to backend\storage)
$Steps = @(
    @{ Name = "detect";         Script = "eval\detect.py";         Args = @();             Needs = @("demo_videos\caviar") },
    @{ Name = "track";          Script = "eval\track.py";          Args = @();             Needs = @("demo_videos\caviar") },
    @{ Name = "reid";           Script = "eval\reid.py";           Args = @();             Needs = @("datasets\market1501") },
    @{ Name = "caviar";         Script = "eval\reid_caviar.py";    Args = $caviarArgs;     Needs = @("demo_videos\caviar") },
    @{ Name = "crowd";          Script = "eval\crowd.py";          Args = @();             Needs = @("demo_videos\mall\mall_dataset") },
    @{ Name = "emotion";        Script = "eval\emotion.py";        Args = @();             Needs = @("datasets\fer2013") },
    @{ Name = "demographics";   Script = "eval\demographics.py";   Args = @();             Needs = @("datasets\fairface\validation-0.25.parquet") },
    @{ Name = "fire";           Script = "eval\fire.py";           Args = @();             Needs = @("datasets\dfire", "demo_videos\caviar") },
    @{ Name = "synopsis";       Script = "eval\synopsis.py";       Args = @();             Needs = @("demo_videos\caviar") },
    @{ Name = "search_latency"; Script = "eval\search_latency.py"; Args = @();             Needs = @() },
    @{ Name = "throughput";     Script = "eval\throughput.py";     Args = @("--url", $BackendUrl, "--hardware-note", "RTX 3060 Ti demo PC (Ryzen 5 5600, 16 GB)"); Needs = @() },
    @{ Name = "adversarial";    Script = "eval\adversarial.py";    Args = @("--images", "50"); Needs = @() },
    @{ Name = "report_figures"; Script = "eval\report_figures.py"; Args = @();             Needs = @() }
)

function Test-Port([string]$HostName, [int]$Port) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect($HostName, $Port); $c.Close(); return $true } catch { return $false }
}

$Summary = @()
foreach ($s in $Steps) {
    $name = $s.Name
    if ($Only.Count -gt 0 -and $Only -notcontains $name) { continue }
    if ($Skip -contains $name) { $Summary += [pscustomobject]@{ Step = $name; Result = "skipped (-Skip)"; Minutes = 0 }; continue }

    $missing = @($s.Needs | Where-Object { -not (Test-Path (Join-Path $Storage $_)) })
    if ($missing.Count -gt 0) {
        $msg = "missing backend\storage\" + ($missing -join ", backend\storage\")
        Write-Host "[$name] SKIP: $msg" -ForegroundColor Yellow
        $Summary += [pscustomobject]@{ Step = $name; Result = "skipped: $msg"; Minutes = 0 }
        continue
    }
    if ($name -eq "search_latency" -and -not (Test-Port "127.0.0.1" 5432)) {
        Write-Host "[$name] SKIP: Postgres is not listening on 5432 - run .\scripts\start-infra.ps1" -ForegroundColor Yellow
        $Summary += [pscustomobject]@{ Step = $name; Result = "skipped: Postgres down"; Minutes = 0 }
        continue
    }
    if ($name -eq "throughput") {
        try { Invoke-RestMethod -Uri "$BackendUrl/api/pipeline/health" -TimeoutSec 5 | Out-Null }
        catch {
            Write-Host "[$name] SKIP: no backend at $BackendUrl. Start uvicorn, add 4 file cameras (720p clips), then: .\eval\run_all_pc.ps1 -Only throughput,report_figures" -ForegroundColor Yellow
            $Summary += [pscustomobject]@{ Step = $name; Result = "skipped: backend not running"; Minutes = 0 }
            continue
        }
    }

    $log = Join-Path $Logs "$name.log"
    $argv = @($s.Script) + $s.Args + @("--threads", "$Threads")
    "=== $(Get-Date -Format s)  $Py $($argv -join ' ')" | Out-File -FilePath $log -Encoding utf8
    Write-Host "`n[$name] $($argv -join ' ')   (log: eval\results\logs\$name.log)" -ForegroundColor Cyan
    $t0 = Get-Date
    & $Py @argv 2>&1 | ForEach-Object { $line = "$_"; Add-Content -Path $log -Value $line -Encoding utf8; $line }
    $rc = $LASTEXITCODE
    $mins = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
    $res = if ($rc -eq 0) { "ok" } else { "FAILED (exit $rc) - see eval\results\logs\$name.log" }
    Write-Host "[$name] $res in $mins min" -ForegroundColor $(if ($rc -eq 0) { "Green" } else { "Red" })
    $Summary += [pscustomobject]@{ Step = $name; Result = $res; Minutes = $mins }
}

Write-Host "`n==== Summary ====" -ForegroundColor Cyan
$Summary | Format-Table -AutoSize | Out-String | Write-Host
$Summary | Format-Table -AutoSize | Out-String | Out-File -FilePath (Join-Path $Logs "summary.txt") -Encoding utf8
Write-Host "Results: eval\results\*.json   Figures: docs\report\figures\   Captions: docs\report\FIGURES.md"
Write-Host "Next: update docs\EVALUATION.md with these numbers (they replace the CPU-only laptop numbers)."
if (@($Summary | Where-Object { $_.Result -like "FAILED*" }).Count -gt 0) { exit 1 }
