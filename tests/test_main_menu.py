"""Tests del menú principal: registry agrupado + render con headers."""

from __future__ import annotations

from textual.widgets.option_list import Option

from kie_avatar_studio.ui.menu import MAIN_MENU, MAIN_MENU_GROUPS
from kie_avatar_studio.ui.screens.main_menu import (
    _build_menu_options,
    _format_section_header,
)


def test_main_menu_flat_matches_group_concat() -> None:
    """La tupla flat es la concatenación de los items de cada section, en orden."""
    expected = tuple(item for section in MAIN_MENU_GROUPS for item in section.items)
    assert expected == MAIN_MENU


def test_build_menu_options_intercalates_headers_and_items() -> None:
    """Cada section produce 1 header `disabled=True` + N items con `id`."""
    options = _build_menu_options()
    expected_total = sum(1 + len(section.items) for section in MAIN_MENU_GROUPS)
    assert len(options) == expected_total

    cursor = 0
    for section in MAIN_MENU_GROUPS:
        header = options[cursor]
        assert isinstance(header, Option)
        assert header.disabled is True
        assert header.id is None
        assert section.label.upper() in str(header.prompt)
        cursor += 1
        for item in section.items:
            opt = options[cursor]
            assert opt.disabled is False
            assert opt.id == item.id
            assert item.label in str(opt.prompt)
            cursor += 1


def test_first_real_option_is_not_a_header() -> None:
    """`on_mount` recorre buscando el primer Option con id; el algoritmo
    debe poder encontrar uno (sanity: cero secciones vacías)."""
    options = _build_menu_options()
    first_with_id = next((i for i, opt in enumerate(options) if opt.id is not None), None)
    assert first_with_id is not None
    # El primer item real debe ser justo después del primer header (índice 1).
    assert first_with_id == 1
    # Y su id debe coincidir con el primer item de la primera section.
    assert options[first_with_id].id == MAIN_MENU_GROUPS[0].items[0].id


def test_format_section_header_renders_with_uppercase_label() -> None:
    assert "CREAR" in _format_section_header("Crear")
    assert "MONITOREO" in _format_section_header("Monitoreo")


def test_no_duplicate_ids_across_groups() -> None:
    ids = [item.id for item in MAIN_MENU]
    assert len(ids) == len(set(ids))


def test_no_duplicate_hotkeys_across_groups() -> None:
    hotkeys = [item.hotkey for item in MAIN_MENU]
    assert len(hotkeys) == len(set(hotkeys))
