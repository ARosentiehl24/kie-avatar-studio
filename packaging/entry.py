"""Punto de entrada para PyInstaller (build de Windows .exe).

PyInstaller corre el script de entrada como módulo top-level llamado
`__main__`, sin paquete padre asignado. Eso rompe los imports relativos
de `kie_avatar_studio/__main__.py` (que usa `from .app import …`) con:

    ImportError: attempted relative import with no known parent package

Este wrapper hace un import ABSOLUTO de `main()`. Al importar
`kie_avatar_studio.__main__` como submódulo del paquete, Python le
asigna `__package__ == "kie_avatar_studio"` y los imports relativos
internos resuelven normalmente.

Se mantiene fuera de `kie_avatar_studio/` a propósito para no contaminar
el paquete con un módulo que viola la convención de imports relativos.
"""

from __future__ import annotations

import sys

from kie_avatar_studio.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
