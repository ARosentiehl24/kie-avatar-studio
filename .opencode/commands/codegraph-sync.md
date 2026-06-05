---
description: Sincroniza el índice local de CodeGraph (.codegraph/codegraph.db). Pasá "full" para forzar reindex completo en lugar del sync incremental.
agent: build
---

# CodeGraph — sincronizar índice

Tarea: actualizar el índice local de CodeGraph para que las tools MCP
(`codegraph_context`, `codegraph_search`, etc.) reflejen el estado actual del
workspace. Equivalente al skill `codegraph-sync` de Copilot CLI.

## Contexto recibido del entorno

Estado actual del workspace:

- Argumento recibido: `$ARGUMENTS` (vacío ⇒ sync incremental, `full` ⇒ reindex completo).
- ¿CodeGraph instalado?: !`command -v codegraph >/dev/null && codegraph --version || echo "NO INSTALADO"`
- ¿Existe `.codegraph/codegraph.db`?: !`test -f .codegraph/codegraph.db && echo "sí ($(stat -c%s .codegraph/codegraph.db 2>/dev/null || stat -f%z .codegraph/codegraph.db) bytes)" || echo "NO"`
- Estado actual del índice:

```text
!`codegraph status 2>&1 || echo "(codegraph status falló — probablemente .codegraph/ no inicializado)"`
```

## Qué hacer (en este orden)

1. **Si CodeGraph no está instalado** (línea de arriba dice "NO INSTALADO"):
   parar y avisarme con el comando exacto:
   `pnpm i -g @colbymchenry/codegraph` (o `npm i -g @colbymchenry/codegraph`).
   No intentes nada más.

2. **Si `.codegraph/codegraph.db` no existe**: ejecutar `codegraph init -i`
   (crea + indexa de cero) y saltar al paso 5.

3. **Si el argumento fue `full`**: ejecutar `codegraph index` (reindex completo,
   reescribe toda la DB). Reservado para tras renames masivos o upgrade de versión.

4. **Si el argumento está vacío** (caso normal): ejecutar `codegraph sync`
   (incremental, re-parsea solo archivos cambiados desde el último index).

5. **Después de cualquiera de los anteriores**: ejecutar `codegraph status` de
   nuevo y mostrarme el diff (cuántos nodos/edges antes vs después, archivos
   pendientes que quedaron en cero).

## Reglas

- No reinicies el MCP server ni edites `opencode.jsonc`: la DB es leída en cada
  query y los cambios se ven sin restart.
- Si aparece error de lock (`Pending: locked by ...`), correr `codegraph unlock`
  y reintentar **una sola vez**. Si vuelve a fallar, parar y reportar.
- No correr `codegraph index` rutinariamente: es full rebuild y agrega latencia.
  Solo cuando el usuario lo pida explícitamente con `/codegraph-sync full`.
- Sin output verboso: una línea por paso ejecutado + el `status` final.
