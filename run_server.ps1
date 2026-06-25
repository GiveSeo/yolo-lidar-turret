# PC 서버 실행 스크립트 (FastAPI + YOLO)
#
# 사용법:
#   .\run_server.ps1                 # 기본(실제 YOLO, GPU, 포트 8000)
#   .\run_server.ps1 -Demo           # DETECTOR=demo (GPU/가중치 없이 흐름 테스트)
#   .\run_server.ps1 -Port 8080      # 포트 변경
#   .\run_server.ps1 -Cpu            # YOLO 를 CPU 로 강제(YOLO_DEVICE=cpu)
#
# 더블클릭으로 실행하려면 run_server.bat 을 쓰세요.

param(
    [int]$Port = 8000,
    [string]$BindHost = "0.0.0.0",
    [switch]$Demo,
    [switch]$Cpu
)

$ErrorActionPreference = "Stop"

# 스크립트 위치를 작업 디렉터리로(어디서 실행하든 동일하게 동작)
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "[오류] venv 파이썬을 찾을 수 없습니다: $python" -ForegroundColor Red
    Write-Host "       먼저 가상환경을 만들고 의존성을 설치하세요 (SETUP_NEW_PC.md 참고)." -ForegroundColor Yellow
    Read-Host "엔터를 누르면 종료"
    exit 1
}

# 선택적 환경변수
if ($Demo) {
    $env:DETECTOR = "demo"
    Write-Host "[설정] DETECTOR=demo (색상 블롭 검출기 — GPU/가중치 불필요)" -ForegroundColor Cyan
}
if ($Cpu) {
    $env:YOLO_DEVICE = "cpu"
    Write-Host "[설정] YOLO_DEVICE=cpu" -ForegroundColor Cyan
}

# 접속 안내(같은 네트워크의 Pi/브라우저용)
$lan = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "=== AI 자동 조준 서버 시작 ===" -ForegroundColor Green
Write-Host "  웹 UI    : http://localhost:$Port/" -ForegroundColor Green
if ($lan) {
    Write-Host "  LAN 접속 : http://${lan}:$Port/   (Pi --pc-host 에 이 IP 사용)" -ForegroundColor Green
}
Write-Host "  대시보드 : http://localhost:$Port/dashboard" -ForegroundColor Green
Write-Host "  Pi TCP   : 포트 9000 (방화벽에서 8000/9000 허용 필요)" -ForegroundColor DarkGray
Write-Host "  종료     : Ctrl+C" -ForegroundColor DarkGray
Write-Host ""

# --timeout-graceful-shutdown 2 : video_feed/ws 때문에 Ctrl+C 가 안 먹는 문제 완화
& $python -m uvicorn app.main:app `
    --host $BindHost `
    --port $Port `
    --timeout-graceful-shutdown 2
