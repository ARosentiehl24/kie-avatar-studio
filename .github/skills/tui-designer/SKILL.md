---
name: TUI Designer
description: Design and implement highly aesthetic, interactive, and modern Terminal User Interfaces (TUIs) using Textual (Python) and Ink (React).
---

# TUI Designer Skill

This skill provides comprehensive instructions, design systems, and code templates to create modern, premium, and visually stunning Terminal User Interfaces (TUIs). It focuses primarily on **Textual (Python)** and **Ink (React/Node.js)**.

---

## 1. Core Design Philosophy for TUIs

Modern TUIs must not look like 1980s DOS applications. They should look like sleek, high-fidelity developer dashboards. Follow these rules on every design:

### A. Whitespace and Spacing (The "Air" Rule)
* **Never crowd the terminal:** Sticking text directly against borders looks cluttered.
* **Margins & Padding:** Always apply at least `margin: 1 2;` or `padding: 1 2;` to containers to let the interface breathe.
* **Border Styling:** Use subtle borders. In Textual, prefer `border: round $primary;` or `border: tall $accent;` over harsh double borders.

### B. Curated Color Palettes (Dark Mode First)
Avoid pure red, blue, or green. Use modern color tokens (e.g., Catppuccin, Tokyo Night, Nord):
* **Backgrounds:** Deep charcoal or slate (e.g., `#1e1e2e`, `#181825`, `#11111b`).
* **Text:** High-contrast off-white (`#cdd6f4`) for primary text, and dimmed gray (`#a6adc8`) for secondary information.
* **Accents:** Vibrant pastel/neon accents (e.g., Cyan `#89dceb`, Magenta `#f5c2e7`, Peach `#fab387`, Lavender `#b4befe`).

### C. Keyboard & Mouse First Navigation
* Keep navigation keyboard-friendly but support mouse clicks.
* Use visible shortcuts in the footer (e.g., `[Q] Quit`, `[S] Settings`).
* Highlight the currently focused widget clearly using `:focus` styles (e.g., a colored border or high-contrast background).

### D. Unicode Glyphs & Status Badges
* Use emojis or Nerd Font icons (like `🎬`, `📦`, `🔧`, `⏳`) to make navigation lists easily scannable.
* Implement color-coded status badges for lists/tables (e.g., Green box `[SUCCESS]` or Yellow `[PENDING]`).

---

## 2. Textual (Python) Design System & Templates

Textual apps use Textual CSS (`.tcss`) to separate layout/styling from Python code.

### A. TCSS Layout Template (`styles.tcss`)
Use this CSS base for a premium dark-themed layout:

```css
/* Variables */
$bg-dark: #11111b;
$bg-panel: #1e1e2e;
$primary: #89b4fa;
$accent: #f5c2e7;
$success: #a6e3a1;
$warning: #f9e2af;
$error: #f38ba8;
$text: #cdd6f4;
$text-muted: #a6adc8;

Screen {
    background: $bg-dark;
    color: $text;
}

/* Header & Footer styling */
Header {
    background: $primary;
    color: $bg-dark;
    text-style: bold;
    height: 1;
}

Footer {
    background: $bg-panel;
    color: $text-muted;
}

/* Base Container */
.container {
    layout: grid;
    grid-size: 12 12;
    padding: 1 2;
}

/* Sidebar and Content Panels */
.sidebar {
    grid-span: 3 12;
    background: $bg-panel;
    border-right: tall $primary;
    padding: 1 1;
}

.main-content {
    grid-span: 9 12;
    padding: 1 2;
}

/* Beautiful Interactive Buttons */
Button {
    background: $bg-panel;
    color: $primary;
    border: tall $primary;
    margin: 1 0;
    transition: background 200ms, color 200ms;
}

Button:hover {
    background: $primary;
    color: $bg-dark;
}

Button:focus {
    border: double $accent;
}

/* Alerts / Status */
.success-badge {
    background: $success;
    color: $bg-dark;
    text-style: bold;
    padding: 0 1;
}
```

### B. Python App Skeleton
A clean, modular structure for a responsive app with a sidebar and main panel:

```python
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Button, DataTable

class DashboardApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "Dev Workstation"
    SUB_TITLE = "Local AI Agent Terminal"

    BINDINGS = [
        ("q", "quit", "Exit"),
        ("d", "toggle_dark", "Toggle Dark Mode"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Vertical(
                Static("[bold cyan]NAVIGATION[/]", id="nav-title"),
                Button("Dashboard", id="btn-dash"),
                Button("Logs", id="btn-logs"),
                Button("Settings", id="btn-settings"),
                classes="sidebar"
            ),
            Vertical(
                Static("[bold]MAIN DASHBOARD[/]", id="main-title"),
                DataTable(id="data-table"),
                classes="main-content"
            )
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#data-table", DataTable)
        table.add_columns("Task", "Status", "Duration")
        table.add_rows([
            ("Fine-tuning Llama 3", "[green]Success[/]", "12m 3s"),
            ("Downloading weights", "[yellow]Pending[/]", "---"),
            ("Evaluating test cases", "[red]Failed[/]", "2m 1s")
        ])

if __name__ == "__main__":
    DashboardApp().run()
```

---

## 3. Ink (React) Design System & Templates

Ink lets you build TUIs using React components. It maps React components into ANSI terminal output.

### A. Basic Layout & Styling
* Use `flexDirection` and `justifyContent` for alignment.
* Wrap labels in `<Box>` components with borders.

```jsx
import React from 'react';
import {render, Box, Text} from 'ink';
import BigText from 'ink-big-text';
import SelectInput from 'ink-select-input';

const MainMenu = () => {
    const handleSelect = (item) => {
        if (item.value === 'exit') {
            process.exit(0);
        }
    };

    const items = [
        { label: '🎬 New Project', value: 'new' },
        { label: '⚙️ Settings', value: 'settings' },
        { label: '🚪 Exit', value: 'exit' }
    ];

    return (
        <Box flexDirection="column" padding={2}>
            {/* Header */}
            <Box borderStyle="round" borderColor="cyan" padding={1} marginBottom={1}>
                <BigText text="KIE AI" colors={['cyan', 'magenta']} />
            </Box>

            {/* Subtitle */}
            <Box marginBottom={1}>
                <Text dimmed>TUI Developer Workspace v1.0.0</Text>
            </Box>

            {/* Menu List */}
            <Box flexDirection="column" borderStyle="single" borderColor="gray" padding={1}>
                <Text bold color="yellow">Select Option:</Text>
                <SelectInput items={items} onSelect={handleSelect} />
            </Box>
        </Box>
    );
};

render(<MainMenu />);
```

---

## 4. UI Refactoring Flow for Agents

When requested to improve the design of an existing TUI:
1. **Identify the library:** Read imports to check if it's Textual, Rich, or Ink.
2. **Isolate Layout from Styling:** If Textual, extract inline styles or programmatic styles into a `.tcss` file.
3. **Inject spacing:** Ensure there are clear paddings (`padding: 1 2;`), margins, and borders separating widgets.
4. **Color Refactoring:** Replace standard primary colors with custom hex codes matching the Nord or Catppuccin color scheme.
5. **Interactive Feedback:** Verify that every clickable or selectable element has `:hover` and `:focus` states.
