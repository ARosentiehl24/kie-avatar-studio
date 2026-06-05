"""Punto de entrada `python -m kie_avatar_studio`."""

from __future__ import annotations

import sys

from .app import KieAvatarStudioApp


def main(argv: list[str] | None = None) -> int:
    KieAvatarStudioApp().run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
