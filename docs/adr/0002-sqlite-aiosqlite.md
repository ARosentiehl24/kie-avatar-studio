# 0002. Persistencia local con SQLite + aiosqlite

Fecha: 2026-05-31 Estado: Aceptado

## Contexto

Necesitamos historial, recuperación de jobs en `WAITING_*` y consultas simples
desde la TUI. La app es local y monousuario.

## Decisión

SQLite via **aiosqlite**, con `PRAGMA journal_mode=WAL`. Un solo archivo
`data/jobs.db`. Repositorio `JobsDB` con API mínima.

## Consecuencias

- Pros: cero dependencias externas, portátil, suficiente para miles de jobs.
- Contras: no escalable a multi-usuario; sin migraciones automáticas.
- Backups triviales: copiar el archivo.

## Alternativas

- JSON plano: difícil de consultar/filtrar y propenso a corrupción.
- Postgres local: complica setup; innecesario.
- TinyDB: peor performance y sin transacciones reales.
