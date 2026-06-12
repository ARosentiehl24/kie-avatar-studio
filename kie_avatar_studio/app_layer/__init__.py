"""Capa de aplicación: orquestación, máquinas de estado, cola.

Depende solo de tipos del `domain/` (incluye los Protocols de `ports.py`).
Nunca importa de `infra/` ni de `ui/` directamente: las dependencias concretas
se inyectan en el composition root.
"""

from __future__ import annotations
