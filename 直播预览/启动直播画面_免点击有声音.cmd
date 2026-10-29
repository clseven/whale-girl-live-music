@echo off
rem Launch the audience live view in a dedicated Chrome profile with:
rem   autoplay allowed without a click
rem   background timer throttling disabled (the page polls every 250ms for replies)
rem   occlusion detection off, so a covered window is not treated as hidden/frozen
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Æô¶¯Æ÷.ps1" -NoOpen
if errorlevel 1 (
  pause
  exit /b 1
)

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo Chrome not found. Edit this file with the correct chrome.exe path.
  pause
  exit /b 1
)

start "" "%CHROME%" --user-data-dir="%~dp0.runtime\chrome-live" --autoplay-policy=no-user-gesture-required --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling --start-maximized "http://127.0.0.1:18763/?view=live"
