"""Live run view: scrolling timestamped log, counters, ETA."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from rich.console import Group
from rich.live import Live
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.table import Table

from database.models import STATUS_LABELS, Status
from engine import EV_CONFLICT, EV_FIELD, EV_NOTE, EV_PROCESSING, EV_RESULT, Event
from ui.colors import console, status_style, symbol


def format_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds or seconds in (float("inf"),):
        return "--:--:--"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


class RunView:
    """Renders the validator screen while the engine works."""

    def __init__(self, total: int, title: str = "BIN-TEL VALIDATOR") -> None:
        self.total = total
        self.title = title
        self.console = console()
        self.processed = 0
        self.discovered = 0
        self.unconfirmed = 0
        self.invalid = 0
        self.errors = 0
        self.started = time.monotonic()
        self.progress = Progress(
            TextColumn("[heading]Progress[/heading]"),
            BarColumn(bar_width=40, complete_style="ok", finished_style="ok"),
            TextColumn("{task.completed} / {task.total}"),
            console=self.console,
            transient=False,
        )
        self.task_id: Optional[TaskID] = None
        self.live: Optional[Live] = None

    # ------------------------------------------------------------- lifecycle
    def __enter__(self) -> "RunView":
        self.console.print()
        self.console.rule(f"[heading]{self.title}[/heading]", style="frame")
        self.task_id = self.progress.add_task("validating", total=max(self.total, 1))
        self.live = Live(
            self._renderable(),
            console=self.console,
            refresh_per_second=8,
            transient=False,
        )
        self.live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self.live is not None:
            self.live.update(self._renderable())
            self.live.__exit__(*exc)
            self.live = None
        self.console.print()

    # ------------------------------------------------------------------ render
    def _renderable(self):
        stats = Table.grid(padding=(0, 2))
        stats.add_column(justify="left")
        stats.add_column(justify="right")
        stats.add_row("[ok]Discovered[/ok]", f"[ok]{self.discovered}[/ok]")
        stats.add_row("[warn]Unconfirmed[/warn]", f"[warn]{self.unconfirmed}[/warn]")
        stats.add_row("[bad]Invalid[/bad]", f"[bad]{self.invalid}[/bad]")
        stats.add_row("[bad]Errors[/bad]", f"[bad]{self.errors}[/bad]")
        stats.add_row("[muted]Average response[/muted]", f"[muted]{self.average:.2f}s[/muted]")
        stats.add_row(
            "[muted]Estimated remaining[/muted]", f"[muted]{format_duration(self.eta)}[/muted]"
        )
        return Group(self.progress, stats)

    def _refresh(self) -> None:
        if self.live is not None:
            self.live.update(self._renderable())

    @property
    def average(self) -> float:
        elapsed = time.monotonic() - self.started
        return elapsed / self.processed if self.processed else 0.0

    @property
    def eta(self) -> float:
        remaining = max(self.total - self.processed, 0)
        return self.average * remaining if self.processed else -1.0

    # --------------------------------------------------------------- printing
    def log(self, text: str, style: str = "value", mark: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        prefix = f"[{mark}] " if mark else ""
        line = f"[time]\\[{stamp}][/time] {prefix}[{style}]{text}[/{style}]"
        if self.live is not None:
            self.live.console.print(line)
        else:
            self.console.print(line)

    def handle_event(self, event: Event) -> None:
        if event.kind == EV_PROCESSING:
            self.log(event.text, "info")
        elif event.kind == EV_FIELD:
            self.log(f"{symbol('ok')} {event.text}", "ok")
        elif event.kind == EV_CONFLICT:
            self.log(f"{symbol('warn')} {event.text}", "warn")
        elif event.kind == EV_NOTE:
            self.log(f"{symbol('bad')} {event.text}", "bad")
        elif event.kind == EV_RESULT:
            self._record_status(event.status)
            label = STATUS_LABELS.get(event.status, event.status.upper())
            style = status_style(event.status)
            suffix = f" {event.bin}" if event.status == Status.DISCOVERED else ""
            self.log(f"{label}{suffix}", style)
            self.console.print() if self.live is None else self.live.console.print()

    def _record_status(self, status: str) -> None:
        self.processed += 1
        if status == Status.DISCOVERED:
            self.discovered += 1
        elif status == Status.INVALID:
            self.invalid += 1
        elif status == Status.ERROR:
            self.errors += 1
        else:
            self.unconfirmed += 1
        if self.task_id is not None:
            self.progress.update(self.task_id, completed=self.processed)
        self._refresh()
