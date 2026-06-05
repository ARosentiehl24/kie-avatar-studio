#!/usr/bin/env bash
# Lanza Kie Avatar Studio. Bootstrap idempotente:
#   - Crea .venv si no existe
#   - Instala/actualiza deps de requirements.txt
#   - Copia .env.example -> .env si falta (avisa para editarlo)
#   - Ejecuta `python -m kie_avatar_studio`
#
# Uso:
#   ./run.sh                  # lanza la TUI
#   ./run.sh --reinstall      # fuerza reinstalación de deps
#   ./run.sh --dev            # instala también extras [dev] (pytest, ruff, mypy)
#   ./run.sh -- pytest -q     # ejecuta otro comando dentro del venv (todo tras `--`)

set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"
PY_BIN="${PYTHON:-python3}"
REINSTALL=0
DEV=0
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reinstall) REINSTALL=1; shift ;;
    --dev)       DEV=1; shift ;;
    --)          shift; PASSTHROUGH=("$@"); break ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *)
      echo "Opción desconocida: $1" >&2
      echo "Usá '--' para pasar argumentos a otro comando, ej: ./run.sh -- pytest -q" >&2
      exit 2 ;;
  esac
done

if ! command -v "$PY_BIN" >/dev/null 2>&1; then
  echo "✖ No se encontró '$PY_BIN'. Instalá Python 3.11+ o exportá PYTHON=/ruta/a/python." >&2
  exit 1
fi

PY_OK=$("$PY_BIN" -c 'import sys; print(1 if sys.version_info[:2] >= (3,11) else 0)')
if [[ "$PY_OK" != "1" ]]; then
  echo "✖ Se requiere Python 3.11+. Versión detectada: $($PY_BIN --version)" >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "▸ Creando entorno virtual en $VENV_DIR …"
  "$PY_BIN" -m venv "$VENV_DIR"
  REINSTALL=1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

STAMP="$VENV_DIR/.deps.stamp"
NEED_INSTALL=0
if [[ $REINSTALL -eq 1 ]]; then
  NEED_INSTALL=1
elif [[ ! -f "$STAMP" ]] || [[ requirements.txt -nt "$STAMP" ]] || [[ pyproject.toml -nt "$STAMP" ]]; then
  NEED_INSTALL=1
fi

if [[ $NEED_INSTALL -eq 1 ]]; then
  echo "▸ Instalando dependencias …"
  python -m pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
  if [[ $DEV -eq 1 ]]; then
    pip install -e ".[dev]"
  else
    pip install -e . >/dev/null
  fi
  touch "$STAMP"
fi

if [[ ! -f ".env" ]]; then
  echo "▸ .env no existe — copiando desde .env.example."
  cp .env.example .env
  echo "  ⚠ Editá .env y completá KIE_API_KEY antes de crear jobs reales."
fi

if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
  echo "▸ Ejecutando: ${PASSTHROUGH[*]}"
  exec "${PASSTHROUGH[@]}"
fi

echo "▸ Lanzando Kie Avatar Studio …"
exec python -m kie_avatar_studio
