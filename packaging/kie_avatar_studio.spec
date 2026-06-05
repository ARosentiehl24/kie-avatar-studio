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
# - Hidden imports: Pydantic v2, Loguru y aiosqlite necesitan que
#   PyInstaller sepa de algunos módulos que importan dinámicamente.

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['../kie_avatar_studio/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        # CSS de Textual (loadeado en runtime con CSS_PATH).
        ('../kie_avatar_studio/ui/styles.tcss', 'kie_avatar_studio/ui'),
    ],
    hiddenimports=[
        'aiosqlite',
        'loguru',
        'pydantic',
        'pydantic_settings',
        'textual',
        'textual.widgets',
        'textual.screen',
        'httpx',
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
