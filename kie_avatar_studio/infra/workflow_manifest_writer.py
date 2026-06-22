"""Escribe el manifest `workflow.json` atómicamente en el `output_dir`.

Implementa `domain.ports.WorkflowManifestWriter`. Single responsibility:
serializar un `WorkflowJob` (snapshot derivado desde la DB) y escribirlo
de forma atómica al filesystem.

### Atomicidad

Patrón estándar `tmp + replace`:
1. Escribir el payload completo a `workflow.json.<uuid>.tmp` en el MISMO
   directorio que el target. Es requisito para que el rename sea atómico
   en el FS (no se permite rename entre volúmenes).
2. `Path.replace(target)` → mapea a:
   - POSIX: `rename(2)` — atómico por contrato del kernel.
   - Windows NTFS: `MoveFileEx(MOVEFILE_REPLACE_EXISTING)` — atómico para
     paths en el mismo volumen NTFS.

Si el proceso crashea entre el `write_text` del tmp y el `replace`, el
`.json` previo sigue intacto. La DB tiene el estado correcto y al
próximo `_transition()` se regenera limpio.

### Robustez en Windows (importante)

`os.replace` en Windows puede fallar con `PermissionError` si el target
tiene un handle abierto por antivirus, OneDrive, indexador o un consumer
externo. Mitigación: hasta 4 intentos en total (1 inicial sin delay + 3
con backoff exponencial 50ms → 150ms → 500ms). Tras agotar todos los
intentos se devuelve `False` y el runner marca
`workflow.manifest_write_failed=True` (NO bloquea el workflow: la DB es
la fuente de verdad).

### No-locks

NO usamos file locking. Las únicas escrituras al target vienen del
`WorkflowRunner._transition()` que está serializado por un
`asyncio.Lock` per workflow_id. Entre workflows distintos, cada uno
escribe a su propio `output_dir/workflow.json` → no hay solapamiento.

### Tmp único por escritura

Cada `write()` genera un sufijo `.<uuid4>.tmp` para evitar colisiones
con escrituras concurrentes (no deberían pasar con el lock, pero por
defensa). Al final del workflow o al arrancar la app, los `.tmp` stale
se pueden limpiar best-effort (no crítico).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Final

from loguru import logger

from ..domain.models import WorkflowJob
from ..domain.policies import is_path_inside
from ._workflow_manifest_payload import build_manifest_payload

_MANIFEST_FILENAME: Final[str] = "workflow.json"
_TMP_SUFFIX_BYTES: Final[int] = 4  # 8 chars hex; suficiente para evitar colisiones.
_REPLACE_RETRY_DELAYS: Final[tuple[float, ...]] = (0.05, 0.15, 0.5)


class AtomicWorkflowManifestWriter:
    """Implementación concreta del `WorkflowManifestWriter` con atomic write."""

    def __init__(self, outputs_dir: Path) -> None:
        self._outputs_dir = outputs_dir

    async def write(self, workflow: WorkflowJob) -> bool:
        """Escribe `output_dir/workflow.json` atómicamente.

        Devuelve `True` si la escritura terminó OK, `False` si falló
        permanentemente tras los retries. El runner usa el resultado
        para setear `workflow.manifest_write_failed`.
        """
        output_dir = Path(workflow.output_dir)
        if not is_path_inside(output_dir, self._outputs_dir):
            logger.warning("Manifest omitido: output_dir fuera de outputs_dir: {}", output_dir)
            return False
        try:
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("No se pudo crear output_dir '{}' para manifest: {}", output_dir, exc)
            return False

        manifest = await build_manifest_payload(workflow)
        payload = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False)
        target = output_dir / _MANIFEST_FILENAME
        tmp = output_dir / f"{_MANIFEST_FILENAME}.{secrets.token_hex(_TMP_SUFFIX_BYTES)}.tmp"

        try:
            await asyncio.to_thread(tmp.write_text, payload, encoding="utf-8")
        except OSError as exc:
            logger.warning("Falló escribir manifest tmp '{}': {}", tmp, exc)
            await _best_effort_unlink(tmp)
            return False

        try:
            await _replace_with_retry(tmp, target)
        except OSError as exc:
            logger.warning("Falló rename atómico de manifest '{}' → '{}': {}", tmp, target, exc)
            await _best_effort_unlink(tmp)
            return False

        return True


async def _replace_with_retry(tmp: Path, target: Path) -> None:
    """Hace `tmp.replace(target)` con reintentos ante `PermissionError`.

    `PermissionError` en Windows es común si el archivo target está abierto
    por otro proceso (antivirus, OneDrive, etc.). Backoff exponencial corto
    suele resolverlo. Tras agotar reintentos, propaga la excepción.
    """
    last_exc: OSError | None = None
    for delay in (0.0, *_REPLACE_RETRY_DELAYS):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(tmp.replace, target)
            return
        except PermissionError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            # Otros OSError (disk full, path inválido) no se reintentan.
            raise exc
    if last_exc is None:
        raise OSError("retry replace: estado inesperado sin excepción capturada")
    raise last_exc


async def _best_effort_unlink(path: Path) -> None:
    """Borra el path si existe, ignorando cualquier error."""
    try:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    except OSError:
        logger.opt(exception=True).debug("Best-effort unlink falló para {}", path)
