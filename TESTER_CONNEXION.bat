@echo off
setlocal
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv & .venv\Scripts\pip install --quiet -r requirements.txt )
set /p HOTE=Adresse du serveur (ex: 127.0.0.1) : 
set /p PORT=Port (ex: 9443) : 
set /p TLS=TLS active ? (o/n) : 
set OPT=
if /i "%TLS%"=="o" set OPT=--tls
.venv\Scripts\python tools\check_connection.py %HOTE% %PORT% %OPT%
echo.
pause
