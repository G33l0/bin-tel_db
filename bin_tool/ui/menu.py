from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from rich import box
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

import config as config_module
from database.database import Database
from database.models import BIN_FIELDS, METADATA_FIELDS, STATUS_LABELS, Status
from engine import ValidationEngine, coverage_confidence
from providers.base import build_provider, build_providers
from ui.colors import confidence_style, console, status_style, symbol
from ui.progress import RunView
from utils.csv_utils import export_rows, list_files, read_bin_file, read_dataset_file
from utils.logging_utils import get_logger
from utils.validation import UNKNOWN, normalize_bin

LOGGER = get_logger()

MENU_ITEMS = [
    ("1", "Input CSV/TXT"),
    ("2", "Validate BINs"),
    ("3", "Import BIN Dataset"),
    ("4", "Export Results"),
    ("5", "Config"),
    ("6", "Statistics"),
    ("7", "Exit"),
]

STATUS_FILTERS = {
    "1": (None, "All records"),
    "2": (Status.DISCOVERED, "Discovered only"),
    "3": (Status.UNCONFIRMED, "Unconfirmed only"),
    "4": (Status.INVALID, "Invalid only"),
    "5": (Status.IMPORTED, "Imported only"),
}


class App:
    def __init__(self, config: Dict[str, object], database: Database) -> None:
        self.config = config
        self.database = database
        self.console = console()
        self.queue: List[str] = []
        self.rejected: List[Dict[str, str]] = []
        self.last_source = ""
        self.box = box.DOUBLE if symbol("ok") == "✓" else box.ASCII

    def run(self) -> None:
        while True:
            self.show_menu()
            choice = Prompt.ask(
                "[prompt]Select option[/prompt]", default="", show_default=False
            ).strip()
            if choice in ("7", "q", "quit", "exit"):
                self.console.print("[muted]Goodbye.[/muted]")
                return
            handler = {
                "1": self.action_input,
                "2": self.action_validate,
                "3": self.action_import,
                "4": self.action_export,
                "5": self.action_config,
                "6": self.action_statistics,
            }.get(choice)
            if handler is None:
                self.console.print("[warn]Choose a number between 1 and 7.[/warn]")
            else:
                try:
                    handler()
                except KeyboardInterrupt:
                    self.console.print("\n[warn]Cancelled.[/warn]")
                except Exception as exc:
                    LOGGER.exception("menu action failed")
                    self.console.print(f"[bad]{symbol('bad')} {type(exc).__name__}: {exc}[/bad]")
            self.pause()

    def pause(self) -> None:
        Prompt.ask("\n[muted]Press Enter to continue[/muted]", default="", show_default=False)

    def show_menu(self) -> None:
        self.console.clear()
        grid = Table.grid(padding=(0, 3))
        grid.add_column(justify="left", width=4)
        grid.add_column(justify="left")
        grid.add_row("", "")
        for key, label in MENU_ITEMS:
            grid.add_row(f"[heading]\\[{key}][/heading]", f"[value]{label}[/value]")
        grid.add_row("", "")
        self.console.print(
            Panel(
                grid,
                title="[heading]BIN-TEL DATABASE[/heading]",
                subtitle="[muted]BIN / IIN Metadata Tool[/muted]",
                box=self.box,
                border_style="frame",
                width=66,
            )
        )
        self.console.print(self.status_line())

    def status_line(self) -> Text:
        providers = build_providers(self.config, self.provider_context())
        ready = [p.name for p in providers if p.check_ready() is None]
        text = Text()
        text.append(f" Queued: {len(self.queue)}", style="info")
        text.append(f"   Stored BINs: {self.database.count_bins()}", style="info")
        text.append(f"   Providers ready: {', '.join(ready) or 'none'}\n", style="muted")
        for provider in providers:
            self._close_provider(provider)
        return text

    def provider_context(self) -> Dict[str, object]:
        return {"database": self.database, "validation": self.config.get("validation", {})}

    @staticmethod
    def _close_provider(provider) -> None:
        try:
            provider.close()
        except Exception:
            pass

    def action_input(self) -> None:
        directory = config_module.resolve_path(str(self.config["paths"]["input"]))
        path = self.choose_file(directory, "input", (".csv", ".txt", ".tsv"))
        if not path:
            return

        entries = read_bin_file(path)
        settings = self.config["input"]
        accepted: List[str] = []
        rejected: List[Dict[str, str]] = []
        seen = set()
        duplicates = 0

        for entry in entries:
            result = normalize_bin(
                entry.value,
                allowed_lengths=settings["allowed_bin_lengths"],
                max_digits=int(settings["max_input_digits"]),
            )
            if not result.ok or result.value is None:
                rejected.append(
                    {"value": entry.value, "line": str(entry.line), "reason": result.reason}
                )
                continue
            if settings.get("deduplicate", True) and result.value in seen:
                duplicates += 1
                continue
            seen.add(result.value)
            accepted.append(result.value)

        self.queue = accepted
        self.rejected = rejected
        self.last_source = os.path.basename(path)

        table = Table(box=self.box, border_style="frame", show_header=False)
        table.add_column("field", style="muted")
        table.add_column("value")
        table.add_row("File", path)
        table.add_row("Values read", str(len(entries)))
        table.add_row("Accepted", f"[ok]{len(accepted)}[/ok]")
        table.add_row("Duplicates skipped", f"[muted]{duplicates}[/muted]")
        table.add_row("Rejected", f"[bad]{len(rejected)}[/bad]" if rejected else "0")
        self.console.print(table)

        if rejected:
            self.console.print("\n[warn]Rejected values (first 10):[/warn]")
            for item in rejected[:10]:
                self.console.print(
                    f"  [bad]{symbol('bad')}[/bad] line {item['line']}: "
                    f"[value]{item['value']}[/value] [muted]- {item['reason']}[/muted]"
                )
            if Confirm.ask("\nWrite the rejected values to a report file?", default=False):
                self.write_rejected_report()

        if accepted:
            self.console.print(
                f"\n[ok]{symbol('ok')} {len(accepted)} BIN(s) queued. "
                "Run option [2] to validate them.[/ok]"
            )

    def write_rejected_report(self) -> None:
        directory = config_module.resolve_path(str(self.config["paths"]["results"]))
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(directory, f"rejected_{stamp}.csv")
        import csv as _csv

        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = _csv.DictWriter(handle, fieldnames=["value", "line", "reason"])
            writer.writeheader()
            writer.writerows(self.rejected)
        self.console.print(f"[ok]{symbol('ok')} Wrote {path}[/ok]")

    def action_validate(self) -> None:
        queue = list(self.queue)
        if not queue:
            self.console.print("[warn]No BINs queued. Use option [1] first.[/warn]")
            if not Confirm.ask("Re-check unconfirmed records already in the database?", default=False):
                return
            queue = [str(row["bin"]) for row in self.database.fetch_bins(Status.UNCONFIRMED)]
            if not queue:
                self.console.print("[muted]No unconfirmed records stored.[/muted]")
                return

        if self.config["validation"].get("skip_already_discovered", True):
            known = {
                str(row["bin"]) for row in self.database.fetch_bins(Status.DISCOVERED)
            }
            skipped = [b for b in queue if b in known]
            if skipped:
                queue = [b for b in queue if b not in known]
                self.console.print(
                    f"[muted]Skipping {len(skipped)} already discovered "
                    "(config: skip_already_discovered).[/muted]"
                )
        if not queue:
            self.console.print("[muted]Nothing left to validate.[/muted]")
            return

        providers = build_providers(self.config, self.provider_context())
        usable = []
        for provider in providers:
            reason = provider.check_ready()
            if reason:
                self.console.print(
                    f"[warn]{symbol('warn')} provider '{provider.name}' unavailable: {reason}[/warn]"
                )
                self._close_provider(provider)
            else:
                usable.append(provider)
        if not usable:
            self.console.print("[bad]No usable providers configured. See option [5].[/bad]")
            return

        self.console.print(
            f"[muted]Providers: {', '.join(p.name for p in usable)} | "
            f"concurrency {self.config['validation']['concurrency']}[/muted]"
        )
        if not Confirm.ask(f"Validate {len(queue)} BIN(s)?", default=True):
            for provider in usable:
                self._close_provider(provider)
            return

        run_id = self.database.start_run("validate", len(queue), self.last_source)
        with RunView(len(queue)) as view:
            engine = ValidationEngine(self.config, self.database, usable, view.handle_event)
            try:
                counters = engine.run(queue, run_id)
            except KeyboardInterrupt:
                engine.stop()
                raise
            finally:
                engine.close()
        self.database.finish_run(run_id, counters.as_dict())

        summary = Table(box=self.box, border_style="frame", show_header=False)
        summary.add_column("field", style="muted")
        summary.add_column("value", justify="right")
        summary.add_row("Processed", str(counters.processed))
        summary.add_row("Discovered", f"[ok]{counters.discovered}[/ok]")
        summary.add_row("Unconfirmed", f"[warn]{counters.unconfirmed}[/warn]")
        summary.add_row("Invalid", f"[bad]{counters.invalid}[/bad]")
        summary.add_row("Errors", f"[bad]{counters.errors}[/bad]")
        summary.add_row("Average response", f"{counters.average_seconds:.2f}s")
        self.console.print(summary)

        if Confirm.ask("Export the results now?", default=False):
            self.action_export()

    def action_import(self) -> None:
        directory = config_module.resolve_path(str(self.config["paths"]["imports"]))
        self.console.print(
            "[muted]Columns read from the CSV: bin/iin, "
            + ", ".join(METADATA_FIELDS)
            + " (common aliases accepted).[/muted]\n"
        )
        path = self.choose_file(directory, "imports", (".csv", ".tsv"))
        if not path:
            return

        records = read_dataset_file(path)
        if not records:
            self.console.print("[warn]No usable rows found in that file.[/warn]")
            return

        settings = self.config["input"]
        clean: List[Dict[str, str]] = []
        rejected = 0
        for record in records:
            result = normalize_bin(
                record["bin"],
                allowed_lengths=settings["allowed_bin_lengths"],
                max_digits=int(settings["max_input_digits"]),
            )
            if not result.ok or result.value is None:
                rejected += 1
                continue
            record["bin"] = result.value
            clean.append(record)

        dataset_name = Prompt.ask(
            "Dataset name", default=os.path.splitext(os.path.basename(path))[0]
        ).strip()
        counts = self.database.import_dataset(clean, dataset_name)
        self.console.print(
            f"[ok]{symbol('ok')} {counts['inserted']} row(s) loaded into the reference dataset "
            f"'{dataset_name}'.[/ok]"
        )
        if rejected:
            self.console.print(f"[warn]{rejected} row(s) rejected on BIN format.[/warn]")

        if Confirm.ask("Also copy these rows into the main BIN table?", default=False):
            stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            for record in clean:
                row: Dict[str, object] = {
                    "bin": record["bin"],
                    "bin_length": len(record["bin"]),
                    "status": Status.IMPORTED,
                    "source": f"dataset:{dataset_name}",
                    "checked_at": stamp,
                }
                for name in METADATA_FIELDS:
                    row[name] = record.get(name, UNKNOWN) or UNKNOWN
                row["confidence"] = coverage_confidence(row)
                self.database.upsert_bin(row)
            self.console.print(
                f"[ok]{symbol('ok')} {len(clean)} row(s) written to the BIN table.[/ok]"
            )

    def action_export(self) -> None:
        table = Table(box=self.box, border_style="frame", show_header=False)
        table.add_column("key", style="heading")
        table.add_column("label")
        for key, (_, label) in STATUS_FILTERS.items():
            table.add_row(f"[{key}]", label)
        self.console.print(table)

        choice = Prompt.ask(
            "Which records", choices=list(STATUS_FILTERS), default="2", show_choices=False
        )
        status, label = STATUS_FILTERS[choice]
        rows = self.database.fetch_bins(status)
        if not rows:
            self.console.print("[muted]Nothing to export.[/muted]")
            return

        fmt = Prompt.ask(
            "Format",
            choices=["csv", "json", "sql"],
            default=str(self.config["export"]["default_format"]),
        )
        directory = config_module.resolve_path(str(self.config["paths"]["results"]))
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = status or "all"
        path = os.path.join(directory, f"bins_{suffix}_{stamp}.{fmt}")
        written = export_rows(rows, path, fmt)
        self.console.print(f"[ok]{symbol('ok')} {written} row(s) ({label}) -> {path}[/ok]")

    def action_config(self) -> None:
        while True:
            self.print_config()
            choice = Prompt.ask(
                "\n[prompt]Edit[/prompt] [muted](v=validation, i=input, u=ui, p=providers, "
                "b=back)[/muted]",
                choices=["v", "i", "u", "p", "b"],
                default="b",
                show_choices=False,
            )
            if choice == "b":
                return
            if choice == "v":
                self.edit_validation()
            elif choice == "i":
                self.edit_input()
            elif choice == "u":
                self.edit_ui()
            else:
                self.edit_providers()
            config_module.save_config(self.config)
            self.console.print(f"[ok]{symbol('ok')} config.json saved.[/ok]")

    def print_config(self) -> None:
        table = Table(box=self.box, border_style="frame", title="[heading]Configuration[/heading]")
        table.add_column("Section", style="muted")
        table.add_column("Setting", style="muted")
        table.add_column("Value")
        for section in ("validation", "input", "ui", "export", "logging"):
            for key, value in self.config[section].items():
                table.add_row(section, key, str(value))
        self.console.print(table)

        providers = Table(box=self.box, border_style="frame", title="[heading]Providers[/heading]")
        providers.add_column("#", style="muted")
        providers.add_column("Name")
        providers.add_column("Type", style="muted")
        providers.add_column("Enabled")
        providers.add_column("Status")
        for index, entry in enumerate(self.config["providers"], start=1):
            built = build_provider(entry, self.provider_context())
            if built is not None:
                reason = built.check_ready()
                self._close_provider(built)
            else:
                reason = f"unknown provider type '{entry.get('type')}'"
            state = "[ok]yes[/ok]" if entry.get("enabled") else "[muted]no[/muted]"
            health = "[ok]ready[/ok]" if reason is None else f"[warn]{reason}[/warn]"
            providers.add_row(
                str(index), str(entry.get("name")), str(entry.get("type")), state, health
            )
        self.console.print(providers)

    def _ask_number(self, section: str, key: str, cast=float):
        current = self.config[section][key]
        raw = Prompt.ask(f"{key}", default=str(current))
        try:
            self.config[section][key] = cast(raw)
        except ValueError:
            self.console.print(f"[warn]Not a number; keeping {current}.[/warn]")

    def edit_validation(self) -> None:
        self._ask_number("validation", "concurrency", int)
        self._ask_number("validation", "request_timeout_seconds", float)
        self._ask_number("validation", "max_retries", int)
        self._ask_number("validation", "retry_backoff_seconds", float)
        self._ask_number("validation", "min_providers_for_confirmation", int)
        self.config["validation"]["skip_already_discovered"] = Confirm.ask(
            "skip_already_discovered", default=bool(self.config["validation"]["skip_already_discovered"])
        )
        self.config["validation"]["store_provider_results"] = Confirm.ask(
            "store_provider_results", default=bool(self.config["validation"]["store_provider_results"])
        )

    def edit_input(self) -> None:
        raw = Prompt.ask(
            "allowed_bin_lengths (comma separated)",
            default=",".join(str(n) for n in self.config["input"]["allowed_bin_lengths"]),
        )
        lengths = [int(part) for part in raw.replace(" ", "").split(",") if part.isdigit()]
        lengths = [n for n in lengths if 1 <= n <= 8]
        if lengths:
            self.config["input"]["allowed_bin_lengths"] = sorted(set(lengths))
        else:
            self.console.print("[warn]Keeping the previous lengths (1-8 digits only).[/warn]")
        self.config["input"]["deduplicate"] = Confirm.ask(
            "deduplicate", default=bool(self.config["input"]["deduplicate"])
        )

    def edit_ui(self) -> None:
        raw = Prompt.ask("ascii_symbols (auto/yes/no)", default=str(self.config["ui"]["ascii_symbols"]))
        value = raw.strip().casefold()
        self.config["ui"]["ascii_symbols"] = (
            "auto" if value == "auto" else value in ("y", "yes", "true", "1")
        )
        self._ask_number("ui", "log_lines", int)

    def edit_providers(self) -> None:
        entries = self.config["providers"]
        raw = Prompt.ask("Provider number to edit", default="1")
        if not raw.isdigit() or not (1 <= int(raw) <= len(entries)):
            self.console.print("[warn]No such provider.[/warn]")
            return
        entry = entries[int(raw) - 1]
        entry["enabled"] = Confirm.ask("enabled", default=bool(entry.get("enabled")))
        if entry.get("type") == "http_json":
            self.console.print(
                "[muted]The API key is read from the environment variable named below, "
                "not from config.json.[/muted]"
            )
            entry["base_url"] = Prompt.ask("base_url", default=str(entry.get("base_url", ""))).strip()
            entry["url_template"] = Prompt.ask(
                "url_template", default=str(entry.get("url_template", "{base_url}/{bin}"))
            )
            entry["api_key_env"] = Prompt.ask(
                "api_key_env (blank for none)", default=str(entry.get("api_key_env", ""))
            ).strip()
            raw_rate = Prompt.ask(
                "rate_limit_per_second", default=str(entry.get("rate_limit_per_second", 1.0))
            )
            try:
                entry["rate_limit_per_second"] = float(raw_rate)
            except ValueError:
                self.console.print("[warn]Not a number; rate limit unchanged.[/warn]")

    def action_statistics(self) -> None:
        stats = self.database.stats()
        overview = Table(box=self.box, border_style="frame", show_header=False)
        overview.add_column("field", style="muted")
        overview.add_column("value", justify="right")
        overview.add_row("Stored BINs", str(stats["total"]))
        for status in STATUS_LABELS:
            count = stats["by_status"].get(status, 0)
            if count:
                style = status_style(status)
                overview.add_row(status.upper(), f"[{style}]{count}[/{style}]")
        confidence = float(stats["avg_confidence"])
        overview.add_row(
            "Average confidence (discovered)",
            f"[{confidence_style(confidence)}]{confidence:.3f}[/{confidence_style(confidence)}]",
        )
        overview.add_row("Records with unknown issuer", str(stats["unknown_issuer"]))
        overview.add_row("Reference dataset rows", str(stats["dataset_rows"]))
        overview.add_row("Cached API responses", str(stats.get("cached_responses", 0)))
        self.console.print(overview)

        if stats["by_network"]:
            networks = Table(box=self.box, border_style="frame", title="[heading]By network[/heading]")
            networks.add_column("Network")
            networks.add_column("Count", justify="right")
            for row in stats["by_network"]:
                networks.add_row(str(row["network"]), str(row["n"]))
            self.console.print(networks)

        if stats["by_country"]:
            countries = Table(box=self.box, border_style="frame", title="[heading]By country[/heading]")
            countries.add_column("Country code")
            countries.add_column("Count", justify="right")
            for row in stats["by_country"]:
                countries.add_row(str(row["country_code"]), str(row["n"]))
            self.console.print(countries)

        if stats["runs"]:
            runs = Table(box=self.box, border_style="frame", title="[heading]Recent runs[/heading]")
            for column in ("id", "kind", "started_at", "total", "discovered", "unconfirmed", "invalid", "errors"):
                runs.add_column(column.replace("_", " ").title(), justify="right")
            for row in stats["runs"]:
                runs.add_row(*[str(row.get(column, "")) for column in
                               ("id", "kind", "started_at", "total", "discovered", "unconfirmed", "invalid", "errors")])
            self.console.print(runs)

    def choose_file(self, directory: str, label: str, extensions: Sequence[str]) -> Optional[str]:
        files = list_files(directory, extensions)
        table = Table(box=self.box, border_style="frame", title=f"[heading]data/{label}[/heading]")
        table.add_column("#", style="muted", justify="right")
        table.add_column("File")
        table.add_column("Size", justify="right", style="muted")
        for index, path in enumerate(files, start=1):
            table.add_row(str(index), os.path.basename(path), f"{os.path.getsize(path):,} B")
        if not files:
            table.add_row("-", "[muted]no files found[/muted]", "")
        self.console.print(table)

        raw = Prompt.ask(
            "File number, or a full path [muted](blank to cancel)[/muted]",
            default="",
            show_default=False,
        ).strip()
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(files):
            return files[int(raw) - 1]
        candidate = os.path.expanduser(raw.strip('"'))
        if os.path.isfile(candidate):
            return candidate
        self.console.print(f"[bad]{symbol('bad')} File not found: {candidate}[/bad]")
        return None


def print_record(record: Dict[str, object]) -> None:
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("field", style="muted")
    table.add_column("value")
    for name in BIN_FIELDS:
        table.add_row(name, str(record.get(name, UNKNOWN)))
    console().print(table)
