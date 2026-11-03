# -*- mode: python ; coding: utf-8 -*-
# Build : pyinstaller packaging/ldc_p2p.spec --noconfirm   (voir build_windows.ps1)
from pathlib import Path

ROOT = Path(SPECPATH).parent
block_cipher = None

a = Analysis(
    [str(ROOT / "src" / "ldc_p2p" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(ROOT / "src" / "ldc_p2p" / "resources"), "ldc_p2p/resources")],
    hiddenimports=["qasync", "nacl", "nacl.bindings", "cryptography.hazmat.primitives.kdf.scrypt"],
    excludes=["tkinter", "unittest", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LDC-P2P", console=False, icon=str(ROOT / "packaging" / "ldc-p2p.ico"),
          version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="LDC-P2P", upx=False)
