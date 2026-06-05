# 0001. Async stack: asyncio + httpx + aiosqlite

Fecha: 2026-05-31
Estado: Aceptado

## Contexto

La app debe hacer en simultáneo: subir imagen, esperar audio, esperar video, descargar resultado y mantener la UI fluida. Necesitamos paralelismo dentro de un job y entre jobs sin complicar el modelo mental.

## Decisión

Usar **asyncio** como modelo de concurrencia único.
HTTP con **httpx.AsyncClient**, DB con **aiosqlite**.
La UI (Textual) ya corre sobre asyncio, así que todo vive en la misma event loop.

## Consecuencias

- Pros: una sola loop, sin threads, fácil cancelación, `asyncio.gather` para paralelismo intra-job, `asyncio.Semaphore` para paralelismo entre jobs.
- Contras: no podemos usar libs solo sync sin envolverlas con `to_thread`.
- Implica disciplina de no llamar `time.sleep` ni `requests`.

## Alternativas

- threading: más complejo para cancelar y para integrar con Textual.
- multiprocessing: overkill para llamadas HTTP.
