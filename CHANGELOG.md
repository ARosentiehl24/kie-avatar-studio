# Changelog

Todas las entradas siguen el esquema de versionado descrito en
[`docs/VERSIONING.md`](docs/VERSIONING.md): **L** → MAJOR, **M** →
MINOR, **S** → PATCH.

## [Unreleased]

_Nada todavía. Próximos cambios se anotan acá antes del release._

---

## [1.0.1] — 2026-06-05

Hotfix del .exe de v1.0.0, que no arrancaba en ningún caso (ni standalone
ni instalado vía Inno Setup). Sin cambios funcionales user-visible: la
TUI corre exactamente igual en modo dev.

### Fixed (S)

- **Build de Windows .exe**: `dist/KieAvatarStudio.exe` fallaba al
  arrancar con `ImportError: attempted relative import with no known
  parent package` porque PyInstaller corría `kie_avatar_studio/__main__.py`
  como módulo top-level (`__main__`), sin paquete padre, y rompía los
  imports relativos del paquete. Se introdujo `packaging/entry.py` como
  wrapper con import absoluto y se actualizó `packaging/kie_avatar_studio.spec`
  para apuntar al wrapper (más paths absolutos derivados de `SPECPATH`
  para que la build sea independiente del CWD). También se agregó
  `collect_all('textual')` + `collect_submodules('pydantic'/'pydantic_settings')`
  porque `textual.widgets` lazy-loadea sus submódulos (`_tab_pane`, etc.)
  vía `__getattr__` y el analizador estático de PyInstaller no los veía.
  Test guardrail nuevo en `tests/test_main_entry.py`.
- **`.exe` instalado en Program Files**: `Settings.ensure_dirs()` usaba
  paths relativos al CWD (`./data`, `./logs`, ...). Al lanzar el shortcut
  generado por Inno Setup, el CWD era `C:\Program Files\Kie Avatar Studio\`
  → no-writable para usuarios sin admin → la app explotaba apenas
  intentaba crear los directorios. `config.py` ahora detecta
  `sys.frozen` y resuelve los defaults a `%LOCALAPPDATA%\KieAvatarStudio\`
  en Windows, `~/Library/Application Support/KieAvatarStudio/` en macOS,
  y `$XDG_DATA_HOME/KieAvatarStudio/` (o `~/.local/share/...`) en Linux.
  El `.env` queda en la misma raíz (resuelto vía `data_dir.parent` en
  `app.py:150`, sin cambios ahí). En modo dev (`python -m kie_avatar_studio`)
  el comportamiento NO cambia: paths siguen relativos al CWD. Tests
  en `tests/test_config.py`.

---

## [1.0.0] — 2026-06-05

Primera versión funcional completa. Las 10 pantallas del menú principal
están implementadas y operativas; pipeline end-to-end probado;
notificaciones del SO cross-platform; suite de 474 tests verdes.

### Added (M)

- **Pantalla Nuevo video** (`n`): flujo end-to-end image + script + voz
  + prompt → MP4 final.
- **Pantalla Procesar lote** (`b`): `BatchLoader` lee `batch_jobs/`
  con `script.txt` + `modelo.<ext>` (+ `prompt.txt`, `voice.txt`,
  `meta.json` opcionales). Encolado masivo válido / individual.
- **Pantalla Cola de trabajos** (`g`): vista unificada de video+audio
  jobs con acciones bulk cancel/retry.
- **Pantalla Historial** (`h`): jobs terminales unificados.
- **Pantalla Imágenes** (`i`): upload, validación, contador de saldo.
- **Pantalla Audios** (`a`): generación TTS, reproducción, copia de
  URL, presets cargables, contador de saldo.
- **Pantalla Presets** (`p`): CRUD file-based JSON para voice presets
  reusables (voice_id + 5 voice_settings + label).
- **Pantalla Configuración** (`c`): multi-perfil de API keys (CRUD,
  test, switch active) + edición de `.env`.
- **Pantalla Logs** (`l`): tail del log de la sesión.
- **Notificaciones del SO** cross-platform al terminar un job
  (`COMPLETED`/`FAILED`): Linux (`notify-send`), macOS (`osascript`),
  Windows 10+ (PowerShell + WinRT). `NOTIFICATIONS_ENABLED` en `.env`.
- **Copy-to-clipboard robusto** multi-backend: `wl-copy` / `xclip` /
  `xsel` / `pbcopy` / `clip.exe` + OSC 52 como fallback.
- **Reproductor de audio** con cadena `mpv` → `ffplay` → `mpg123` →
  fallback al launcher del SO.
- **Cola estructurada** con paralelismo limitado (`max_parallel_jobs`)
  por `asyncio.Semaphore` compartido entre video y audio queues.
- **Persistencia y restore_pending**: jobs en progreso al cerrar la
  app se reanudan al volver a abrir.
- **Sistema de colores semántico** para botones (primary/info/warning/
  error/glyph/filter) — documentado en `.github/skills/tui-designer`.
- **Validaciones de dominio** alineadas con límites duros de Kie
  (script ≤ 5000, prompt ≤ 5000, imagen ≤ 10 MB, audio ≤ 100 MB / 5 min).
- **Retención automática** de assets en Kie según TTL (24h imágenes,
  14d audios generados).

### Changed (M)

- `KieClient.__init__` degrada el warning de `KIE_API_KEY` vacío a
  `DEBUG`; el `WARNING` real solo se emite en `on_mount` si tras
  aplicar `keys.json` la key sigue vacía.
- Mensajes de "Copiar URL" simplificados a una línea (las URLs largas
  de Kie inflaban los toasts).
- Pre-commit: ruff `0.6.9` → `0.15.15`, mypy `1.11.2` → `1.13.0`.

### Fixed (S)

- CSS de `#audios-credits` / `#images-credits`: `height: 1` + `padding:
  2 4` recortaba el texto haciendo invisible el contador. Ajustado a
  `height: auto` + `padding: 0 4` + `margin-top: 1`.
- Glifos `⊘` / `↻` (que algunas fuentes renderizaban como cajas
  vacías) reemplazados por `✖` / `🔄`.
- `.gitignore`: `presets/voices/*.json` para no commitear data del
  usuario.

### Arquitectura

- 4 capas con imports en una sola dirección (CR-1):
  `ui → app_layer → domain` + `infra → domain` + `app.py` como
  composition root. Validado por `import-linter` (4 contratos KEPT).
- `domain/`: Pydantic models, errores tipados, eventos, Protocols.
- `infra/`: HTTP (httpx), SQLite (aiosqlite), file-based stores.
- `app_layer/`: controllers + queue + state machines.
- `ui/`: pantallas Textual con TCSS dedicado.

### Tests

- **474 verdes** total.
- ruff + mypy strict + import-linter en pre-commit.
- Cobertura ~75% (objetivo Fase 4: 80%).

---

[Unreleased]: https://github.com/_/_/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/_/_/releases/tag/v1.0.1
[1.0.0]: https://github.com/_/_/releases/tag/v1.0.0
