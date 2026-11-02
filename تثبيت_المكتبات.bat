@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo Installing required Python packages...
echo ================================================
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.14 --version >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=py -3.14"
  ) else (
    set "PYTHON_CMD=py -3"
  )
) else (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo ERROR: Python was not found. Install Python 3.11 or newer and enable Add Python to PATH.
  pause
  exit /b 1
)
%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 goto failed
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo SUCCESS: Required packages were installed.
pause
exit /b 0
:failed
echo.
echo ERROR: Package installation failed. Check your internet connection and Python installation.
pause
exit /b 1
