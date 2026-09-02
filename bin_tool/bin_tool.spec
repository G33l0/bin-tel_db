# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec: produces a single-file Windows console executable.

    python -m PyInstaller --clean --noconfirm bin_tool.spec

The resulting dist\\BIN-TEL.exe creates config.json and the data\\ folders next
to itself the first time it runs.
"""

block_cipher = None

a = Analysis(
    ["bin_tool.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        "providers.base",
        "providers.offline_provider",
        "providers.local_provider",
        "providers.public_provider",
        "database.database",
        "database.models",
        "ui.colors",
        "ui.menu",
        "ui.progress",
        "utils.csv_utils",
        "utils.logging_utils",
        "utils.validation",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "numpy", "matplotlib"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BIN-TEL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
