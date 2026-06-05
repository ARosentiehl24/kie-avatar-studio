#!/usr/bin/env bash
# Verifica que el "cuerpo" (prompt) del agente code-quality-reviewer esté sincronizado
# entre los tres archivos:
#
#   docs/agents/code-quality-reviewer.prompt.md          (fuente, sin frontmatter)
#   .opencode/agents/code-quality-reviewer.md            (frontmatter OpenCode)
#   .github/agents/code-quality-reviewer.agent.md        (frontmatter Copilot CLI)
#
# Los frontmatters difieren por diseño (OpenCode usa `mode` + `permission`, Copilot usa
# `tools` como lista y sufijo `.agent.md`). Lo que debe coincidir es el prompt.
#
# Uso:
#   ./scripts/check_agent_sync.sh

set -euo pipefail
cd "$(dirname "$0")/.."

PROMPT="docs/agents/code-quality-reviewer.prompt.md"
OPENCODE=".opencode/agents/code-quality-reviewer.md"
COPILOT=".github/agents/code-quality-reviewer.agent.md"

if [[ ! -f "$PROMPT" ]]; then
  echo "ERROR: falta la fuente $PROMPT" >&2
  exit 1
fi

# Extrae el body después del segundo `---` del frontmatter; si no hay frontmatter,
# devuelve el archivo completo. Descarta líneas vacías iniciales para tolerar el
# espacio cosmético entre frontmatter y prompt.
extract_body() {
  awk '
    BEGIN { in_fm = 0; past_fm = 0; emitted = 0 }
    NR == 1 && $0 == "---" { in_fm = 1; next }
    in_fm && $0 == "---" { in_fm = 0; past_fm = 1; next }
    in_fm { next }
    !emitted && $0 == "" { next }
    { emitted = 1; print }
  ' "$1"
}

prompt_hash=$(extract_body "$PROMPT" | sha256sum | awk '{print $1}')

fail=0
for path in "$OPENCODE" "$COPILOT"; do
  if [[ ! -f "$path" ]]; then
    echo "ERROR: falta $path" >&2
    fail=1
    continue
  fi
  body_hash=$(extract_body "$path" | sha256sum | awk '{print $1}')
  if [[ "$prompt_hash" != "$body_hash" ]]; then
    echo "ERROR: el cuerpo de $path difiere de $PROMPT" >&2
    echo "       regenera con: scripts/build_agent_profiles.sh" >&2
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "OK: prompt sincronizado en docs/, .opencode/agents/ y .github/agents/"
