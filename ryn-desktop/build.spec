# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec لعميل ويندوز RYN (بيشتغل مع PyInstaller 5 و 6):
#   cd ryn-desktop && pyinstaller --noconfirm build.spec
# نقطة البناء هي run_desktop.py (مو app/main.py لأنو بيستعمل imports نسبية).

a = Analysis(
    ['run_desktop.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'mss', 'PIL', 'pyautogui', 'keyring', 'keyring.backends.Windows',
        'pynput.keyboard._win32', 'pynput.mouse._win32',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RYN-Agent-PC',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
