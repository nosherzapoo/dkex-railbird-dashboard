"""dkex -- ingest & analyze Railbird Exchange Daily Market Reports (DKEX venue).

Modules:
    reference  -- friendly-name lookup tables (leagues, markets, teams, ...)
    symbols    -- decode a contract Symbol into structured fields
    store      -- SQLite schema + connection helpers (raw + decoded + logs)
    fetch      -- download daily report CSVs (idempotent, rate-limited)
    load       -- parse raw CSVs and upsert into the store (idempotent)
    data       -- query helpers for the dashboard
"""

__version__ = "0.1.0"

DEFAULT_DB = "data/processed/dkex.sqlite"
RAW_DIR = "data/raw"
