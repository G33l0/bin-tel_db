from __future__ import annotations

import argparse
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

try:
    import rich
except ImportError:
    sys.stderr.write(
        "The 'rich' package is required.\n"
        "Install the dependencies first:  pip install -r requirements.txt\n"
    )
    raise SystemExit(2)

import config as config_module
from database.database import Database
from database.models import Status
from engine import ValidationEngine
from providers.base import build_providers
from ui import colors
from ui.menu import App, print_record
from ui.progress import RunView
from utils.csv_utils import export_rows, read_bin_file, read_dataset_file
from utils.logging_utils import setup_logging
from utils.validation import normalize_bin

VERSION = "1.0.0"


def bootstrap(config_path: str):
    config = config_module.load_config(config_path)
    config_module.ensure_directories(config)
    setup_logging(
        config_module.resolve_path(str(config["paths"]["logs"])),
        str(config["logging"]["file"]),
        str(config["logging"]["level"]),
    )
    colors.configure(config["ui"]["ascii_symbols"])
    database = Database(config_module.resolve_path(str(config["database"]["path"])))
    database.connect()
    return config, database


def _normalise_all(values, config):
    settings = config["input"]
    accepted, rejected = [], []
    for value in values:
        result = normalize_bin(
            value,
            allowed_lengths=settings["allowed_bin_lengths"],
            max_digits=int(settings["max_input_digits"]),
        )
        (accepted.append(result.value) if result.ok else rejected.append((value, result.reason)))
    return list(dict.fromkeys(accepted)), rejected


def cmd_validate(args, config, database) -> int:
    console = colors.console()
    entries = read_bin_file(args.file)
    accepted, rejected = _normalise_all([entry.value for entry in entries], config)
    for value, reason in rejected[:20]:
        console.print(f"[warn]skipped {value}: {reason}[/warn]")
    if not accepted:
        console.print("[bad]No valid BINs found in that file.[/bad]")
        return 1

    providers = [p for p in build_providers(config, {"database": database, "validation": config["validation"]})
                 if p.check_ready() is None]
    if not providers:
        console.print("[bad]No usable providers configured.[/bad]")
        return 1

    run_id = database.start_run("validate", len(accepted), os.path.basename(args.file))
    with RunView(len(accepted)) as view:
        engine = ValidationEngine(config, database, providers, view.handle_event)
        try:
            counters = engine.run(accepted, run_id)
        finally:
            engine.close()
    database.finish_run(run_id, counters.as_dict())
    console.print(
        f"[ok]discovered {counters.discovered}[/ok] | [warn]unconfirmed {counters.unconfirmed}[/warn] "
        f"| [bad]invalid {counters.invalid} errors {counters.errors}[/bad]"
    )
    if args.export:
        rows = database.fetch_bins(Status.DISCOVERED if args.discovered_only else None)
        written = export_rows(rows, args.export, os.path.splitext(args.export)[1].lstrip(".") or "csv")
        console.print(f"[ok]exported {written} row(s) to {args.export}[/ok]")
    return 0


def cmd_import(args, config, database) -> int:
    console = colors.console()
    records = read_dataset_file(args.file)
    accepted = []
    for record in records:
        result = normalize_bin(
            record["bin"],
            allowed_lengths=config["input"]["allowed_bin_lengths"],
            max_digits=int(config["input"]["max_input_digits"]),
        )
        if result.ok and result.value:
            record["bin"] = result.value
            accepted.append(record)
    name = args.name or os.path.splitext(os.path.basename(args.file))[0]
    counts = database.import_dataset(accepted, name)
    console.print(
        f"[ok]imported {counts['inserted']} row(s) into dataset '{name}'[/ok] "
        f"[muted]({len(records) - len(accepted)} rejected)[/muted]"
    )
    return 0


def cmd_export(args, config, database) -> int:
    rows = database.fetch_bins(args.status)
    if not rows:
        colors.console().print("[muted]Nothing to export.[/muted]")
        return 1
    written = export_rows(rows, args.out, args.format)
    colors.console().print(f"[ok]exported {written} row(s) to {args.out}[/ok]")
    return 0


def cmd_stats(args, config, database) -> int:
    App(config, database).action_statistics()
    return 0


def cmd_lookup(args, config, database) -> int:
    console = colors.console()
    result = normalize_bin(
        args.bin,
        allowed_lengths=config["input"]["allowed_bin_lengths"],
        max_digits=int(config["input"]["max_input_digits"]),
    )
    if not result.ok or result.value is None:
        console.print(f"[bad]{result.reason}[/bad]")
        return 1
    record = database.get_bin(result.value)
    if not record:
        console.print("[muted]not stored[/muted]")
        return 1
    print_record(record)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin_tool",
        description="BIN-TEL DATABASE - BIN/IIN metadata tool. Run without arguments "
                    "for the interactive menu.",
    )
    parser.add_argument("--version", action="version", version=f"bin-tel {VERSION}")
    parser.add_argument(
        "--config", default=config_module.CONFIG_PATH, help="path to config.json"
    )
    sub = parser.add_subparsers(dest="command")

    validate = sub.add_parser("validate", help="validate the BINs in a CSV/TXT file")
    validate.add_argument("file")
    validate.add_argument("--export", help="write results to this file when finished")
    validate.add_argument(
        "--discovered-only", action="store_true", help="export only discovered records"
    )
    validate.set_defaults(func=cmd_validate)

    importer = sub.add_parser("import", help="import a reference BIN dataset (CSV)")
    importer.add_argument("file")
    importer.add_argument("--name", help="dataset name (defaults to the file name)")
    importer.set_defaults(func=cmd_import)

    export = sub.add_parser("export", help="export stored records")
    export.add_argument("out")
    export.add_argument("--format", choices=["csv", "json", "sql"], default="csv")
    export.add_argument(
        "--status",
        choices=[Status.DISCOVERED, Status.UNCONFIRMED, Status.INVALID, Status.ERROR, Status.IMPORTED],
        default=None,
    )
    export.set_defaults(func=cmd_export)

    stats = sub.add_parser("stats", help="print database statistics")
    stats.set_defaults(func=cmd_stats)

    lookup = sub.add_parser("lookup", help="print one stored record")
    lookup.add_argument("bin")
    lookup.set_defaults(func=cmd_lookup)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config, database = bootstrap(args.config)
    try:
        if getattr(args, "func", None) is None:
            App(config, database).run()
            return 0
        return int(args.func(args, config, database) or 0)
    except KeyboardInterrupt:
        colors.console().print("\n[warn]Interrupted.[/warn]")
        return 130
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
