"""Render a ScanResult to the terminal or JSON."""

from __future__ import annotations

import io
import json
from dataclasses import asdict

from models import BLOCKER, WARN, ScanResult

_SEVERITY_STYLE = {BLOCKER: "red", WARN: "yellow"}
_SEVERITY_LABEL = {BLOCKER: "BLOCKER", WARN: "WARN"}


def to_json(result: ScanResult) -> str:
    payload = {
        "files_scanned": result.files_scanned,
        "findings": [asdict(f) for f in result.findings],
        "summary": {
            "total": len(result.findings),
            "blockers": len(result.blockers),
            "warnings": len(result.warnings),
        },
    }
    return json.dumps(payload, indent=2)


def render_result(result: ScanResult, color: bool = True) -> str:
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel

    console = Console(
        record=True, no_color=not color, width=100, force_terminal=color, file=io.StringIO()
    )

    if not result.has_findings:
        console.print(
            f"\n[green]✓ judgeskew: no biased LLM-as-judge setup found "
            f"({result.files_scanned} file(s) scanned).[/green]\n"
        )
        return console.export_text(styles=color)

    for f in result.findings:
        style = _SEVERITY_STYLE.get(f.severity, "white")
        label = _SEVERITY_LABEL.get(f.severity, f.severity.upper())
        title = f"[{style}]{label}[/{style}] [dim]{escape(f.rule)}[/dim]"
        body = (
            f"[bold]{escape(f.file)}:{f.line}:{f.col}[/bold]\n"
            f"{escape(f.message)}\n"
            f"[dim]Fix:[/dim] {escape(f.fix)}"
        )
        console.print(Panel(body, title=title, border_style=style, title_align="left"))

    console.print(
        f"\n[bold]{len(result.findings)} finding(s)[/bold] "
        f"([red]{len(result.blockers)} blocker(s)[/red], "
        f"[yellow]{len(result.warnings)} warning(s)[/yellow]) "
        f"across {result.files_scanned} file(s).\n"
    )
    return console.export_text(styles=color)
