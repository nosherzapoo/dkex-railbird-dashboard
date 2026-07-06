# DKEX / Railbird Exchange — Daily Market Report Pipeline & Dashboard

A local, reproducible pipeline that ingests **Railbird Exchange report CSVs** (the
CFTC-regulated venue DraftKings' DKEX prediction markets run on) and an interactive
dashboard to analyze trade volume, contracts, open interest, and pricing across
many cuts — by sport, market type, event, date, and more.

It ingests **all three** Railbird report families, which reconcile exactly:

| Report | Grain | Adds |
|---|---|---|
| **Daily Market Report** | per contract, end-of-day | listed/OI/daily volume, high/low |
| **Daily Settlement Report** | per settled contract | resolved $1/$0/$0.50 outcomes + **verified** entity names |
| **Time & Sales Report** | per trade (tick) | intraday **traded prices** (implied prob.), timing, size |

Sanity-checked: Time & Sales quantities sum to the Daily Market Report's Trade
Volume, and the Settlement Report row count equals its `Settled` contracts.

> **Correctness over polish.** The single most important caveat: **Trade Volume is
> a count of _contracts_, not dollars.** Every chart and table in the dashboard
> labels its unit, and the two dollar figures are always flagged as **estimates**.
> Details in [Caveats](#caveats-read-this).

---

## What the pipeline does

```
railbirdexchange.com                data/raw/*.csv           data/processed/dkex.sqlite         Streamlit
  manifest.json  ──fetch──▶  Daily_Market_Report_*.csv ──load──▶  raw_reports + decoded_reports ──▶  dashboard
                             (idempotent, cached)                  + parse_warnings + ingest_log
```

1. **Fetch** — downloads daily report CSVs, idempotently, rate-limited & polite.
2. **Load** — decodes each row's `Symbol` into structured fields and upserts into
   SQLite (dedupe on `(business_date, symbol)`; re-runs never duplicate rows).
3. **Dashboard** — one-command Streamlit app with global filters and all the cuts.

---

## The download mechanism (Step 0 findings)

The reports page (`/daily-market-reports`) is a client-rendered Remix/Vite SPA —
the CSV list is **not** in the initial HTML. Reverse-engineering the JS bundles
(`assets/reports-*.js`) revealed the real data source:

- **Manifest (JSON listing), one per family:**
  `GET https://railbirdexchange.com/reports/<family>/manifest.json` where
  `<family>` ∈ `daily-market`, `daily-settlement`, `time-and-sales`. Each returns
  `{"reports": [{ "name", "date": "YYYY-MM-DD", "href": "/reports/<family>/… .csv" }, ...]}`
- **File:** `GET https://railbirdexchange.com<href>` (spaces/parens/`&` URL-encoded).
- No `robots.txt` exists (404). The fetcher still sends a descriptive
  `User-Agent`, rate-limits to **1 req/sec**, retries with exponential backoff,
  and caches the manifest to `data/processed/manifest_cache.json`.
- Dates with no report (weekends/holidays) are simply **absent** from the
  manifest — the fetcher logs the gap and moves on, never crashes.

The on-disk filename is normalized to the sample convention:
`Daily_Market_Report_-__YYYY_MM_DD_.csv`. The loader reads the date from the CSV's
`Business Date` column, not the filename, so any naming works.

---

## Setup

Requires **Python 3.10+**.

```bash
cd dkex
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

(Optional) install the package so `dkex-fetch` / `dkex-load` are on your PATH:

```bash
pip install -e .
```

Otherwise run modules with `PYTHONPATH=src` (examples below use that form).

---

## Usage

### 1. Fetch report CSVs

```bash
# Download any dates not already on disk, for ALL three families (the default):
PYTHONPATH=src python -m dkex.fetch

# Just one family:
PYTHONPATH=src python -m dkex.fetch --report time-and-sales
#   choices: daily-market | daily-settlement | time-and-sales | all

# A specific inclusive date range:
PYTHONPATH=src python -m dkex.fetch --start 2026-06-11 --end 2026-06-20

# Just list what the manifests offer (downloads nothing):
PYTHONPATH=src python -m dkex.fetch --list

# Re-download files even if present:
PYTHONPATH=src python -m dkex.fetch --force
```

The loader auto-detects each CSV's family by filename and routes it to the right
parser — a single `python -m dkex.load` ingests all three and (re)builds the
verified-name lookup.

Raw files land in `data/raw/`. The fetch is **idempotent** — existing dates are
skipped unless `--force`.

### 2. Load into the store

```bash
# Parse every new CSV in data/raw/ into SQLite:
PYTHONPATH=src python -m dkex.load

# Re-load everything (e.g. after a parser change):
PYTHONPATH=src python -m dkex.load --force
```

Loading is **idempotent** (upsert on `(business_date, symbol)`) and incremental
(already-ingested files are skipped by default).

### 3. Refresh data (daily routine)

```bash
PYTHONPATH=src python -m dkex.fetch     # grab new dates
PYTHONPATH=src python -m dkex.load      # decode + upsert new files
```

### 4. Launch the dashboard

```bash
streamlit run app/dashboard.py
```

Opens at `http://localhost:8501`. Everything runs **offline** once data is fetched.

### 5. Run the tests

```bash
PYTHONPATH=src python -m pytest -q
# or, if installed:  pytest -q
```

The suite validates the symbol parser against every example in the spec (including
the synthetic unknown league `WCUP-...`), the `$`/empty price coercion, threshold
line decoding, and asserts every row of the real sample file parses without error.

---

## Dashboard tour

Global sidebar controls (date range; multi-select league / market / source /
status; daily/weekly/monthly roll-up) apply to every view.

- **Headline KPIs** — contracts traded, notional@price (est.), max notional (est.),
  distinct traded contracts, distinct events (approx.), open interest, leagues
  active, listed-vs-traded breadth.
- **📈 Time series** — contracts, dollar estimates, open interest, and listed-vs-
  traded over time.
- **🧩 Mix / composition** — stacked / 100%-stacked area + point-in-time bars by
  league, market type, source/period, and baseball-vs-golf.
- **🏆 Leaderboards** — top events, top individual contracts, volume by team
  (baseball, **settlement-verified names**) / player (golf), and the volume
  distribution across TRUNS/MOVY lines.
- **⏱️ Time & Sales (intraday)** — the trade tape: VWAP, an intraday **price path**
  per contract (price = implied probability), activity by hour of day, trade-size
  distribution, and a per-contract VWAP/range table.
- **⚖️ Settlement** — resolution split ($1 YES / $0 NO / $0.50 push), resolution by
  market type, and the settlement tape with Railbird's human-readable names.
- **💧 Liquidity & structure** — fill rate, concentration (HHI + top-1/5/10/25
  share + cumulative-share curve), contracts per event/line, settlement outcome
  distribution ($1 vs $0).
- **🧮 Reconciliation** — annualizes the selected range to a run-rate and compares
  against manually-entered benchmarks (defaults seeded from DK's Q1'26 call:
  ~$1B annualized consumer, ~$2.3B total). Clearly captioned as exchange-only.
- **🆕 New / Novelty** — first-seen date for every league / market / source code,
  flagging any without a friendly name. A "**What's new**" banner on the main page
  highlights debuts so you catch soccer/World Cup, NFL, NBA when they first list.
- **🩺 Data quality** — files ingested, rows parsed vs. warned, date-coverage gaps,
  and the `parse_warnings` table.
- **⬇️ Export** — every table downloads as CSV, plus a combined tidy-dataset button
  (filtered or full).

---

## Symbol grammar

Hyphen-delimited, 5–7 segments:

```
LEAGUE-MARKETTYPE-SOURCE-EVENTID-OUTCOME[-OP-P000XX]
```

| Segment | Meaning | Examples |
|---|---|---|
| LEAGUE | sport / league | `MLB`, `KBO`, `NPB`, `PGA`, … (open set) |
| MARKETTYPE | market | `WIN`, `TRUNS`, `MOVY`, `WTRN`, `MCUT`, `T5`…`T40`, `3BLS`, `EORL` |
| SOURCE | period | `FG` (full game), `IT5` (first 5), `TRN`, `R1`–`R3` |
| EVENTID | game/tournament id | long alphanumeric token (groups contracts) |
| OUTCOME | team / player | `NYTS000` (team), golf player code, or absent for totals |
| OP-P000XX | threshold | `GTE-P00055` → line `5.5` (`XX/10`) |

The parser (`src/dkex/symbols.py`) is **defensive**: it locates the `P#####` line
token structurally rather than trusting segment counts, keeps both raw tokens and
decoded fields, and never raises — odd input is flagged (`parse_ok=False` /
`warning`) and logged to `parse_warnings`, not dropped. Unknown league/market/
source codes load fine and fall back to their raw value.

Worked examples:

| Symbol | Decodes to |
|---|---|
| `MLB-WIN-IT5-0146NBSSL8SDU3G-NYTS000` | MLB · Moneyline · First-5 · team `NYTS000` · no line |
| `MLB-TRUNS-IT5-0145BO10L8S8G70-GTE-P00055` | Total Runs · line **5.5** · no team |
| `KBO-MOVY-FG-00N8UBQBPIT76T0-DORS000-GTE-P00015` | KBO · Run Line · full game · team `DORS000` · line **1.5** |
| `PGA-3BLS-R1-…-BRAN008` | PGA · 3 Balls · Round 1 · player `BRAN008` |

---

## Caveats (read this)

- **Contracts ≠ dollars.** `Trade Volume` counts binary contracts that settle at
  **$1.00 (YES)** or **$0.00 (NO)**. The two dollar figures are estimates:
  - **Notional @ price** = Σ(volume × settlement/last price) — realistic proxy.
  - **Max notional** = Σ(volume × $1) — absolute ceiling.
- **"Distinct events" is approximate** — the event-id token can differ across
  market types for the same real-world game.
- **Team/player names: verified where possible, else heuristic.** Outcome codes
  (e.g. `LARS000`) are obfuscated. The loader harvests **verified** names from the
  Daily Settlement Report's `Market Name` column (Railbird's own labels) into a
  `verified_names` table — this covered all 52 baseball team codes in the sample
  window and even corrected two heuristic guesses. Where no settlement name exists
  yet, the code falls back to the best-effort map in `src/dkex/reference.py`, then
  the raw code. The UI shows how many codes are verified.
- **Railbird ≠ all of DKEX.** These reports capture only the **regulated-exchange**
  layer — not DraftKings' full Predictions/consumer volume or market-making. The
  reconciliation tab is a floor/sanity check, not an apples-to-apples tie-out.
- **Breadth is real, not a bug.** In a typical file the vast majority of listed
  contracts have zero volume (future events listed early). The dashboard surfaces
  this as a "listed vs. traded" metric rather than filtering it away.

---

## Project layout

```
dkex/
├── app/dashboard.py         # Streamlit dashboard
├── src/dkex/
│   ├── reference.py         # friendly-name lookups (leagues, markets, teams…)
│   ├── symbols.py           # Symbol -> structured fields (defensive parser)
│   ├── store.py             # SQLite schema + connection (raw/decoded/warnings/log)
│   ├── fetch.py             # downloader for all 3 families (rate-limit, backoff, cache)
│   ├── load.py              # CSV -> parse -> idempotent upsert; routes by filename
│   └── data.py              # query helpers for the dashboard
├── tests/test_symbols.py    # parser + coercion unit tests
├── tests/test_reports.py    # settlement + time&sales loaders, verified names
├── data/raw/                # downloaded CSVs (gitignored)
├── data/processed/          # dkex.sqlite + manifest cache (gitignored)
├── requirements.txt         # pinned deps
├── pyproject.toml           # package + console scripts + pytest config
└── README.md
```

### Store schema

- `raw_reports` — verbatim Daily Market rows (lossless), PK `(business_date, symbol)`.
- `decoded_reports` — tidy analytical table: coerced numerics + all decoded symbol
  fields + `notional_at_price` / `max_notional`. PK `(business_date, symbol)`.
  Indexed on date, league, market, source, event, status.
- `settlement_reports` — Daily Settlement rows: resolved price, status, timestamp,
  `market_name`, decoded fields. PK `(report_date, symbol)`.
- `time_and_sales` — the trade tape: one row per execution (price, quantity,
  timestamp, decoded fields). No PK; idempotent via delete-by-file.
- `verified_names` — clean entity names harvested from settlement `Market Name`,
  keyed `(league_raw, outcome_code)`.
- `parse_warnings` — rows with unparsed/unknown tokens (schema-drift diagnostics).
- `ingest_log` — one row per file: `report_type`, totals, parsed vs. warned, timestamp.

> **Date-key nuance:** the Time & Sales file's internal `Business Date` column is
> offset +1 from its filename date. The loader keys `time_and_sales.report_date`
> (and `settlement_reports.report_date`) off the **filename**, so all three
> families join on the same date — the raw internal value is kept in
> `business_date_raw` for transparency.

---

## Engineering notes

- **Idempotent & incremental** ingestion throughout (upsert + skip-already-done).
- **Schema-drift tolerant** — columns matched by header name; unknown leagues/
  markets/sources load and are surfaced in the novelty view rather than crashing.
- **Vectorized** pandas in the dashboard; SQLite indexed for the query paths.
- **Offline-first** — once CSVs are fetched, nothing hits the network; the manifest
  is cached so `--list` works offline too.
