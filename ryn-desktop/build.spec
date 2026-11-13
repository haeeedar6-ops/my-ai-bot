# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for RYN Desktop Agent (Section 8 & 14)
block_cipher = None

a = Analysis(
    ['run_desktop.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['mss', 'PIL', 'pyautogui', 'keyring', 'pynput'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
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
    name='RYN-Desktop-Agent',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
