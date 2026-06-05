"""Helpers compartidos para manipular el cursor de un `DataTable` Textual.

Las pantallas con tablas live (`AudiosScreen`, `HistoryScreen`, futura
`VideoJobsScreen`) necesitan exactamente las mismas operaciones:

1. Leer el `row_key` de la fila seleccionada (con tolerancia a tabla
   vacía o cursor fuera del rango).
2. Mover el cursor a una fila por su `row_key` (idempotente: si no
   existe, no hace nada).

Centralizar acá evita 3+ copias del mismo `try/except` y deja un
único lugar donde justificar por qué tragamos `Exception` (las APIs
internas de Textual mutan durante refresh y pueden lanzar errores
que dependen de la versión).
"""

from __future__ import annotations

from textual.widgets import DataTable


def get_selected_row_key(table: DataTable[str]) -> str | None:
    """Devuelve el `row_key.value` de la fila seleccionada o `None`.

    Devuelve `None` si:
    - la tabla está vacía;
    - el cursor está fuera del rango (puede pasar durante un refresh
      mientras se reconstruyen filas);
    - cualquier otra excepción interna de Textual al resolver el
      `coordinate_to_cell_key` (la API es interna y puede cambiar).
    """
    if table.row_count == 0:
        return None
    # `coordinate_to_cell_key` puede lanzar varias excepciones internas de
    # Textual durante un refresh (cursor fuera de rango, fila siendo
    # mutada, etc). Como acá solo queremos "best effort" para preservar
    # selección, atrapamos todo y devolvemos None — el caller asume
    # "no hay selección" sin romper el flujo.
    try:
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
    except Exception:
        return None
    value = row_key.value
    return value if isinstance(value, str) else None


def select_row_by_key(table: DataTable[str], row_key: str) -> None:
    """Mueve el cursor a la fila con `row_key`. No-op si no existe.

    Idempotente y silenciosa: pensada para usar después de un refresh
    cuando queremos preservar la selección del usuario.
    """
    # `get_row_index` lanza `RowDoesNotExist` cuando la fila fue borrada
    # entre refreshes. Caso esperado durante refresh en vivo.
    try:
        row_index = table.get_row_index(row_key)
    except Exception:
        return
    table.move_cursor(row=row_index, animate=False)
