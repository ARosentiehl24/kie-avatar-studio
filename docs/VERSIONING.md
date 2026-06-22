# Versionado — Kie Avatar Studio

Esquema basado en **SemVer** ([semver.org](https://semver.org/)) con etiquetas
humanas (L/M/S) para que sea fácil decidir en cada commit. La versión vive en
`pyproject.toml` y se tagea en git como `vMAJOR.MINOR.PATCH`.

Formato: `MAJOR.MINOR.PATCH` (ej. `1.2.5`).

## Reglas de bump

| Tamaño | Bump  | Posición | Cuándo                                           |
| ------ | ----- | -------- | ------------------------------------------------ |
| **L**  | MAJOR | `X.0.0`  | Cambio incompatible con instalaciones existentes |
| **M**  | MINOR | `x.X.0`  | Feature nueva que no rompe nada existente        |
| **S**  | PATCH | `x.x.X`  | Fix / pulido / refactor interno user-invisible   |

Cuando se incrementa una posición, las de la derecha se resetean a `0` (ej:
`1.4.7` + M → `1.5.0`, no `1.5.7`).

## Qué cuenta como L (MAJOR — breaking)

- Cambio incompatible en `Settings`/`.env` (renames, defaults cambiados que
  alteran comportamiento, formato de `keys.json` distinto, etc.).
- Migración de schema SQLite sin script de upgrade automático.
- Cambios en la firma pública de modelos del `domain/` que rompen presets/jobs
  persistidos.
- Eliminación o rename de comandos / hotkeys del menú principal que romperían
  muscle memory o scripts del usuario.
- Cambios de stack mayores (Textual → otra UI, SQLite → otra DB).

> Si un usuario que viene de la versión anterior tiene que hacer **cualquier
> acción manual** (ajustar `.env`, borrar `data/`, migrar archivos) para que la
> app siga funcionando, es **L**.

## Qué cuenta como M (MINOR — feature)

- Pantalla nueva, hotkey nuevo, acción nueva en una pantalla existente.
- Integración con un endpoint nuevo de Kie (nuevo modelo, nueva voz, etc.).
- Bug fix con cambio de comportamiento **user-visible** que no rompe nada
  existente (ej: "ahora el contador de créditos refresca cada minuto en vez de
  solo al abrir").
- Optimización que cambia métricas observables (ej: paralelismo duplicado por
  default).
- Nueva capacidad opcional habilitada por defecto pero apagable.

> Si un usuario abre la nueva versión y descubre "ah mirá, ahora puedo hacer X",
> es **M**.

## Qué cuenta como S (PATCH — pulido)

- Bug fix sin cambio de comportamiento visible (corrige un error que no debería
  haber pasado).
- Ajuste de copy en mensajes, labels, hints.
- CSS / UX / espaciado / colores.
- Refactor interno (CR-1, SRP, renames de privados, mover archivos manteniendo
  la API pública).
- Tests nuevos.
- Documentación.
- Dependencias bumpeadas sin cambio de comportamiento.

> Si el usuario no nota nada al abrir la app, es **S**.

## Workflow

1. Hacer el cambio + commits temáticos (uno o varios).
2. Decidir el tamaño: L / M / S.
3. Bumpear `pyproject.toml` (línea `version = "X.Y.Z"`).
4. Mover entradas de `[Unreleased]` a una nueva sección en `CHANGELOG.md` con la
   nueva versión + fecha.
5. Commit final: `chore(release): vX.Y.Z`.
6. Tag git: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
7. Push del tag: `git push --tags` (cuando aplique).

## Casos de borde

- **Múltiples cambios en una release**: se aplica el **más grande**. 3 fixes + 1
  feature = M. 1 feature + 1 breaking = L.
- **0.x.y**: este proyecto saltó directo a `1.0.0` (estado funcional completo
  con las 10 pantallas listas). No vamos a versionar `0.x`.
- **Pre-releases**: si alguna vez necesitamos, usar sufijo `-rc.N` (ej.
  `1.2.0-rc.1`). Por ahora no se usa.
