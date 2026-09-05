@echo off
if /I "%~1"=="up" (
  wsl.exe -e bash -lc "exec /mnt/c/Users/Admin/code/discord_minecraft_terminal/scripts/mc-up.sh"
  exit /b %ERRORLEVEL%
)
echo Usage: mc up
echo    or: mcup
exit /b 1
