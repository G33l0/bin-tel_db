# bin-tel

Console tool for building a BIN/IIN metadata database from CSV/TXT lists and from
imported reference datasets. Runs on Windows (also Linux/macOS), Python 3.9+.

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

## Install

```cmd
cd bin_tool
pip install -r requirements.txt
python bin_tool.py
```

`run.bat` does the same and installs the dependencies if they are missing.

Standalone executable:

```cmd
cd bin_tool
build.bat
```

Produces `dist\BIN-TEL.exe` with Python bundled. It writes `config.json` and the
`data\` folders next to itself on first run.

## Menu

**1. Input CSV/TXT.** Lists `data\input`, reads BIN values and queues the valid
ones. CSV uses the first column whose header is `bin`, `iin`, `prefix` or a
similar alias, otherwise the first column. TXT takes one value per line, or
comma separated values on one line. Separators are stripped before validation;
rejects are listed with a reason and can be written to a report file.

**2. Validate BINs.** Runs the queue through every ready provider, reconciles
the answers and stores the result.

```
──────────────────────────── BIN-TEL VALIDATOR ─────────────────────────────
[08:42:11] Processing 411111
[08:42:11] ✓ Issuer: Example Bank NA
[08:42:11] ✓ Visa
[08:42:11] ✓ Card Type: credit
[08:42:11] ✓ Country: United States
[08:42:11] DISCOVERED 411111

[08:42:13] Processing 522222
[08:42:13] ! Conflicting issuer (local_dataset=Bank A, metadata_api=Bank B)
[08:42:13] SKIPPED - requires verification

Progress ━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 37 / 100
Discovered                 31
Unconfirmed                 4
Invalid                     2
Errors                      0
Average response        1.42s
Estimated remaining  00:01:29
```

Green is confirmed, yellow is uncertain or conflicting, red is invalid or error.
Symbols drop to `OK`/`x` on code pages that cannot render them.

**3. Import BIN Dataset.** Loads a reference dataset (CSV) from `data\imports`
into the `dataset_bins` table, which the `local_dataset` provider reads. Rows
can optionally be copied into the main BIN table with status `imported`. Values
are normalised on import (network casing, ISO country and currency codes,
booleans), so the stored data is consistent regardless of how the file spells
them.

Column headers are matched against a wide alias set, so the common open BIN
datasets import without editing. Both of these layouts work as-is:

```
BIN;Brand;Type;Category;Issuer;IssuerURL;isCommercial;isPrepaid;CountryCode
iin,scheme,type,level,bank_name,bank_url,bank_phone,alpha_2,currency
```

The delimiter (comma, semicolon, tab or pipe) is detected automatically. If no
column maps to a BIN, the import stops with an error naming the accepted BIN
column names. Sample files in both layouts are in `data\imports`.

**4. Export Results.** Writes to `data\results` as CSV, JSON or SQL. The SQL
export includes a matching `CREATE TABLE`.

**5. Config.** Shows every setting and each provider's readiness, edits
`config.json` in place.

**6. Statistics.** Totals by status, network and country, average confidence,
dataset size, recent runs.

## Command line

```cmd
python bin_tool.py validate data\input\bins.csv --export data\results\out.csv
python bin_tool.py validate --only unconfirmed --limit 500
python bin_tool.py import data\imports\dataset.csv --name acme_2026 --to-bins
python bin_tool.py export data\results\bins.sql --format sql --status discovered
python bin_tool.py lookup 411111
python bin_tool.py cache
python bin_tool.py stats
```

## Schema

SQLite at `data\bin_tel.sqlite3`. The `bins` table and every export share this
column order, so the export drops straight into PostgreSQL:

| column | meaning |
| --- | --- |
| `bin` | BIN/IIN, primary key |
| `bin_length` | 6 or 8 |
| `issuer` | issuing institution |
| `network` | Visa, Mastercard, ... |
| `card_type` | credit, debit, ... |
| `card_level` | classic, platinum, business, ... |
| `country` | issuing country |
| `country_code` | ISO 3166-1 alpha-2 |
| `currency` | ISO 4217 |
| `prepaid` | `true`, `false`, `unknown` |
| `commercial` | `true`, `false`, `unknown` |
| `issuer_phone` | issuer contact number |
| `issuer_website` | issuer website |
| `status` | `discovered`, `unconfirmed`, `invalid`, `error`, `imported` |
| `confidence` | 0.0 to 1.0 |
| `source` | providers that supplied the record |
| `checked_at` | UTC timestamp |

Text fields default to `unknown`. Supporting tables: `dataset_bins` (imported
reference data), `runs` (one row per validation run), `provider_results` (raw
per-provider answers, kept for auditing), `http_cache` (one stored answer per
HTTP provider and BIN, so a BIN is never fetched twice).

## Reconciliation

A field is written only when the providers that reported it agree, compared
after case folding and punctuation normalisation ("Example Bank, N.A." matches
"Example Bank NA"). On disagreement the field stays `unknown`, the reporters are
recorded in `conflicts`, and the record is marked `unconfirmed`. A field nobody
reported stays `unknown`; the tool does not fill gaps by inference.

```
confidence = (0.6 * coverage + 0.4 * agreement) * provider_factor
```

`coverage` is the share of fields resolved, weighting `network`, `issuer`,
`country_code` and `card_type` double. `agreement` is the share of reported
fields the providers agreed on. `provider_factor` is 1.0 with two or more
answering providers, 0.85 with one.

Status:

| status | condition |
| --- | --- |
| `discovered` | found, no conflicts, `required_fields_for_discovery` resolved, confidence above `min_confidence_for_discovery` |
| `unconfirmed` | found but conflicting, incomplete or below the threshold |
| `invalid` | every provider reported not found |
| `error` | no provider found it and at least one failed, so absence is unproven |

## Providers

Declared in `config.json`, editable from menu option 5.

| name | type | source |
| --- | --- | --- |
| `offline_iin_ranges` | `offline_iin_ranges` | Network from published ISO/IEC 7812 range allocations. No network access. Ranges shared by two networks return no answer. |
| `local_dataset` | `local_dataset` | Dataset imported with option 3. Exact match first, then longest prefix. |
| `binlist` | `binlist` | binlist.net public lookup. No API key. Issuer, country, currency, card type. Rate limited and cached. Disabled by default. |
| `metadata_api` | `http_json` | Disabled template for a licensed BIN metadata API. |

### binlist

`binlist` is ready to use with no key: enable it in menu option 5 (or set
`"enabled": true` on its config entry). It supplies the issuer, country,
currency and card type that the offline provider cannot, which is what moves a
record from `unconfirmed` to `discovered`.

binlist enforces a low rate limit (a few requests per minute), so the provider
defaults to `rate_limit_per_second: 0.08`. It is meant for filling gaps and
verifying a subset, not for sweeping hundreds of thousands of BINs in one pass.
Data is crowd-sourced, so some BINs return no bank name and stay `unconfirmed`;
that is expected, not a fault.

To work through a large list, run against the stored backlog in slices:

```cmd
python bin_tool.py validate --only unconfirmed --limit 500
```

Every HTTP response is cached in the database, so re-runs and overlapping lists
never fetch the same BIN twice. Inspect or clear the cache:

```cmd
python bin_tool.py cache
python bin_tool.py cache --clear
python bin_tool.py cache --clear --provider binlist
```

The HTTP provider is generic: set `base_url`, map the response fields, enable it.
Requests are rate limited by `rate_limit_per_second` and retried with
exponential backoff on 429, 5xx and timeouts. A provider failure is recorded
against the BIN instead of aborting the run. API keys are read from the
environment variable named in `api_key_env` and are never written to
`config.json`.

`field_map` takes dotted paths and a list of candidates per field, first hit
wins:

```json
"field_map": {
  "issuer":  ["bank.name", "issuer", "bank"],
  "network": ["scheme", "network", "brand"],
  "country_code": ["country.alpha2", "country_code"]
}
```

Adding a provider type: subclass `BaseProvider`, implement `fetch()`, register
it in `providers/base.py:provider_registry`.

## Config

`config.json` is created from the defaults on first run and missing keys are
filled in, so a partial file is fine.

| setting | default |
| --- | --- |
| `validation.concurrency` | 4 |
| `validation.request_timeout_seconds` | 10.0 |
| `validation.max_retries` | 2 |
| `validation.retry_backoff_seconds` | 1.5 |
| `validation.min_providers_for_confirmation` | 1 |
| `validation.required_fields_for_discovery` | `["network", "issuer"]` |
| `validation.min_confidence_for_discovery` | 0.35 |
| `validation.skip_already_discovered` | true |
| `validation.store_provider_results` | true |
| `input.allowed_bin_lengths` | `[6, 8]` |
| `input.max_input_digits` | 8 |
| `ui.ascii_symbols` | `"auto"` |
| `logging.level` | `INFO` |

HTTP providers also take `cache_responses` (default true) and `cache_ttl_days`
(default 0, meaning cached answers never expire).

Logs go to `data\logs\bin_tel.log`, rotated at 2 MB, five backups.

## Layout

```
bin_tool/
├── bin_tool.py        entry point, menu and CLI
├── engine.py          provider fan-out, reconciliation, scoring
├── config.py          defaults, load/save, path resolution
├── bin_tool.spec      PyInstaller spec
├── build.bat          builds dist\BIN-TEL.exe
├── run.bat            runs from source
├── data/              input, imports, results, logs, database
├── providers/         base, offline_provider, local_provider, public_provider
├── database/          models, database
├── ui/                menu, colors, progress
├── utils/             validation, csv_utils, logging_utils
└── tests/
```

## Tests

```cmd
cd bin_tool
python -m unittest discover -s tests -t .
```

## Scope

The tool handles issuer identification numbers only, the first 6 or 8 digits,
plus public issuer metadata. Input longer than `input.max_input_digits` is
rejected before anything else runs, so full card numbers, CVVs, expiry dates and
cardholder data never reach the pipeline or the database. There is no
enumeration mode: it processes the BINs you supply or the dataset you import,
and does not sweep 000000-999999 against third party services.
