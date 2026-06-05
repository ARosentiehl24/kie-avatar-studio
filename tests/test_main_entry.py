"""Guardrails de los puntos de entrada de la app.

Cubre dos escenarios:

1. `python -m kie_avatar_studio` — `__main__.py` se importa como
   submódulo del paquete; los imports relativos internos deben resolver.

2. PyInstaller — el script de entrada `packaging/entry.py` se corre como
   módulo top-level `__main__` y delega en `main()` vía import absoluto.

El test simula ambos casos haciendo `importlib.import_module()`. NO
ejecuta `main()` porque eso lanza la TUI de Textual y necesita TTY.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_main_module_imports_with_parent_package() -> None:
    """`kie_avatar_studio.__main__` debe importarse como submódulo.

    Si el import relativo `from .app import KieAvatarStudioApp` rompiera,
    este import también fallaría con `ImportError`.
    """
    if "kie_avatar_studio.__main__" in sys.modules:
        del sys.modules["kie_avatar_studio.__main__"]

    mod = importlib.import_module("kie_avatar_studio.__main__")

    assert mod.__package__ == "kie_avatar_studio"
    assert callable(mod.main)


def test_pyinstaller_entry_script_uses_absolute_import() -> None:
    """`packaging/entry.py` debe existir y reexportar `main` con import absoluto.

    PyInstaller corre este script como `__main__` (sin paquete padre).
    Validamos por contenido — leer el archivo es suficiente porque
    ejecutarlo aquí también lanzaría la TUI.
    """
    entry = Path(__file__).resolve().parent.parent / "packaging" / "entry.py"

    assert entry.is_file(), f"Falta {entry}"

    source = entry.read_text(encoding="utf-8")
    assert "from kie_avatar_studio.__main__ import main" in source, (
        "El entry script DEBE usar import absoluto; un relativo "
        "rompería PyInstaller (CR del bug original)."
    )
