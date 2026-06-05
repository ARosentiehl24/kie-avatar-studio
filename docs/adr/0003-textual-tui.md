# 0003. UI con Textual

Fecha: 2026-05-31
Estado: Aceptado

## Contexto

Queremos una interfaz interactiva en terminal, con tablas vivas y formularios, sin reinventar primitivas.

## Decisión

Usar **Textual** (de Textualize). Aprovecha Rich, vive sobre asyncio y tiene widgets ricos (`DataTable`, `Input`, `Select`, `Log`).

## Consecuencias

- Pros: integración natural con `asyncio`, theming via TCSS, devtools opcionales.
- Contras: dep importante; cambios entre versiones pueden requerir mantenimiento.
- Permite migrar la UI a web (Textual Web) más adelante con poco esfuerzo.

## Alternativas

- Rich solo: bajo nivel, sin manejo de pantallas/eventos.
- prompt_toolkit: más viejo, menos widgets de alto nivel.
- curses: demasiado bajo nivel y poco multiplataforma.
