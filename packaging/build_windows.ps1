# Construit l'exécutable et l'installateur Windows. À lancer depuis la racine du dépôt, dans PowerShell.
$ErrorActionPreference = "Stop"
python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\pip install -r requirements.txt pytest pytest-asyncio pyinstaller pillow

Write-Host "== Tests =="
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv-build\Scripts\python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Les tests échouent : build annulé." }
.\.venv-build\Scripts\python tests\ui_smoke.py
if ($LASTEXITCODE -ne 0) { throw "Le test de fumée échoue : build annulé." }
Remove-Item Env:\QT_QPA_PLATFORM

Write-Host "== Icône =="
.\.venv-build\Scripts\python tools\make_icon.py

Write-Host "== PyInstaller =="
.\.venv-build\Scripts\pyinstaller packaging\ldc_p2p.spec --noconfirm --distpath dist --workpath build

Write-Host "== Contrôle : aucune donnée personnelle dans la distribution =="
$bad = Get-ChildItem dist\LDC-P2P -Recurse -Include *.db,*.db-wal,*.pem,*.ldckey,*.ldcbackup,keystore.json -ErrorAction SilentlyContinue
if ($bad) { $bad | ForEach-Object { Write-Error $_.FullName }; throw "Fichiers sensibles trouvés dans la distribution." }

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($iscc) { & iscc packaging\installer.iss } else { Write-Warning "Inno Setup (iscc) introuvable : installateur non généré (dist\LDC-P2P reste utilisable en portable)." }

Get-ChildItem dist\*.exe -ErrorAction SilentlyContinue | ForEach-Object {
  "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name | Tee-Object -FilePath dist\SHA256SUMS.txt -Append
}
Write-Host "Terminé."
