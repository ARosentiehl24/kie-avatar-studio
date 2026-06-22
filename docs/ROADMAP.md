# Roadmap

## Fase 0 – Esqueleto (HECHO)

- [x] Estructura de paquete y stubs (`config`, `models`, `kie_client`,
      `job_runner`, `queue_manager`, `db`, `app`).
- [x] `pyproject.toml`, `requirements.txt`, `.env.example`, `.gitignore`.
- [x] Tests básicos.
- [x] Documentación: `README`, `docs/SPEC.md`, `docs/ARCHITECTURE.md`,
      `docs/ROADMAP.md`, `docs/API_KIE.md`, ADRs 0001-0003.

## Fase 1 – Reorganizar y endurecer (HECHO)

- [x] Layout final `domain/infra/app_layer/ui` con `app.py` como composition
      root.
- [x] `domain/policies.py` con `validate_job` y constantes nombradas.
- [x] Errores tipados (`KieError` y subclases, `JobValidationError`) en
      `domain/errors.py`.
- [x] `JobsDB` con `get`, `list_by_status`, `delete`, `PRAGMA journal_mode=WAL`
      y helpers `_row_to_job` / `_job_to_row`.
- [x] `KieClient` con retries 5xx + backoff exponencial, errores tipados,
      `_request_json` centralizado, streaming en `download_file`.
- [x] `QueueManager` con `cancel`, `retry`, `restore_pending`, listeners sync +
      async.
- [x] Recuperación automática al arrancar.
- [x] Tests con `httpx.MockTransport`. 38 passed antes del enforcement.

## Fase 1.5 – Enforcement (HECHO)

- [x] `docs/CODE_QUALITY.md` (constitución del proyecto con reglas CR-X.Y).
- [x] `docs/adr/0004-layered-architecture-with-ports.md`.
- [x] Agente `code-quality-reviewer` con fuente única
      `docs/agents/code-quality-reviewer.prompt.md` y perfiles generados en
      `.opencode/agents/code-quality-reviewer.md` (OpenCode `mode`/`permission`)
      y `.github/agents/code-quality-reviewer.agent.md` (Copilot CLI `tools[]`).
      Sincronización validada por hash con `scripts/check_agent_sync.sh`;
      regeneración con `scripts/build_agent_profiles.sh`.
- [x] `pyproject.toml` con `ruff` estricto, `mypy --strict`, `pytest-cov`,
      `import-linter`, `pre-commit`.
- [x] `.importlinter` con contratos de capas.
- [x] `.pre-commit-config.yaml` (ruff + ruff-format + mypy + import-linter +
      pytest -q).
- [x] `scripts/check.sh` y `Makefile` con `check` / `check-fast`.
- [x] Smoke test del agente con `tests/agent_fixtures/{bad,good}_feature.py` y
      `quality_detector.py`. 44 passed.

## Fase 2 – End to end real

- [x] Confirmar shape de `/api/v1/jobs/recordInfo` con un task real (commit
      `4ad9a8f`: `state` + `resultJson` parser).
- [x] Documentar normalización de status (`domain/policies.py`
      `extract_task_status` / `extract_result_url`).
- [x] `JobRunner.poll_for_url` final (vía `app_layer/polling.py`).
- [x] **Cola estructurada de audios** (ADR-0007): `AudioJob` modelo +
      `AudioJobsDB` + `AudioJobRunner` + `audio_queue` (QueueManager genérico).
      Restore al arrancar. Cancelar/reintentar. (Commits `8846126`, `3dc98d9`,
      `f7f96cf`.)
- [x] **Pantalla `audios` con cola live** (commit `44009b7`): tabla unificada de
      `AudioJob` (todos los estados), panel de contadores en vivo, suscripción
      al stream del queue, acciones Cancelar / Reintentar / Quitar.
- [x] **Pantalla `history` unificada** (commit `484640e`): tabla video + audio
      con refresh en vivo y filtros.
- [x] **Pantalla `new_job`** (Modo B — commit
      `0008-video-from-existing-assets`): `VideosScreen` con cola visible +
      modal `NewVideoFormScreen` que toma imagen + audio ya en Kie + prompt →
      encola el video. El runner saltea upload y TTS si las URLs ya están
      pobladas. Modo "from scratch" (foto local + script + voz) queda en Fase 3.
- [ ] Pantalla `job_detail` con stream de logs por job.
- [ ] Quickstart probado punta a punta con un sample.

## Fase 3 – Batch + presets + UX

- [ ] `BatchLoader` lee `batch_jobs/video_NNN/`.
- [ ] Salida por job en
      `outputs/<id>/{final.mp4, audio.json, video.json, job.json}`.
- [ ] Pantalla `presets` para editar voces/prompts.
- [ ] Pantalla `settings` que edita `.env` con confirmación.
- [ ] Validar `policies.is_path_inside(OUTPUTS_DIR)` en escrituras de batch
      (CR-7.2).

## Fase 4 – Calidad y release

- [ ] Subir `fail_under` de cobertura a 80 %
      (`pyproject.toml [tool.coverage.report]`).
- [ ] CHANGELOG.md.
- [ ] Empaquetar 0.1.0 (`pipx install .`).
- [ ] Capturas para README.
- [ ] Workflow MANUAL: preservar `task_id` del AudioJob persistido en re-pause
      para evitar regeneración O(N²) de TTS por step b-roll. Ver
      `kie_avatar_studio/app_layer/workflow_step_runner.py::_build_audio_job`.

## Fase 5 – Opcionales

- [ ] Servidor HTTP local (FastAPI) reusando `app_layer`.
- [ ] UI web mínima.
- [ ] Callbacks de Kie usando tunnel (`cloudflared`) en vez de polling.
- [ ] Notificaciones (Telegram / desktop).
