#!/usr/bin/env bash
# Build BanShi-Setup.exe from WSL: syncs the repo to a local Windows folder
# (PyInstaller can't build from a \\wsl$ path) and runs build.ps1 there.
# Output: %USERPROFILE%\banshi-build\out\BanShi-Setup.exe
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
win_home="$(wslpath "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')")"
work="$win_home/banshi-build"

mkdir -p "$work/src"
rsync -a --delete \
  --exclude .git --exclude '.venv*' --exclude __pycache__ \
  --exclude banshi.db --exclude .secret_key \
  "$repo/" "$work/src/"

cd "$work"  # powershell.exe refuses to start with a \\wsl$ working directory
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$work/src/installer/build.ps1")" "$@"
