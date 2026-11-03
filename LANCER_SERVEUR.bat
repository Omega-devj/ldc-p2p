@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (python -m venv .venv & .venv\Scripts\pip install --quiet -r requirements.txt)
set PYTHONPATH=%~dp0src
echo Serveur LDC sur le port 9443 (Ctrl+C pour arreter). Sur le meme PC, utilisez 127.0.0.1 sans TLS.
.venv\Scripts\python -m ldc_p2p.server --host 0.0.0.0 --port 9443 --db ldc-relay.db
pause
