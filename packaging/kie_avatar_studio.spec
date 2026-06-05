# PyInstaller spec file para Kie Avatar Studio (Windows .exe).
#
# Usado por `.github/workflows/release.yml` para empaquetar la app
# en un solo .exe distribuible. El runner Windows del workflow corre:
#
#     pyinstaller packaging/kie_avatar_studio.spec
#
# que produce `dist/KieAvatarStudio.exe`.
#
# Notas:
# - `console=True`: la app ES una TUI, NECESITA una consola con TTY.
#   Con `console=False` Textual no podría renderizar.
# - `--onefile` equivalente: usamos `EXE` directo sin `COLLECT`.
# - `textual` usa `__getattr__` en `widgets/__init__.py` para lazy-load
#   submódulos (`_tab_pane`, `_data_table`, etc.). El analizador estático
#   de PyInstaller NO los descubre; sin `collect_all('textual')` el .exe
#   muere con `ModuleNotFoundError: No module named 'textual.widgets._tab_pane'`.
# - Pydantic v2 y pydantic_settings también cargan submódulos dinámicamente
#   (validadores, hooks de configuración): los cubrimos con `collect_submodules`.
# - El script de entrada es `packaging/entry.py` (NO `__main__.py`
#   del paquete): un wrapper con import absoluto necesario para que
#   PyInstaller no rompa los imports relativos internos. Detalles en
#   el docstring de `packaging/entry.py`.

# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# `__file__` no está definido en el namespace del spec, pero `SPECPATH`
# sí: PyInstaller lo inyecta apuntando al directorio del .spec.
SPEC_DIR = Path(SPECPATH).resolve()
ROOT_DIR = SPEC_DIR.parent

block_cipher = None

# `collect_all` devuelve (datas, binaries, hiddenimports) para un paquete
# entero, incluyendo módulos lazy-loaded vía `__getattr__` que el análisis
# estático no detecta.
textual_datas, textual_binaries, textual_hiddenimports = collect_all('textual')

a = Analysis(
    [str(SPEC_DIR / 'entry.py')],
    pathex=[str(ROOT_DIR)],
    binaries=textual_binaries,
    datas=[
        # CSS propio de la app (loadeado en runtime con CSS_PATH).
        (str(ROOT_DIR / 'kie_avatar_studio' / 'ui' / 'styles.tcss'),
         'kie_avatar_studio/ui'),
        *textual_datas,
    ],
    hiddenimports=[
        'aiosqlite',
        'loguru',
        'httpx',
        *collect_submodules('pydantic'),
        *collect_submodules('pydantic_settings'),
        *textual_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='KieAvatarStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='packaging/app.ico',  # opcional: agregar más adelante.
)
