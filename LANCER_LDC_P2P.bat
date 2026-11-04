@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.11 ou plus recent est requis : https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
  echo Premiere installation, patientez...
  python -m venv .venv || (pause & exit /b 1)
)
.venv\Scripts\python -m pip install --quiet --disable-pip-version-check -r requirements.txt || (echo Echec de l'installation & pause & exit /b 1)
set PYTHONPATH=%~dp0src
start "" .venv\Scripts\pythonw.exe -m ldc_p2p %*
