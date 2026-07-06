"""SQLite persistence for DKEX daily market reports.

Three tables:
  * ``raw_reports``     -- one row per CSV row, verbatim strings, dedup key
                           (business_date, symbol). The lossless source of truth.
  * ``decoded_reports`` -- the tidy/analytical table: raw numeric fields coerced
                           + all decoded symbol fields. Same dedup key.
  * ``parse_warnings``  -- rows whose symbol did not fully parse (schema drift,
                           unknown tokens, malformed symbols).
  * ``ingest_log``      -- one row per file ingested (coverage / data-quality).

Idempotency: both data tables use ``INSERT OR REPLACE`` on the primary key
(business_date, symbol), so re-running the loader never duplicates rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_reports (
    business_date        TEXT NOT NULL,   -- ISO date 'YYYY-MM-DD'
    symbol               TEXT NOT NULL,
    status               TEXT,
    open_interest        TEXT,
    trade_volume         TEXT,
    high_price           TEXT,
    low_price            TEXT,
    settlement_price     TEXT,
    maturity_raw         TEXT,
    source_file          TEXT,
    PRIMARY KEY (business_date, symbol)
);

CREATE TABLE IF NOT EXISTS decoded_reports (
    business_date        TEXT NOT NULL,   -- ISO date 'YYYY-MM-DD'
    symbol               TEXT NOT NULL,
    status               TEXT,
    open_interest        INTEGER,
    trade_volume         INTEGER,
    high_price           REAL,
    low_price            REAL,
    settlement_price     REAL,
    maturity_raw         TEXT,
    maturity_ts          TEXT,            -- ISO timestamp, best-effort
    -- decoded symbol fields --
    league_raw           TEXT,
    league               TEXT,
    league_group         TEXT,
    market_raw           TEXT,
    market               TEXT,
    source_raw           TEXT,
    source               TEXT,
    event_id             TEXT,
    outcome_code         TEXT,
    outcome_name         TEXT,
    outcome_kind         TEXT,
    threshold_op         TEXT,
    line                 REAL,
    n_segments           INTEGER,
    unparsed_tokens      TEXT,
    parse_ok             INTEGER,         -- 0/1
    -- derived dollar estimates (contracts * price). Clearly ESTIMATES. --
    notional_at_price    REAL,            -- trade_volume * settlement_price
    max_notional         REAL,            -- trade_volume * 1.00
    source_file          TEXT,
    PRIMARY KEY (business_date, symbol)
);

-- Daily Settlement Report: how each contract RESOLVED. One row per settled
-- contract. 'Market Name' is Railbird's own human-readable label -> the source
-- of VERIFIED outcome names (see verified_names). Same dedup key as decoded.
CREATE TABLE IF NOT EXISTS settlement_reports (
    report_date          TEXT NOT NULL,   -- ISO date from the FILENAME (join key)
    symbol               TEXT NOT NULL,   -- == decoded_reports.symbol (the ticker)
    market_name          TEXT,            -- human label, e.g. 'ARI Diamondbacks -4.5'
    status               TEXT,            -- e.g. 'Settled Early'
    settlement_raw       TEXT,
    settlement_ts        TEXT,            -- ISO timestamp, best-effort
    settlement_price     REAL,            -- $1 YES / $0 NO / $0.5 push
    -- decoded symbol fields --
    league_raw           TEXT,
    league               TEXT,
    league_group         TEXT,
    market_raw           TEXT,
    market               TEXT,
    source_raw           TEXT,
    source               TEXT,
    event_id             TEXT,
    outcome_code         TEXT,
    outcome_kind         TEXT,
    line                 REAL,
    source_file          TEXT,
    PRIMARY KEY (report_date, symbol)
);

-- Time & Sales Report: the TRADE TAPE. One row per individual execution.
-- No natural primary key (many trades per symbol); idempotency is achieved by
-- deleting a file's rows before re-inserting it (a file is the atomic unit).
CREATE TABLE IF NOT EXISTS time_and_sales (
    report_date          TEXT NOT NULL,   -- ISO date from the FILENAME (join key)
    business_date_raw    TEXT,            -- the file's internal Business Date (offset +1)
    symbol               TEXT NOT NULL,
    transaction_raw      TEXT,
    transaction_ts       TEXT,            -- ISO timestamp (minute precision)
    price                REAL,            -- intraday last price ($0.01-$0.99 = implied prob)
    quantity             INTEGER,         -- contracts in this fill
    notional_at_price    REAL,            -- price * quantity ($ est. for this trade)
    -- decoded symbol fields --
    league_raw           TEXT,
    league               TEXT,
    league_group         TEXT,
    market_raw           TEXT,
    market               TEXT,
    source_raw           TEXT,
    source               TEXT,
    event_id             TEXT,
    outcome_code         TEXT,
    outcome_kind         TEXT,
    line                 REAL,
    source_file          TEXT
);

-- Verified outcome names harvested from the settlement report's Market Name.
-- Replaces the heuristic team map where available. Keyed by (league, code).
CREATE TABLE IF NOT EXISTS verified_names (
    league_raw           TEXT NOT NULL,
    outcome_code         TEXT NOT NULL,
    name                 TEXT,            -- clean entity name (team / player)
    from_market          TEXT,           -- which market type the name came from
    PRIMARY KEY (league_raw, outcome_code)
);

CREATE TABLE IF NOT EXISTS parse_warnings (
    business_date        TEXT,
    symbol               TEXT,
    n_segments           INTEGER,
    warning              TEXT,
    unparsed_tokens      TEXT,
    source_file          TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
    source_file          TEXT PRIMARY KEY,
    report_type          TEXT,            -- 'daily-market' | 'daily-settlement' | 'time-and-sales'
    business_date        TEXT,
    rows_total           INTEGER,
    rows_parsed_ok       INTEGER,
    rows_warned          INTEGER,
    ingested_at          TEXT             -- caller-supplied ISO timestamp
);

CREATE INDEX IF NOT EXISTS idx_decoded_date    ON decoded_reports(business_date);
CREATE INDEX IF NOT EXISTS idx_decoded_league  ON decoded_reports(league_raw);
CREATE INDEX IF NOT EXISTS idx_decoded_market  ON decoded_reports(market_raw);
CREATE INDEX IF NOT EXISTS idx_decoded_source  ON decoded_reports(source_raw);
CREATE INDEX IF NOT EXISTS idx_decoded_event   ON decoded_reports(event_id);
CREATE INDEX IF NOT EXISTS idx_decoded_status  ON decoded_reports(status);

CREATE INDEX IF NOT EXISTS idx_settle_date   ON settlement_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_settle_league ON settlement_reports(league_raw);
CREATE INDEX IF NOT EXISTS idx_settle_market ON settlement_reports(market_raw);

CREATE INDEX IF NOT EXISTS idx_tas_date    ON time_and_sales(report_date);
CREATE INDEX IF NOT EXISTS idx_tas_symbol  ON time_and_sales(symbol);
CREATE INDEX IF NOT EXISTS idx_tas_league  ON time_and_sales(league_raw);
CREATE INDEX IF NOT EXISTS idx_tas_market  ON time_and_sales(market_raw);
CREATE INDEX IF NOT EXISTS idx_tas_ts      ON time_and_sales(transaction_ts);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Open (creating dirs as needed) a SQLite connection with the schema applied."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def already_ingested_dates(conn: sqlite3.Connection) -> set[str]:
    """ISO dates already present in decoded_reports (for incremental loads)."""
    cur = conn.execute("SELECT DISTINCT business_date FROM decoded_reports;")
    return {r[0] for r in cur.fetchall()}
