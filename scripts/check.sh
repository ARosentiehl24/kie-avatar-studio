#!/usr/bin/env bash
# scripts/check.sh — corre toda la validación local en orden, sin atajos.
# Espejo de lo que debería correr CI antes de mergear.
#
# Uso:
#   ./scripts/check.sh            # corre todo
#   ./scripts/check.sh fast       # omite cobertura y mypy (loop rápido)
#
# Requisitos:
#   pip install -e ".[dev]"

set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-full}"
PKG="kie_avatar_studio"

echo "==> ruff lint"
ruff check .

echo "==> ruff format --check"
ruff format --check .

if [[ "$MODE" != "fast" ]]; then
  echo "==> mypy --strict (paquete)"
  mypy "$PKG"
fi

echo "==> import-linter (contratos de capas)"
lint-imports

echo "==> agente code-quality-reviewer sincronizado"
./scripts/check_agent_sync.sh

if [[ "$MODE" == "fast" ]]; then
  echo "==> pytest -q"
  pytest -q
else
  echo "==> pytest + coverage"
  pytest -q --cov="$PKG" --cov-report=term-missing
fi

echo
echo "OK — todos los checks pasaron"
