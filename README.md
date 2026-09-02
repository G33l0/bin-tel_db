# BIN-TEL DATABASE

A Windows-console tool for building a BIN / IIN metadata database from CSV/TXT
input lists and from official or licensed reference datasets.

```
╔═══════════════════════ BIN-TEL DATABASE ═══════════════════════╗
║                                                                ║
║ [1]    Input CSV/TXT                                           ║
║ [2]    Validate BINs                                           ║
║ [3]    Import BIN Dataset                                      ║
║ [4]    Export Results                                          ║
║ [5]    Config                                                  ║
║ [6]    Statistics                                              ║
║ [7]    Exit                                                    ║
║                                                                ║
╚═══════════════════ BIN / IIN Metadata Tool ════════════════════╝
 Queued: 0   Stored BINs: 5   Providers ready: offline_iin_ranges, local_dataset

Select option:
```

## Scope

The tool works only with issuer identification numbers - the first 6 or 8
digits - and publicly documented issuer metadata. Inputs longer than the
configured BIN length are rejected before anything else happens, so full card
numbers, CVVs, expiry dates and cardholder data never enter the pipeline or the
database. It does not attempt to determine whether an individual payment card
is usable.

There is no enumeration mode: the tool validates the BINs you supply or the
dataset you import, and it does not sweep 000000-999999 against third-party
services.

Two rules shape everything below:

* **Nothing is invented.** A field the providers did not report is written as
  `unknown`, never as a guess.
* **Disagreement is not resolved silently.** When providers report different
  values for a field, the field stays `unknown`, the disagreement is recorded
  in `conflicts`, and the record is marked `unconfirmed` for review.

## Install and run

Requires Python 3.9 or newer.

```cmd
cd bin_tool
pip install -r requirements.txt
python bin_tool.py
```

Or just double-click `run.bat`, which installs the dependencies on first use.

### Build a standalone .exe

```cmd
cd bin_tool
build.bat
```

This produces `dist\BIN-TEL.exe` - a single file with Python bundled in. Copy
it anywhere; it creates `config.json` and the `data\` folders next to itself
the first time it runs.

## Using the menu

**[1] Input CSV/TXT** - lists the files in `data\input`, reads BIN values from
them and queues the valid ones. CSV files use the first column whose header is
`bin`, `iin`, `prefix` and similar, falling back to the first column; TXT files
take one value per line (or comma-separated values on one line). Every value is
normalised (separators stripped) and checked; rejects are shown with a reason
and can be written to a report file.

**[2] Validate BINs** - runs the queued BINs through every ready provider,
reconciles the answers and stores the result. The screen shows a scrolling
timestamped log, running counters, average response time and an ETA:

```
──────────────────────────── BIN-TEL VALIDATOR ─────────────────────────────
[08:42:11] Processing 411111
[08:42:11] ✓ Issuer: Example Bank NA
[08:42:11] ✓ Visa
[08:42:11] ✓ Card Type: credit
[08:42:11] ✓ Country: United States
[08:42:11] DISCOVERED 411111

[08:42:13] Processing 522222
[08:42:13] ! Conflicting issuer (dataset=Bank A, metadata_api=Bank B)
[08:42:13] SKIPPED - requires verification

Progress ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 37 / 100
Discovered                 31
Unconfirmed                 4
Invalid                     2
Errors                      0
Average response        1.42s
Estimated remaining  00:01:29
```

Green is a confirmed value, yellow is uncertain or conflicting, red is invalid
or an error.

**[3] Import BIN Dataset** - bulk-loads an official or licensed BIN dataset
(CSV) from `data\imports` into the reference table, which the `local_dataset`
provider then answers from. Column names are matched against common aliases
(`bank`/`issuer`, `scheme`/`network`, `countrycode`/`country_code`, ...).
Optionally the rows are also written straight into the main BIN table with
status `imported`.

**[4] Export Results** - writes the stored records to `data\results` as CSV,
JSON, or SQL `INSERT` statements (with a matching `CREATE TABLE`) ready for
PostgreSQL or SQLite.

**[5] Config** - shows every setting and each provider's readiness, and edits
`config.json` in place.

**[6] Statistics** - totals by status, network and country, average confidence,
dataset size and recent runs.

## Command line

The same operations are available non-interactively:

```cmd
python bin_tool.py validate data\input\bins.csv --export data\results\out.csv
python bin_tool.py import data\imports\dataset.csv --name acme_2026
python bin_tool.py export data\results\bins.sql --format sql --status discovered
python bin_tool.py lookup 411111
python bin_tool.py stats
```

## Database schema

SQLite (`data\bin_tel.sqlite3`), with the same column order used for every
export so a table can be created directly in PostgreSQL:

| column | meaning |
| --- | --- |
| `bin` | the BIN/IIN itself (primary key) |
| `bin_length` | 6 or 8 |
| `issuer` | issuing institution |
| `network` | Visa, Mastercard, ... |
| `card_type` | credit / debit / ... |
| `card_level` | classic, platinum, business, ... |
| `country` | issuing country name |
| `country_code` | ISO 3166-1 alpha-2 |
| `currency` | ISO 4217 |
| `prepaid` | `true` / `false` / `unknown` |
| `commercial` | `true` / `false` / `unknown` |
| `issuer_phone` | issuer contact number |
| `issuer_website` | issuer website |
| `status` | `discovered`, `unconfirmed`, `invalid`, `error`, `imported` |
| `confidence` | 0.0-1.0, from field coverage and cross-provider agreement |
| `source` | which providers supplied the record |
| `checked_at` | UTC timestamp |

Every text field defaults to `unknown`. Supporting tables: `dataset_bins`
(imported reference data), `runs` (one row per validation run) and
`provider_results` (each provider's raw answer, for auditing).

### How `status` and `confidence` are decided

`confidence = (0.6 × field coverage + 0.4 × cross-provider agreement) × provider factor`,
where coverage weights `network`, `issuer`, `country_code` and `card_type`
double, agreement is the share of reported fields the providers agreed on, and
the provider factor is 1.0 when at least two providers answered and 0.85 when
only one did.

A record is `discovered` only when at least one provider found it, no field
conflicted, every field in `required_fields_for_discovery` was resolved and the
confidence clears `min_confidence_for_discovery`. Anything short of that is
`unconfirmed`. All providers reporting "not found" gives `invalid`; all
providers failing gives `error`.

## Providers

Providers are declared in `config.json` and can be enabled, disabled and
reconfigured from menu option [5].

| name | type | what it does |
| --- | --- | --- |
| `offline_iin_ranges` | `offline_iin_ranges` | Derives the network from published ISO/IEC 7812 range allocations. Offline, no network access. Reports nothing else - ranges shared by two networks return no answer rather than a guess. |
| `local_dataset` | `local_dataset` | Answers from the dataset imported through option [3], with exact match first and longest-prefix match as a fallback. |
| `metadata_api` | `http_json` | Disabled template for a licensed BIN metadata API. |

To use an HTTP provider, set `base_url`, map the response fields, and enable
it - only against a service whose terms permit automated metadata lookups, and
with `rate_limit_per_second` inside what they allow. API keys are read from an
environment variable (`api_key_env`), never stored in `config.json`. Requests
are rate limited, retried with exponential backoff on 429/5xx/timeouts, and a
provider failure is recorded rather than allowed to abort the run.

`field_map` accepts dotted paths and a list of candidates per field, first hit
wins:

```json
"field_map": {
  "issuer":  ["bank.name", "issuer", "bank"],
  "network": ["scheme", "network", "brand"],
  "country_code": ["country.alpha2", "country_code"]
}
```

## Configuration

`config.json` is created from the defaults on first run; missing keys are
filled in, so it is safe to keep a partial file.

| setting | default | meaning |
| --- | --- | --- |
| `validation.concurrency` | 4 | worker threads |
| `validation.request_timeout_seconds` | 10.0 | per HTTP request |
| `validation.max_retries` | 2 | retries per request |
| `validation.retry_backoff_seconds` | 1.5 | base of the exponential backoff |
| `validation.min_providers_for_confirmation` | 1 | providers that must find the BIN |
| `validation.required_fields_for_discovery` | `["network", "issuer"]` | fields required for `discovered` |
| `validation.min_confidence_for_discovery` | 0.35 | confidence floor for `discovered` |
| `validation.skip_already_discovered` | true | don't re-check confirmed records |
| `validation.store_provider_results` | true | keep the per-provider audit trail |
| `input.allowed_bin_lengths` | `[6, 8]` | accepted BIN lengths |
| `input.max_input_digits` | 8 | hard ceiling on input length |
| `ui.ascii_symbols` | `"auto"` | use `OK`/`x` instead of `✓`/`✗` on legacy code pages |
| `logging.level` | `INFO` | file log level (`data\logs\bin_tel.log`, rotated) |

## Layout

```
bin_tool/
├── bin_tool.py          entry point: menu and CLI subcommands
├── engine.py            provider fan-out, reconciliation, confidence scoring
├── config.py            defaults, load/save, path resolution
├── config.json          generated on first run
├── requirements.txt
├── build.bat            builds dist\BIN-TEL.exe
├── run.bat              runs from source
├── bin_tool.spec        PyInstaller spec
├── data/
│   ├── input/           BIN lists to validate (sample included)
│   ├── imports/         reference datasets (sample included)
│   ├── results/         exports
│   └── logs/            rotating log files
├── providers/
│   ├── base.py          provider interface, rate limiting, registry
│   ├── offline_provider.py
│   ├── local_provider.py
│   └── public_provider.py   configurable HTTP/JSON provider
├── database/
│   ├── models.py        record shapes, statuses, SQL schema
│   └── database.py      SQLite persistence
├── ui/
│   ├── menu.py          interactive menu
│   ├── colors.py        theme, symbols, status styling
│   └── progress.py      live log, counters, ETA
├── utils/
│   ├── validation.py    BIN normalisation and IIN ranges
│   ├── csv_utils.py     CSV/TXT reading, exports
│   └── logging_utils.py
└── tests/               unittest suite
```

## Tests

```cmd
cd bin_tool
python -m unittest discover -s tests -t .
```
