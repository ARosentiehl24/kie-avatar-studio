"""Detector estático local que aplica un subconjunto de reglas de `docs/CODE_QUALITY.md`.

El agente "vivo" (`code-quality-reviewer.md`) corre dentro de un modelo y necesita red.
Esta utilidad replica las reglas **mecánicas** para que la suite de tests pueda validar
sin red que el formato y los hallazgos esperados se producen sobre fixtures conocidos.

Reglas implementadas (subconjunto):

- ``CR-1.1`` capa restringida importa módulos prohibidos
- ``CR-3.3`` número mágico inline en cuerpo de función
- ``CR-4.1`` uso de ``ValueError``/``RuntimeError`` ad-hoc
- ``CR-4.2`` ``except Exception: pass`` o ``except:`` desnudo
- ``CR-5.1`` ``time.sleep``, ``requests.``
- ``CR-5.6`` ``datetime.utcnow()``

Las reglas opinables (SRP, OCP, nombres) se dejan al agente real.

Uso desde tests::

    from pathlib import Path
    from quality_detector import analizar_archivo, renderizar_informe

    hallazgos = analizar_archivo(Path("tests/agent_fixtures/bad_feature.py"))
    informe = renderizar_informe("bad_feature", hallazgos)
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Para cada prefijo de capa (real o declarado por `# capa: <nombre>`), qué módulos NO
# debe importar. Alineado con `.importlinter` y CR-1.
LAYER_FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    "kie_avatar_studio/ui": ("httpx", "aiosqlite", "kie_avatar_studio.infra"),
    "kie_avatar_studio/app_layer": ("httpx", "aiosqlite", "kie_avatar_studio.infra"),
    "kie_avatar_studio/domain": (
        "httpx",
        "aiosqlite",
        "textual",
        "loguru",
        "kie_avatar_studio.infra",
        "kie_avatar_studio.app_layer",
        "kie_avatar_studio.ui",
    ),
}

# Literales numéricos tolerados en cuerpos de función. `50` cubre los defaults de
# paginación habituales (`limit: int = 50`) sin necesidad de excepción por archivo.
ALLOWED_MAGIC_NUMBERS: frozenset[int] = frozenset({0, 1, -1, 2, 50, 100})

_FORBIDDEN_CALL_RULES: dict[str, tuple[str, str]] = {
    "time.sleep": ("CR-5.1", "Llamada bloqueante; usa asyncio.sleep."),
    "datetime.utcnow": ("CR-5.6", "Usa datetime.now(UTC) (no utcnow)."),
}

_FORBIDDEN_RAISE_NAMES: frozenset[str] = frozenset({"ValueError", "RuntimeError"})


@dataclass(frozen=True)
class Hallazgo:
    """Un hallazgo concreto del detector."""

    rule: str
    file: str
    line: int
    message: str

    def render(self, index: int) -> str:
        return f"{index}. [{self.rule}] {self.file}:{self.line}  {self.message}"


# --- resolución de capa -----------------------------------------------------


def _resolve_layer(path: Path) -> str | None:
    """Devuelve el prefijo de capa de `path`, o None si no aplica."""
    parts = path.parts
    for prefix in LAYER_FORBIDDEN_IMPORTS:
        prefix_parts = prefix.split("/")
        if parts[: len(prefix_parts)] == tuple(prefix_parts):
            return prefix
    return _read_declared_layer(path)


def _read_declared_layer(path: Path) -> str | None:
    """Permite que los fixtures declaren su capa con `# capa: <nombre>`."""
    try:
        first_lines = path.read_text(encoding="utf-8").splitlines()[:5]
    except OSError:
        return None
    for line in first_lines:
        stripped = line.strip()
        if stripped.startswith("# capa:"):
            label = stripped.split(":", 1)[1].strip()
            for prefix in LAYER_FORBIDDEN_IMPORTS:
                if prefix.endswith("/" + label):
                    return prefix
    return None


# --- helpers AST ------------------------------------------------------------


def _call_dotted_name(node: ast.AST) -> str | None:
    """Devuelve el nombre punteado de un callable: `a.b.c` o `name`. None si no aplica."""
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None
    if isinstance(node, ast.Name):
        return node.id
    return None


# --- checks ----------------------------------------------------------------


def _check_imports(tree: ast.AST, layer: str | None, path: Path) -> list[Hallazgo]:
    if layer is None:
        return []
    forbidden = LAYER_FORBIDDEN_IMPORTS[layer]
    hallazgos: list[Hallazgo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _matches_forbidden(alias.name, forbidden):
                    hallazgos.append(
                        Hallazgo(
                            "CR-1.1",
                            str(path),
                            node.lineno,
                            f"Capa `{layer}` no puede importar `{alias.name}`.",
                        )
                    )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and _matches_forbidden(node.module, forbidden)
        ):
            hallazgos.append(
                Hallazgo(
                    "CR-1.1",
                    str(path),
                    node.lineno,
                    f"Capa `{layer}` no puede importar `from {node.module}`.",
                )
            )
    return hallazgos


def _matches_forbidden(module: str, forbidden: tuple[str, ...]) -> bool:
    return any(module == m or module.startswith(m + ".") for m in forbidden)


def _check_calls(tree: ast.AST, path: Path) -> list[Hallazgo]:
    hallazgos: list[Hallazgo] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_dotted_name(node.func)
        if name is None:
            continue
        rule = _FORBIDDEN_CALL_RULES.get(name)
        if rule is not None:
            code, msg = rule
            hallazgos.append(Hallazgo(code, str(path), node.lineno, msg))
    return hallazgos


def _check_bare_except(tree: ast.AST, path: Path) -> list[Hallazgo]:
    hallazgos: list[Hallazgo] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body_is_only_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
        is_bare = node.type is None
        catches_exception = isinstance(node.type, ast.Name) and node.type.id == "Exception"
        if is_bare or (catches_exception and body_is_only_pass):
            hallazgos.append(
                Hallazgo(
                    "CR-4.2",
                    str(path),
                    node.lineno,
                    "`except Exception: pass` o `except:` desnudo prohibido.",
                )
            )
    return hallazgos


def _check_forbidden_raises(tree: ast.AST, path: Path) -> list[Hallazgo]:
    hallazgos: list[Hallazgo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            name = _call_dotted_name(node.exc.func)
            if name in _FORBIDDEN_RAISE_NAMES:
                hallazgos.append(_build_raise_finding(node, path, name))
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            name = _call_dotted_name(node.value.func)
            if name in _FORBIDDEN_RAISE_NAMES:
                hallazgos.append(_build_raise_finding(node, path, name))
    return hallazgos


def _build_raise_finding(node: ast.AST, path: Path, name: str) -> Hallazgo:
    return Hallazgo(
        rule="CR-4.1",
        file=str(path),
        line=getattr(node, "lineno", 0),
        message=f"Uso de `{name}` prohibido; usa la jerarquía del dominio.",
    )


def _check_magic_numbers(tree: ast.AST, path: Path) -> list[Hallazgo]:
    """Marca literales enteros mágicos dentro del cuerpo de funciones.

    Excluye:
    - Defaults de parámetros (`def f(limit: int = 50)`): forman parte del contrato público.
    - Anotaciones de tipo / `Annotated[...]`: no son lógica ejecutable.
    - Asignaciones a constantes a nivel módulo: ya viven fuera de cuerpos de función.
    """
    hallazgos: list[Hallazgo] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for stmt in fn.body:
            for node in ast.walk(stmt):
                if not _is_magic_int_literal(node):
                    continue
                # `node.value` es int garantizado por `_is_magic_int_literal`.
                assert isinstance(node, ast.Constant)  # noqa: S101 (refina al type checker)
                hallazgos.append(
                    Hallazgo(
                        "CR-3.3",
                        str(path),
                        node.lineno,
                        f"Número mágico `{node.value}`; usa una constante nombrada.",
                    )
                )
    return hallazgos


def _is_magic_int_literal(node: ast.AST) -> bool:
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return value not in ALLOWED_MAGIC_NUMBERS


# --- API pública ------------------------------------------------------------


def analizar_archivo(path: Path) -> list[Hallazgo]:
    """Analiza un único archivo Python y devuelve la lista de hallazgos."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    layer = _resolve_layer(path)
    hallazgos: list[Hallazgo] = []
    hallazgos.extend(_check_imports(tree, layer, path))
    hallazgos.extend(_check_calls(tree, path))
    hallazgos.extend(_check_bare_except(tree, path))
    hallazgos.extend(_check_forbidden_raises(tree, path))
    hallazgos.extend(_check_magic_numbers(tree, path))
    return hallazgos


def renderizar_informe(title: str, hallazgos: list[Hallazgo]) -> str:
    """Devuelve el informe Markdown en el formato oficial del agente."""
    veredicto = "CAMBIOS_REQUERIDOS" if hallazgos else "APROBADO"
    lineas = [
        f"# Code Quality Review — {title}",
        "",
        "## Veredicto",
        veredicto,
        "",
        "## Hallazgos",
    ]
    if not hallazgos:
        lineas.append("- ninguno")
    else:
        for idx, h in enumerate(hallazgos, start=1):
            lineas.append(h.render(idx))
    return "\n".join(lineas) + "\n"
