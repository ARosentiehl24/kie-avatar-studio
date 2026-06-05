"""Smoke test del detector local del agente `code-quality-reviewer`.

Valida dos cosas:

1. Sobre `bad_feature.py` el detector emite veredicto ``CAMBIOS_REQUERIDOS`` y reporta
   todas las reglas esperadas (``CR-1.1``, ``CR-3.3``, ``CR-4.1``, ``CR-4.2``,
   ``CR-5.1``, ``CR-5.6``) con líneas concretas.
2. Sobre `good_feature.py` el detector emite veredicto ``APROBADO`` sin hallazgos y el
   informe respeta el formato oficial.
"""

from __future__ import annotations

import sys
from pathlib import Path

# El detector vive en la raíz del proyecto y no es parte del paquete distribuible.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_detector import (  # noqa: E402 — necesario tras manipular sys.path
    Hallazgo,
    analizar_archivo,
    renderizar_informe,
)

FIXTURE_DIR = Path(__file__).parent / "agent_fixtures"
BAD = FIXTURE_DIR / "bad_feature.py"
GOOD = FIXTURE_DIR / "good_feature.py"

REGLAS_ESPERADAS_BAD: frozenset[str] = frozenset(
    {"CR-1.1", "CR-3.3", "CR-4.1", "CR-4.2", "CR-5.1", "CR-5.6"}
)


def _reglas(hallazgos: list[Hallazgo]) -> set[str]:
    return {h.rule for h in hallazgos}


def test_fixtures_existen() -> None:
    assert BAD.is_file(), f"falta {BAD}"
    assert GOOD.is_file(), f"falta {GOOD}"


def test_bad_dispara_todas_las_reglas_esperadas() -> None:
    hallazgos = analizar_archivo(BAD)
    encontradas = _reglas(hallazgos)
    faltantes = REGLAS_ESPERADAS_BAD - encontradas
    assert not faltantes, f"el detector no marcó {faltantes}; salida={encontradas}"


def test_bad_no_inventa_reglas() -> None:
    hallazgos = analizar_archivo(BAD)
    encontradas = _reglas(hallazgos)
    desconocidas = encontradas - REGLAS_ESPERADAS_BAD
    assert not desconocidas, f"el detector marcó reglas no esperadas: {desconocidas}"


def test_bad_informe_es_cambios_requeridos() -> None:
    hallazgos = analizar_archivo(BAD)
    informe = renderizar_informe("bad_feature", hallazgos)
    assert "## Veredicto\nCAMBIOS_REQUERIDOS" in informe
    assert informe.startswith("# Code Quality Review — bad_feature")
    for rule in REGLAS_ESPERADAS_BAD:
        assert f"[{rule}]" in informe, f"informe no cita la regla {rule}"


def test_good_es_aprobado_sin_hallazgos() -> None:
    hallazgos = analizar_archivo(GOOD)
    assert hallazgos == [], f"el fixture limpio no debería tener hallazgos: {hallazgos}"
    informe = renderizar_informe("good_feature", hallazgos)
    assert "## Veredicto\nAPROBADO" in informe
    assert "- ninguno" in informe


def test_informe_respeta_formato_oficial() -> None:
    """El primer renglón es el título, luego van Veredicto, Hallazgos, sin Notas extra."""
    informe = renderizar_informe("demo", [])
    lineas = informe.splitlines()
    assert lineas[0] == "# Code Quality Review — demo"
    assert lineas[2] == "## Veredicto"
    assert lineas[3] in {"APROBADO", "CAMBIOS_REQUERIDOS"}
    assert lineas[5] == "## Hallazgos"
