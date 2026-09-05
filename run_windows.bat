@echo off
cd /d "%~dp0"
title Binance Futures Trade Scanner V2

where py >nul 2>nul
if not errorlevel 1 (
  set PY=py
  goto :found
)

where python >nul 2>nul
if not errorlevel 1 (
  set PY=python
  goto :found
)

echo Python launcher not found.
echo Install Python and make sure either "py" or "python" works.
pause
exit /b 1

:found
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Failed to create virtual environment.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
echo Installing requirements...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Package installation failed.
  pause
  exit /b 1
)

echo Starting scanner...
python -m streamlit run app.py
pause
