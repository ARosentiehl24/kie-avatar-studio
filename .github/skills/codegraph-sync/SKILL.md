---
name: codegraph-sync
description: >-
  Sincroniza el índice local de CodeGraph (.codegraph/codegraph.db) con el
  estado actual del workspace. Úsalo cuando: el usuario lo pida explícitamente
  ("actualiza codegraph", "sync codegraph", "refresca el índice"), después de
  un git pull / git checkout de otra rama, después de generar muchos archivos
  por scripts, o cuando `codegraph_status` reporta archivos "Pending sync"
  que el watcher no alcanzó a procesar. NO usar para cada edit individual: el
  file watcher de `codegraph serve --mcp` ya cubre cambios incrementales en
  ~1s — esta skill es para forzar sync de un saque o para reindex completo.
user-invocable: true
---

# CodeGraph — sincronizar índice

El índice de CodeGraph vive en `.codegraph/codegraph.db` y normalmente se mantiene
fresco solo gracias al file watcher del MCP server (`codegraph serve --mcp`, sin
`--no-watch`). Esta skill ataca los casos donde el watcher se quedó corto o donde
querés forzar un sync determinístico:

- Volviste de otra rama (`git checkout`) o hiciste `git pull` con muchos cambios.
- Acabás de generar archivos por script (scaffolding, codegen, refactor masivo).
- `codegraph_status` reportó archivos en "Pending sync".
- El watcher está deshabilitado (WSL2 lento, filesystem montado, etc.).

## Cómo decidir entre `sync` y `index`

```
sync   → incremental (re-parsea solo archivos cambiados desde el último index)
index  → full reindex (rebuilds the whole graph; usar tras renames masivos)
```

Casi siempre alcanza con `sync`. Solo correr `index` si:
- Hubo un `git mv` masivo o renombre de paquete.
- El esquema de CodeGraph cambió (upgrade de versión major).
- `sync` reporta errores raros (corrupción del índice).

## Pasos a ejecutar

1. **Verificá que CodeGraph está instalado**:

   ```bash
   command -v codegraph >/dev/null && codegraph --version || {
     echo "✖ codegraph no está en PATH. Instalá con: pnpm i -g @colbymchenry/codegraph"
     exit 1
   }
   ```

2. **Verificá que el índice existe**. Si no existe, hay que inicializar:

   ```bash
   if [ ! -f .codegraph/codegraph.db ]; then
     codegraph init -i        # crea + indexa de cero
   fi
   ```

3. **Mostrá el estado actual** (cuántos archivos pendientes, edad del índice):

   ```bash
   codegraph status
   ```

4. **Sincronizá incremental** (default seguro):

   ```bash
   codegraph sync
   ```

5. **Solo si `sync` falla o el usuario pidió reindex completo**:

   ```bash
   codegraph index          # reindex full
   ```

6. **Confirmá** mostrando el `status` final.

## Notas operativas

- La DB se ignora en `.gitignore` (`.codegraph/*.db*`), no se versiona.
- Si la salida de `codegraph status` muestra `unlocked = false` o un lock viejo,
  liberalo con `codegraph unlock` antes de reintentar.
- Esta skill no toca el `.mcp.json` ni reinicia ningún proceso — el MCP server
  ya leerá la DB actualizada en la próxima query.
- Después de correr esta skill, las herramientas `codegraph_*` (vía MCP)
  devuelven resultados frescos sin necesidad de reiniciar Copilot CLI.

## Anti-patrones

- **No** correr esta skill después de cada edit puntual: el watcher integrado
  ya lo cubre y este reindex agrega latencia innecesaria.
- **No** usar `codegraph index` rutinariamente: es full rebuild y reescribe
  toda la DB. Reservalo para los casos del punto 5.
- **No** intentar sincronizar si el MCP server tiene un lock activo
  (`Pending: locked by serve`). Es esperado mientras el server esté corriendo;
  `sync` y `index` desde CLI funcionan en paralelo porque CodeGraph usa SQLite
  con WAL, pero si reportara error de lock real, parar el server primero.
