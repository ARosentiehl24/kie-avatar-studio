# Kie Avatar Studio

App local **TUI en Python** para automatizar la generación de videos con avatar/lip-sync
usando las APIs de Kie.ai:

- Kie File Upload API
- Kie ElevenLabs Text-to-Speech
- Kie Kling AI Avatar Pro

Basada en `~/Documents/notas/kie-avatar-tui-plans-from-openclaw-workspace.md`.

## Estado

Fase 1.5 cerrada: arquitectura por capas + ports, errores tipados, retries 5xx, cola con
recuperación, tooling estricto y agente de revisión embebido. La TUI todavía es un menú
mínimo; las pantallas funcionales son la Fase 2.

## Stack

- Python 3.11+
- [Textual](https://textual.textualize.io/) para la UI
- `httpx`, `aiosqlite`, `pydantic`, `pydantic-settings`, `loguru`, `rich`
- Calidad: `ruff`, `mypy --strict`, `import-linter`, `pytest`, `pre-commit`

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env         # editar y poner KIE_API_KEY

python -m kie_avatar_studio  # lanza la TUI
make check                   # ruff + mypy + import-linter + pytest+cov
make check-fast              # versión rápida (sin mypy ni cov)
```

En Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
python -m kie_avatar_studio
scripts\check.sh             :: requiere Git Bash o WSL
```

## Code intelligence (CodeGraph)

El repo viene cableado con **CodeGraph** vía MCP para que cualquier agente con soporte MCP
(Copilot CLI, OpenCode, Claude Code, Cursor…) consulte el grafo del código en vez de hacer
grep ciego. Reduce ~25% el costo y ~62% las tool calls de exploración.

Instalación una sola vez por máquina:

```bash
pnpm i -g @colbymchenry/codegraph        # o npm i -g, o el install.sh standalone
codegraph init -i                        # crea + indexa el repo (.codegraph/codegraph.db)
```

Los archivos `.mcp.json` (Copilot CLI) y `opencode.jsonc` (OpenCode) ya están en el repo;
cada cliente carga su archivo automáticamente al arrancar. Mantener el índice fresco:

```bash
codegraph sync         # incremental
codegraph status       # salud + archivos pendientes
```

Si tu cliente MCP está activo, podés delegar la decisión `sync` vs `index` a un
comando preconfigurado:

| Cliente | Invocación |
|---|---|
| Copilot CLI | `/skill codegraph-sync` |
| OpenCode    | `/codegraph-sync` (o `/codegraph-sync full` para reindex completo) |

Tools preferidas vs grep/view: `codegraph_context` (PRIMARY), `codegraph_search`,
`codegraph_trace`, `codegraph_callers`, `codegraph_impact`, `codegraph_explore`,
`codegraph_node`. Ver `.github/copilot-instructions.md` para la guía completa.

## Estructura

```text
kie_avatar_studio/
  app.py                composition root (única excepción a las reglas de capas)
  config.py
  domain/               cero imports internos (models, policies, errors, events, ports)
  infra/                solo importa domain (kie_client, db, logging)
  app_layer/            solo importa domain (job_runner, queue_manager, ids)
  ui/                   solo importa domain + app_layer (menu, screens, styles.tcss)
docs/
  SPEC.md                  spec maestra
  ARCHITECTURE.md          capas, dependencias y ciclo de vida del job
  CODE_QUALITY.md          constitución (reglas CR-X.Y que aplica el agente)
  API_KIE.md               endpoints y restricciones de Kie.ai
  ROADMAP.md
  agents/
    code-quality-reviewer.md   perfil canónico del agente
  adr/                         registros de decisiones
.opencode/agents/, .github/agents/                copias verificadas por hash
.importlinter, .pre-commit-config.yaml, Makefile, scripts/
data/, outputs/, inputs/, presets/, logs/, batch_jobs/
tests/
  agent_fixtures/{bad,good}_feature.py
  test_agent_smoke.py + suite de domain/infra/app_layer/ui
```

## Cómo contribuir

1. `pip install -e ".[dev]" && pre-commit install`
2. Lee `docs/CODE_QUALITY.md`. Toda regla tiene un código `CR-X.Y`.
3. Antes de pedir review:

```bash
./scripts/check.sh
```

   o `make check`. Si CI/pre-commit falla, el agente
   `code-quality-reviewer` te explica por qué citando la regla.

4. Para invocar al agente sobre tu cambio:

```text
TUI (OpenCode):  /agent code-quality-reviewer   ← lee .opencode/agents/
CLI (Copilot):   /agent code-quality-reviewer   ← lee .github/agents/
```

5. El prompt es **único** en `docs/agents/code-quality-reviewer.prompt.md`. Cada
   sistema tiene su propio frontmatter:

```text
.opencode/agents/code-quality-reviewer.md          (OpenCode, mode/permission)
.github/agents/code-quality-reviewer.agent.md      (Copilot CLI, tools[])
```

   Para regenerar ambos desde la fuente:

```bash
./scripts/build_agent_profiles.sh
```

   `scripts/check_agent_sync.sh` valida en pre-commit que los cuerpos coincidan.

## Variables de entorno

Ver `.env.example`.

```env
KIE_API_KEY=
KIE_API_BASE=https://api.kie.ai
KIE_UPLOAD_BASE=https://kieai.redpandaai.co
MAX_PARALLEL_JOBS=2
POLL_INTERVAL_SECONDS=10
TASK_TIMEOUT_SECONDS=1800
DEFAULT_VOICE=EkK5I93UQWFDigLMpZcX
LOG_LEVEL=INFO
DATA_DIR=./data
OUTPUTS_DIR=./outputs
INPUTS_DIR=./inputs
PRESETS_DIR=./presets
LOGS_DIR=./logs
```

`KIE_API_KEY` queda como **fallback**: si configurás keys en la pantalla
**Configuración** (`/C`), se guardan en `data/keys.json` (`chmod 0o600`) y la
key activa sobrescribe a la del `.env`. La pantalla también permite editar
endpoints, paralelismo, polling y defaults, todo persistido en `.env` con
backup `.env.bak`.

## Flujo de un job

```text
validate ─► upload_image  ┐
            create_audio  ┘─► wait_audio ─► create_avatar ─► wait_video ─► download ─► completed
```

`upload_image` y `create_audio` corren en paralelo dentro del mismo job
(`asyncio.gather`). Entre jobs, el paralelismo lo limita
`asyncio.Semaphore(settings.max_parallel_jobs)`.

## Estados del job

```text
queued, validating, uploading_image, creating_audio, waiting_audio,
creating_avatar, waiting_video, downloading, completed, failed, cancelled
```

Solo `JobRunner` muta `VideoJob.status`. Patrón write-ahead:
asignar → `await repository.upsert(job)` → notificar listeners.

## Restricciones de Kie

```text
script max chars        : 5000
prompt max chars        : 5000
imagen formatos         : jpeg, png
imagen tamaño max       : 10 MB
audio tamaño max        : 100 MB
audio duración max      : 5 min
```

## Referencias

- `docs/SPEC.md` — comportamiento detallado (state machine, schemas, contratos)
- `docs/ARCHITECTURE.md` — capas, ports, ciclo de vida del job
- `docs/CODE_QUALITY.md` — reglas que aplica el agente
- `docs/API_KIE.md` — endpoints de Kie
- Plan original: `~/Documents/notas/kie-avatar-tui-plans-from-openclaw-workspace.md`
