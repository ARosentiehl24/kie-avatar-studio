#!/usr/bin/env bash
# Regenera los perfiles del agente code-quality-reviewer desde el prompt canónico.
#
#   Fuente : docs/agents/code-quality-reviewer.prompt.md
#   Salida : .opencode/agents/code-quality-reviewer.md
#            .github/agents/code-quality-reviewer.agent.md
#
# Llamalo cuando edites el prompt o cuando cambien los frontmatters.

set -euo pipefail
cd "$(dirname "$0")/.."

PROMPT="docs/agents/code-quality-reviewer.prompt.md"
OPENCODE=".opencode/agents/code-quality-reviewer.md"
COPILOT=".github/agents/code-quality-reviewer.agent.md"

if [[ ! -f "$PROMPT" ]]; then
  echo "ERROR: falta $PROMPT" >&2
  exit 1
fi

mkdir -p "$(dirname "$OPENCODE")" "$(dirname "$COPILOT")"
BODY=$(cat "$PROMPT")

# OpenCode: frontmatter usa mode + permission (la API de `tools` quedó deprecada).
# Ref: https://opencode.ai/docs/agents/
cat > "$OPENCODE" <<EOF
---
description: Revisor experto de calidad y arquitectura de Kie Avatar Studio. Analiza diffs y archivos contra docs/CODE_QUALITY.md y docs/ARCHITECTURE.md, emite un informe Markdown citando cada regla (CR-X.Y) y devuelve veredicto APROBADO o CAMBIOS_REQUERIDOS. No modifica código.
mode: subagent
model: github-copilot/claude-opus-4.7-1m-internal
temperature: 0.1
permission:
  edit: deny
  bash: deny
  webfetch: ask
---

${BODY}
EOF

# Copilot CLI: archivo con sufijo `.agent.md` y `tools` como lista de strings.
# Ref: https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/create-custom-agents
cat > "$COPILOT" <<EOF
---
name: code-quality-reviewer
description: Revisor experto de calidad y arquitectura de Kie Avatar Studio. Analiza diffs y archivos contra docs/CODE_QUALITY.md y docs/ARCHITECTURE.md, emite un informe Markdown citando cada regla (CR-X.Y) y devuelve veredicto APROBADO o CAMBIOS_REQUERIDOS. No modifica código.
tools: ["read", "search", "grep", "glob", "view"]
---

${BODY}
EOF

echo "OK: regenerados $OPENCODE y $COPILOT desde $PROMPT"
