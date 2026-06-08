"""Formatters compartidos para los panels de contadores de las pantallas.

Centraliza el patrón visual `Total N · activos · en cola · listos · fallidos`
que se repetía idénticamente en `AudiosScreen`, `VideosScreen`,
`HistoryScreen` y `QueueScreen` (CR-3.7). Cada pantalla pasa los
labels semánticos que correspondan a su dominio
(`active_label="generando"` para audio/video, `"procesando"` para
queue, `"activos"` para historial).

### Decisión de UI

Sin emojis prefix: el color del texto comunica el estado y es 100%
portable entre terminales (algunos chars dingbat caen a text-style
narrow y se pegan al siguiente carácter).

Colores semánticos uniformes:
- `[bold]` blanco — total (cuenta sin estado)
- `[cyan]` — en ejecución / activo
- `[yellow]` — pendiente / en cola
- `[green]` — terminado OK
- `[red]` — fallido
"""

from __future__ import annotations

_SEPARATOR: str = "  ·  "


def format_full_counters(
    total: int,
    active: int,
    queued: int,
    done: int,
    failed: int,
    *,
    active_label: str = "activos",
) -> str:
    """Render del header completo con los 5 contadores.

    Usado por `HistoryScreen`, `AudiosScreen`, `VideosScreen`.
    `active_label` se pasa según el dominio: "activos" (historial),
    "generando" (audio/video).
    """
    return _SEPARATOR.join(
        (
            f"[bold]Total {total}[/bold]",
            f"[cyan]{active} {active_label}[/cyan]",
            f"[yellow]{queued} en cola[/yellow]",
            f"[green]{done} listos[/green]",
            f"[red]{failed} fallidos[/red]",
        )
    )


def format_queue_summary(
    total: int,
    queued: int,
    in_progress: int,
    failed: int,
) -> str:
    """Render reducido para `QueueScreen` (sin `listos`, porque la
    pantalla solo muestra jobs no-completados)."""
    return _SEPARATOR.join(
        (
            f"[bold]Total {total}[/bold]",
            f"[cyan]{in_progress} procesando[/cyan]",
            f"[yellow]{queued} en cola[/yellow]",
            f"[red]{failed} fallidos[/red]",
        )
    )
