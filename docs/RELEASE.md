# Release Process

> **TL;DR**: bumpeo `__version__` + `pyproject.toml` → muevo CHANGELOG
> `[Unreleased]` → `git tag vX.Y.Z` → `git push --tags`. El resto lo
> hace la GitHub Action automáticamente.

## Workflow paso a paso

1. **Decidir tamaño** del cambio (ver `docs/VERSIONING.md`):
   - L → MAJOR (`X.0.0`)
   - M → MINOR (`x.X.0`)
   - S → PATCH (`x.x.X`)

2. **Bumpear versión** en DOS lugares:
   - `pyproject.toml` → línea `version = "X.Y.Z"`
   - `kie_avatar_studio/__init__.py` → `__version__ = "X.Y.Z"`

3. **Actualizar CHANGELOG.md**:
   - Mover entradas de `## [Unreleased]` a una nueva sección
     `## [X.Y.Z] — YYYY-MM-DD`.
   - Vaciar `[Unreleased]` (dejar un placeholder).

4. **Commit + tag + push**:
   ```bash
   git add pyproject.toml kie_avatar_studio/__init__.py CHANGELOG.md
   git commit -m "chore(release): vX.Y.Z"
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```

5. **GitHub Actions** se dispara automáticamente:
   - `release.yml`: buildea Windows .exe + Inno Setup installer en
     un runner Windows, los sube a GitHub Releases con las release
     notes extraídas del CHANGELOG.
   - `publish-pypi.yml`: si tenés `PYPI_API_TOKEN` configurado en
     Settings → Secrets, sube el sdist + wheel a PyPI. Sin token,
     skipea con warning (no rompe el release).
   - `ci.yml`: corre tests + ruff + mypy + import-linter en cada
     push a `main` y cada PR.

6. **Verificar el release** en
   `https://github.com/ARosentiehl24/kie-avatar-studio/releases/latest`.

## Updater en la app

Cada vez que un usuario abre la TUI con `UPDATE_CHECK_ENABLED=True`
(default), la app hace un `GET https://api.github.com/repos/<repo>/
releases/latest`. Si la versión publicada es mayor que la instalada,
muestra una notificación con el link al installer.

- Es **best-effort**: si la red falla, no rompe la app (log DEBUG).
- Rate limit anónimo de GitHub: 60 req/h por IP, suficiente.
- El usuario puede deshabilitar con `UPDATE_CHECK_ENABLED=False`.

## Sin Inno Setup local

El `iscc.exe` solo lo necesita el runner Windows del workflow (viene
preinstalado en `windows-latest`). No hay que instalarlo localmente.
El `.spec` de PyInstaller tampoco necesita ejecutarse local — solo si
querés debuggear el build vos mismo.

## Setup PyPI (opcional, una sola vez)

1. Crear cuenta en https://pypi.org/.
2. Settings → API tokens → Generate token (scope: solo este proyecto
   una vez que el primer upload exista, o "Entire account" para el
   primer upload).
3. En el repo GitHub: Settings → Environments → New environment
   `pypi` → agregar secret `PYPI_API_TOKEN` con el token de PyPI.
4. (Recomendado) Activar Trusted Publishing en PyPI para no manejar
   tokens: https://docs.pypi.org/trusted-publishers/

Sin esto, el workflow `publish-pypi.yml` skipea con warning y los
usuarios solo pueden bajar el `.exe` de GitHub Releases (sigue OK).
