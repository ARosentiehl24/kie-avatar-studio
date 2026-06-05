"""`BatchLoader`: convierte carpetas `batch_jobs/<name>/` en `BatchEntry[]`.

Cada subcarpeta del directorio raíz se interpreta como UN video a generar.
El loader es **puro filesystem**: no toca red, no toca DB, no encola nada.
Los `BatchEntry` resultantes se devuelven con `errors` poblados si la
carpeta no cumple el contrato (ver `docs/SPEC.md §11`):

```
batch_jobs/<name>/
    script.txt           obligatorio (texto plano UTF-8)
    modelo.(png|jpg)     obligatorio (primer match)
    prompt.txt           opcional, si falta -> default_prompt
    voice.txt            opcional, si falta -> default_voice
    meta.json            opcional, override puntual:
                         { "voice": "...", "prompt": "..." }
```

`meta.json` (si existe) tiene precedencia sobre `voice.txt` / `prompt.txt`.
Los archivos sueltos son útiles para edición rápida con un editor;
`meta.json` es útil cuando hay otros consumidores que escriben los lotes
desde un pipeline (Python, n8n, etc.).

Lecturas async vía `asyncio.to_thread`: el batch puede tener cientos de
carpetas y bloquear el event loop con I/O del filesystem rompería el
refresh de la TUI mientras se escanea.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..domain.models import BatchEntry
from ..domain.policies import IMAGE_EXTENSIONS

_SCRIPT_FILENAME = "script.txt"
_PROMPT_FILENAME = "prompt.txt"
_VOICE_FILENAME = "voice.txt"
_META_FILENAME = "meta.json"
_MODELO_STEM = "modelo"


async def scan_batch_dir(
    directory: Path,
    *,
    default_prompt: str,
    default_voice: str,
) -> list[BatchEntry]:
    """Escanea `directory` y devuelve un `BatchEntry` por subcarpeta.

    El orden es alfabético por nombre de carpeta — predecible y útil para
    lotes nombrados `video_001`, `video_002`, etc. Las entries inválidas
    se devuelven igual (con `errors` poblado) para que la UI las muestre
    y el usuario sepa qué arreglar.

    Si `directory` no existe o no es un directorio, devuelve `[]` (no
    es error: simplemente no hay lotes).
    """
    if not await asyncio.to_thread(directory.is_dir):
        return []
    names = await asyncio.to_thread(_list_subdir_names, directory)
    entries: list[BatchEntry] = []
    for name in names:
        entry = await asyncio.to_thread(
            _build_entry,
            directory / name,
            name,
            default_prompt,
            default_voice,
        )
        entries.append(entry)
    return entries


def _list_subdir_names(directory: Path) -> list[str]:
    return sorted(child.name for child in directory.iterdir() if child.is_dir())


def _build_entry(
    folder: Path,
    name: str,
    default_prompt: str,
    default_voice: str,
) -> BatchEntry:
    """Construye un `BatchEntry` (síncrono: corre dentro de `to_thread`)."""
    errors: list[str] = []

    script = _read_text_or_empty(folder / _SCRIPT_FILENAME)
    if not script:
        errors.append(f"falta {_SCRIPT_FILENAME} (o está vacío)")

    image_path = _find_image(folder)
    if image_path is None:
        exts = ", ".join(sorted(IMAGE_EXTENSIONS))
        errors.append(f"falta {_MODELO_STEM}.<{exts}>")

    meta = _read_meta(folder / _META_FILENAME)
    if isinstance(meta, str):
        errors.append(meta)
        meta_data: dict[str, Any] = {}
    else:
        meta_data = meta

    prompt = _resolve_field(
        folder / _PROMPT_FILENAME,
        meta_data.get("prompt"),
        default_prompt,
    )
    voice = _resolve_field(
        folder / _VOICE_FILENAME,
        meta_data.get("voice"),
        default_voice,
    )

    return BatchEntry(
        name=name,
        path=folder,
        script=script,
        image_path=image_path,
        prompt=prompt,
        voice=voice,
        errors=errors,
    )


def _read_text_or_empty(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _find_image(folder: Path) -> Path | None:
    """Busca el primer `modelo.<ext>` con extensión soportada por Kie."""
    for ext in sorted(IMAGE_EXTENSIONS):
        candidate = folder / f"{_MODELO_STEM}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _read_meta(path: Path) -> dict[str, Any] | str:
    """Devuelve dict válido o mensaje de error si el JSON está roto.

    Si no existe `meta.json`, devuelve `{}` (no es error: es opcional).
    """
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return f"{_META_FILENAME} inválido: {exc}"
    if not isinstance(data, dict):
        return f"{_META_FILENAME} debe ser un objeto JSON"
    return data


def _resolve_field(file_path: Path, meta_value: Any, default: str) -> str:
    """Precedencia: meta.json > <field>.txt > default."""
    if isinstance(meta_value, str) and meta_value.strip():
        return meta_value.strip()
    file_value = _read_text_or_empty(file_path)
    if file_value:
        return file_value
    return default
