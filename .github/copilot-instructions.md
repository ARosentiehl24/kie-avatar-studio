# Kie Avatar Studio — Copilot Instructions

## Objetivo

TUI local en Python (Textual) que automatiza punta a punta la generación de videos con
avatar/lip-sync usando tres APIs de **Kie.ai** encadenadas:

```
script + imagen + voz + prompt
   ↓
File Upload  +  ElevenLabs TTS  →  Kling AI Avatar Pro
   ↓
outputs/<job_id>/final.mp4
```

Pensada para correr en una máquina personal con cola, paralelismo controlado e historial en
SQLite. Toda la doc y comentarios están en **español** — mantén ese idioma al generar texto.

Estado actual: **Fase 1.5 cerrada** (arquitectura por capas + ports, errores tipados,
retries 5xx, cola con recuperación, tooling estricto y agente de revisión embebido).
`docs/CODE_QUALITY.md` es la constitución (reglas `CR-X.Y` que aplica el agente).
`docs/SPEC.md` es la fuente de verdad del comportamiento — consúltalos antes de cambios
no triviales.

## Cómo correr y probar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                  # pytest, pytest-asyncio, ruff, mypy,
                                         # pytest-cov, import-linter, pre-commit
cp .env.example .env                     # poner KIE_API_KEY

python -m kie_avatar_studio              # lanza la TUI
pytest -q                                # toda la suite
pytest tests/test_models.py::test_job_default_status -q   # un solo test

./scripts/check.sh                       # ruff + mypy + import-linter + pytest+cov
./scripts/check.sh fast                  # versión rápida (sin mypy ni cov)
make check                               # alias de check.sh
```

`pytest-asyncio` está en modo **auto** (`pyproject.toml`); no decores los tests async con
`@pytest.mark.asyncio`.

## Arquitectura (lo mínimo a respetar)

Cuatro capas con imports en una sola dirección (ver `docs/ARCHITECTURE.md` y
`docs/CODE_QUALITY.md` §1):

```
ui          → app_layer, domain
app_layer   → domain                (NUNCA infra)   ← JobRunner, QueueManager
infra       → domain (solo DTOs)                    ← KieClient, JobsDB
domain      → nada interno                          ← models, policies, errors, events, ports
app.py      → infra, app_layer, ui, config          ← composition root (única excepción)
```

`.importlinter` codifica estos contratos; cualquier import nuevo que los rompa hace
fallar `pre-commit` y `scripts/check.sh`.

- `app.py` es el composition root: arma `Settings → JobsDB → KieClient → JobRunner →
  QueueManager` y los inyecta. **Sin singletons globales.**
- La UI nunca llama a `KieClient` ni a `JobsDB`; solo usa `queue.enqueue/cancel/retry` y se
  suscribe con `queue.add_listener`.
- Flujo de un job (solo `JobRunner` muta `status`, y siempre persiste antes de seguir):

```
queued → validating → (uploading_image ∥ creating_audio) → waiting_audio
       → creating_avatar → waiting_video → downloading → completed | failed | cancelled
```

`upload_image` y `create_audio` corren en paralelo con `asyncio.gather`. Entre jobs el
paralelismo lo limita `asyncio.Semaphore(settings.max_parallel_jobs)`.

## Convenciones del repo

- **Async-only**: nada de `requests` ni `time.sleep`. Un único `httpx.AsyncClient` compartido en
  `KieClient`; `JobsDB` abre/cierra una conexión `aiosqlite` por operación (WAL mode).
- `from __future__ import annotations` y type hints en todos los módulos.
- `KieClient` es **HTTP puro** (sin validación ni lógica de negocio). Retries solo en 5xx con
  backoff exponencial; 4xx propaga como `KieClientError`. Descargas siempre por streaming.
- Validación de dominio en `domain/policies.py`. Límites duros de Kie: script y prompt ≤ 5000
  chars, imagen png/jpg ≤ 10 MB, audio ≤ 100 MB / 5 min.
- Config con `pydantic-settings` leyendo `.env` (ver `.env.example`). `Settings.ensure_dirs()`
  crea `data/ outputs/ inputs/ presets/ logs/`. No introducir un segundo mecanismo de config.
- Logs con `loguru`. Nunca loguear `KIE_API_KEY`; redactar `Authorization` como `Bearer ***`.
- Tests: cero llamadas reales a Kie — siempre `httpx.MockTransport`.

## Clean Code y SOLID (regla permanente, no negociable)

Todo cambio — nuevo o refactor — se evalúa contra estas reglas. Si una "solución rápida" las
viola, hacer primero el refactor que las restaure.

**SOLID aplicado a este repo:**

- **SRP** — cada módulo tiene UNA razón para cambiar. Ejemplos vigentes:
  `KieClient` = solo HTTP, `JobsDB` = solo persistencia, `JobRunner` = solo state machine,
  `QueueManager` = solo concurrencia, `policies` = solo validación. No mezclar.
- **OCP** — agregar un nuevo modelo de Kie, una nueva fuente de batch, o una nueva pantalla
  debe ser una clase/módulo nuevo, no editar el contrato de los existentes.
- **LSP** — cualquier doble de `KieClient` o `JobsDB` usado en tests debe respetar la misma
  firma async y los mismos tipos de retorno/excepción.
- **ISP** — si una pantalla solo necesita `enqueue`, no le pases todo `QueueManager`; expón un
  `Protocol` mínimo en `domain/` y dependé de él.
- **DIP** — el composition root (`app.py`) es el único lugar que conoce las clases concretas
  de infra. Las capas superiores dependen de tipos del `domain/`, nunca importan `httpx`,
  `aiosqlite`, ni `textual` desde `domain/` o `app_layer/`.

**Clean Code aplicado a este repo:**

- Funciones cortas con una sola responsabilidad. Si `JobRunner.run` crece, partir en métodos
  privados (`_upload_image`, `_create_audio`, `_poll_for_url`, …) como ya está hecho.
- Nombres descriptivos **en español** consistentes con el resto del código y la doc.
- Sin números mágicos: timeouts, tamaños y reintentos viven en `Settings` o en constantes
  nombradas en `domain/policies.py`, nunca inline.
- Comentar el **por qué**, no el qué. Los docstrings van en español y describen el contrato,
  no la implementación.
- Sin código muerto, sin `TODO` sin contexto: cada `TODO` referencia una fase del roadmap
  (ej. `# TODO(Fase 2): confirmar shape de recordInfo`).
- Manejo de errores explícito: nada de `except Exception: pass`. `JobRunner` es el único
  punto que captura todo para marcar `FAILED`; el resto deja propagar excepciones tipadas.
- Tests primero cuando agregás lógica de dominio o de cliente (`MockTransport` para HTTP,
  fixtures en `conftest.py`).
- Cero duplicación: si dos pantallas formatean un `VideoJob` igual, el formateo va a
  `domain/` o a un helper en `ui/`.

## Referencias

- `docs/CODE_QUALITY.md` — constitución del proyecto (reglas `CR-X.Y` no negociables).
- `docs/SPEC.md` — spec maestra (state machine, schemas, contratos, checklist).
- `docs/ARCHITECTURE.md` — reglas de capas, ciclo de vida del job, plantilla ADR.
- `docs/ROADMAP.md` — fase actual y siguiente.
- `docs/API_KIE.md` — endpoints, payloads y códigos de error de Kie.
- `docs/agents/code-quality-reviewer.prompt.md` — agente que revisa todo cambio (prompt
  canónico). Perfiles generados en `.opencode/agents/code-quality-reviewer.md` (OpenCode,
  `mode`/`permission`) y `.github/agents/code-quality-reviewer.agent.md` (Copilot CLI,
  `tools[]`). Sincronización validada por `scripts/check_agent_sync.sh`; regeneración
  con `scripts/build_agent_profiles.sh`.

## Code intelligence (CodeGraph MCP) — OBLIGATORIO

> **Esta no es una sugerencia. Es la primera línea de búsqueda del repo.**
> Las herramientas `codegraph_*` no son "una opción más" junto a grep/view:
> son el **default obligatorio** para toda pregunta sobre código en este
> proyecto. Caer a grep/view sin justificación es un anti-patrón.

El repo se sirve por **CodeGraph** vía MCP. Tanto Copilot CLI como OpenCode lo cargan
automáticamente desde el workspace:

| Cliente | Archivo cargado |
|---|---|
| Copilot CLI | `.mcp.json` (raíz del repo) |
| OpenCode    | `opencode.jsonc` (raíz del repo) |

Índice local en `.codegraph/codegraph.db` (no commiteado). Tras un clone:

```bash
codegraph init -i        # crea + indexa
codegraph sync           # incremental tras cambios
codegraph status         # salud del índice
```

### Regla MUST-USE — sin excepciones por inercia

Si la pregunta o la tarea es **sobre código** (símbolos, callers/callees, flujo
"cómo X llega a Y", impacto de cambiar algo, exploración inicial de un módulo,
investigación de un bug, planificación de un refactor), la **primera tool que
disparás es `codegraph_*`**. No `grep`, no `view`, no `glob`. Punto.

Antes de cada batch de tool calls que toque código, hacé este check mental:

```
¿Voy a usar grep/view/glob para buscar algo en el código?
   └─ SÍ ── ¿Hay un codegraph_* que responda lo mismo?
                ├─ SÍ ── Usá codegraph_*. Si después necesitás confirmar
                │        un string/comentario puntual, ahí sí grep/view.
                └─ NO ── Documentá brevemente por qué (string literal,
                         archivo no-Python fuera del índice, etc).
```

### Mapeo de intención → tool

| Intención | Tool obligatoria (primera llamada) |
|---|---|
| Contexto de una tarea / "¿cómo funciona X?" / planificación | `codegraph_context` (**PRIMARY** — usá esta antes que nada) |
| Buscar símbolo por nombre | `codegraph_search` |
| Trazar flujo `X → Y` (request→handler, update→render, etc.) | `codegraph_trace` |
| Quién llama a esto / a quién llama | `codegraph_callers` / `codegraph_callees` |
| Impacto de cambiar/renombrar un símbolo | `codegraph_impact` |
| Ver código de varios símbolos relacionados | `codegraph_explore` (una sola llamada, no chained `view`s) |
| Ver código de un símbolo puntual con su trail | `codegraph_node` (con `includeCode=true` si necesitás el body) |

### Cuándo caer a grep/view/glob (excepciones legítimas, documentar)

Solo en estos casos:

1. **Strings o comentarios literales**: error messages, IDs CSS, claves de
   diccionarios, palabras dentro de logs — CodeGraph indexa símbolos, no
   strings arbitrarios.
2. **Archivos fuera del índice**: `.tcss`, `.md`, `.yml`, `.json`, configs
   (CodeGraph indexa Python + YAML, ver `codegraph_status`).
3. **Pending sync**: si `codegraph_status` reporta archivos pendientes y el
   símbolo que necesitás está ahí, usá `view` directo hasta que el sync corra.
4. **Confirmación rápida** (≤1 call): después de que CodeGraph te dio el
   símbolo + ubicación, podés abrir el archivo con `view` para ver el contexto
   visual completo si te ayuda. No es reemplazo, es complemento.

### Anti-patrones (errores observados, no repetir)

- ❌ `grep -rn "copy_to_clipboard" --include="*.py"` para encontrar callers
  → ✅ `codegraph_callers` sobre el símbolo.
- ❌ `grep -rn "KIE_FILE_RETENTION_DAYS"` para evaluar impacto de un rename
  → ✅ `codegraph_impact` sobre la constante.
- ❌ Cadena `grep` + `view` + `view` + `view` para entender un flujo
  → ✅ `codegraph_context "descripción de la tarea"` UNA sola llamada.
- ❌ `view` con `view_range` repetidos en el mismo archivo
  → ✅ `codegraph_explore` con los símbolos que te interesan.
- ❌ "Ya conozco el codebase, no necesito CodeGraph" (108 archivos = sí, lo
  necesitás).

### Mantener el índice fresco

El MCP server arranca con file watcher (`codegraph serve --mcp`, sin `--no-watch`)
así que el grafo se actualiza solo ~1 s después de cada edit. Para casos donde el
watcher no alcanza — `git pull` con muchos cambios, `git checkout` entre ramas,
scaffolding masivo — usar el comando del cliente que corresponda:

| Cliente | Invocación | Definición |
|---|---|---|
| Copilot CLI | `/skill codegraph-sync` | `.github/skills/codegraph-sync/SKILL.md` |
| OpenCode    | `/codegraph-sync` (opcional `full` para reindex completo) | `.opencode/commands/codegraph-sync.md` |

Ambos hacen lo mismo: verifican instalación, deciden entre `sync` (incremental,
default) e `index` (full rebuild), y muestran el `status` antes/después. Comandos
equivalentes a mano:

```bash
codegraph status                # cuántos archivos pendientes + edad del índice
codegraph sync                  # incremental (default)
codegraph index                 # full reindex (solo tras renames masivos)
codegraph unlock                # liberar lock si quedó colgado
```

## Agente de revisión

Antes de pedir review, invoca al agente sobre tu cambio:

```text
TUI (OpenCode):  /agent code-quality-reviewer   ← .opencode/agents/code-quality-reviewer.md
CLI (Copilot):   /agent code-quality-reviewer   ← .github/agents/code-quality-reviewer.agent.md
```

El prompt vive como fuente única en `docs/agents/code-quality-reviewer.prompt.md`. Cada
sistema tiene su propio frontmatter (OpenCode usa `mode`+`permission`, Copilot usa
`tools[]` y sufijo `.agent.md`). Para regenerar los perfiles desde la fuente:

```bash
./scripts/build_agent_profiles.sh
```

`scripts/check_agent_sync.sh` valida en pre-commit que el cuerpo de los tres archivos
coincida.

El agente devuelve un informe Markdown con veredicto `APROBADO` o `CAMBIOS_REQUERIDOS`,
citando la regla `CR-X.Y` exacta de cada hallazgo.
