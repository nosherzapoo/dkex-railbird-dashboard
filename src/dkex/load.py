"""Parse raw Railbird CSVs and load them into SQLite (idempotent, all 3 families).

The loader classifies each CSV in data/raw/ by filename and routes it to the
right parser:
    Daily Market Report   -> decoded_reports (+ raw_reports)
    Daily Settlement Report -> settlement_reports
    Time & Sales Report   -> time_and_sales
After loading, it rebuilds `verified_names` from the settlement Market Names.

Usage:
    python -m dkex.load                    # load every new CSV in data/raw/
    python -m dkex.load --force            # re-load even already-ingested files
    python -m dkex.load --files a.csv b.csv

Robustness: reads dates from the CSV column (market) or the filename (settlement /
time-and-sales, whose internal Business Date is offset), tolerates the '$' prefix
and empty prices, and never drops rows -- odd symbols are logged to parse_warnings.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
from datetime import datetime, timezone

from . import DEFAULT_DB, RAW_DIR, store
from .symbols import parse_symbol

# ---- Daily Market Report headers ---------------------------------------------
MARKET_COLUMN_MAP = {
    "business date": "business_date",
    "symbol": "symbol",
    "status": "status",
    "open interest": "open_interest",
    "trade volume": "trade_volume",
    "high price (usd)": "high_price",
    "low price (usd)": "low_price",
    "settlement/last trade price (usd)": "settlement_price",
    "maturity date and time (et)": "maturity_raw",
}
# ---- Daily Settlement Report headers -----------------------------------------
SETTLE_COLUMN_MAP = {
    "market name": "market_name",
    "ticker": "symbol",
    "status": "status",
    "date and time of settlement (et)": "settlement_raw",
    "price (usd)": "settlement_price",
}
# ---- Time & Sales Report headers ---------------------------------------------
TAS_COLUMN_MAP = {
    "business date": "business_date_raw",
    "symbol": "symbol",
    "transaction date and time": "transaction_raw",
    "last price (usd)": "price",
    "last quantity": "quantity",
}

_FILE_DATE_RE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})_")
# ET timestamp formats, most precise first. Trailing tz token (EDT/EST) is stripped.
_TS_FORMATS = (
    "%m/%d/%y %I:%M:%S.%f %p",   # settlement, sub-second
    "%m/%d/%Y %I:%M:%S.%f %p",
    "%m/%d/%y %I:%M:%S %p",
    "%m/%d/%y %I:%M %p",         # time & sales, and maturity, minute precision
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%y %H:%M",
)


def _norm_header(h: str) -> str:
    return (h or "").strip().lower()


# ------------------------------- coercion -------------------------------------
def parse_price(val: str | None) -> float | None:
    """'$1.00' -> 1.0 ; '' / None -> None ; tolerant of stray commas/spaces."""
    if val is None:
        return None
    s = str(val).strip().replace("$", "").replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(val: str | None) -> int:
    if val is None:
        return 0
    s = str(val).strip().replace(",", "")
    if s == "":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def parse_business_date(val: str | None) -> str | None:
    """'20260705' (or ISO-ish) -> '2026-07-05'. Returns None if unparseable."""
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_et_timestamp(val: str | None) -> str | None:
    """'07/05/26 12:20:29.156 AM EDT' / '07/24/26 10:30 AM EDT' -> ISO. Best-effort."""
    if not val:
        return None
    s = str(val).strip()
    parts = s.rsplit(" ", 1)          # drop trailing 'EDT'/'EST' token
    if len(parts) == 2 and parts[1].isalpha():
        s = parts[0]
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


# Maturity uses the same ET grammar; kept as a named alias for clarity/tests.
parse_maturity = parse_et_timestamp


def file_date_from_name(fname: str) -> str | None:
    """Extract the ISO report date embedded in a normalized filename."""
    m = _FILE_DATE_RE.search(fname)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def classify(fname: str) -> str:
    low = fname.lower()
    if "settlement" in low:
        return "daily-settlement"
    if "time" in low and "sales" in low:
        return "time-and-sales"
    return "daily-market"


def _header_index(header, column_map):
    idx = {}
    for i, h in enumerate(header):
        key = column_map.get(_norm_header(h))
        if key:
            idx[key] = i
    return idx


def _row_getter(idx):
    def get(row, key):
        i = idx.get(key)
        return row[i] if i is not None and i < len(row) else None
    return get


# ------------------------------- loaders --------------------------------------
def load_market_file(conn, path, fname, now_iso) -> dict:
    raw_rows, decoded_rows, warnings = [], [], []
    n_total = n_ok = n_warn = 0
    file_date = None
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return _empty_stats(fname, "daily-market")
        idx = _header_index(header, MARKET_COLUMN_MAP)
        get = _row_getter(idx)
        for row in reader:
            if not row or all((c or "").strip() == "" for c in row):
                continue
            n_total += 1
            bdate = parse_business_date(get(row, "business_date"))
            file_date = file_date or bdate
            symbol = (get(row, "symbol") or "").strip()
            raw_rows.append((
                bdate, symbol, get(row, "status"), get(row, "open_interest"),
                get(row, "trade_volume"), get(row, "high_price"),
                get(row, "low_price"), get(row, "settlement_price"),
                get(row, "maturity_raw"), fname))
            p = parse_symbol(symbol)
            vol = parse_int(get(row, "trade_volume"))
            settle = parse_price(get(row, "settlement_price"))
            nap = vol * settle if (vol and settle is not None) else 0.0
            decoded_rows.append((
                bdate, symbol, get(row, "status"),
                parse_int(get(row, "open_interest")), vol,
                parse_price(get(row, "high_price")), parse_price(get(row, "low_price")),
                settle, get(row, "maturity_raw"), parse_et_timestamp(get(row, "maturity_raw")),
                p.league_raw, p.league, p.league_group, p.market_raw, p.market,
                p.source_raw, p.source, p.event_id, p.outcome_code, p.outcome_name,
                p.outcome_kind, p.threshold_op, p.line, p.n_segments,
                p.unparsed_tokens, 1 if p.parse_ok else 0, nap,
                float(vol) if vol else 0.0, fname))
            if p.parse_ok and not p.warning:
                n_ok += 1
            else:
                n_warn += 1
                warnings.append((bdate, symbol, p.n_segments, p.warning,
                                p.unparsed_tokens, fname))
    cur = conn.cursor()
    cur.executemany("INSERT OR REPLACE INTO raw_reports VALUES (?,?,?,?,?,?,?,?,?,?)",
                    raw_rows)
    cur.executemany("INSERT OR REPLACE INTO decoded_reports VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    decoded_rows)
    cur.execute("DELETE FROM parse_warnings WHERE source_file=?", (fname,))
    cur.executemany("INSERT INTO parse_warnings VALUES (?,?,?,?,?,?)", warnings)
    _log_ingest(cur, fname, "daily-market", file_date, n_total, n_ok, n_warn, now_iso)
    conn.commit()
    return dict(file=fname, report_type="daily-market", rows_total=n_total,
                rows_ok=n_ok, rows_warned=n_warn, business_date=file_date)


def load_settlement_file(conn, path, fname, now_iso) -> dict:
    rows, warnings = [], []
    n_total = n_ok = n_warn = 0
    rdate = file_date_from_name(fname)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return _empty_stats(fname, "daily-settlement")
        idx = _header_index(header, SETTLE_COLUMN_MAP)
        get = _row_getter(idx)
        for row in reader:
            if not row or all((c or "").strip() == "" for c in row):
                continue
            n_total += 1
            symbol = (get(row, "symbol") or "").strip()
            p = parse_symbol(symbol)
            rows.append((
                rdate, symbol, get(row, "market_name"), get(row, "status"),
                get(row, "settlement_raw"), parse_et_timestamp(get(row, "settlement_raw")),
                parse_price(get(row, "settlement_price")),
                p.league_raw, p.league, p.league_group, p.market_raw, p.market,
                p.source_raw, p.source, p.event_id, p.outcome_code, p.outcome_kind,
                p.line, fname))
            if p.parse_ok and not p.warning:
                n_ok += 1
            else:
                n_warn += 1
                warnings.append((rdate, symbol, p.n_segments, p.warning,
                                p.unparsed_tokens, fname))
    cur = conn.cursor()
    cur.execute("DELETE FROM settlement_reports WHERE source_file=?", (fname,))
    cur.executemany("INSERT OR REPLACE INTO settlement_reports VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    cur.execute("DELETE FROM parse_warnings WHERE source_file=?", (fname,))
    cur.executemany("INSERT INTO parse_warnings VALUES (?,?,?,?,?,?)", warnings)
    _log_ingest(cur, fname, "daily-settlement", rdate, n_total, n_ok, n_warn, now_iso)
    conn.commit()
    return dict(file=fname, report_type="daily-settlement", rows_total=n_total,
                rows_ok=n_ok, rows_warned=n_warn, business_date=rdate)


def load_timesales_file(conn, path, fname, now_iso) -> dict:
    rows, warnings = [], []
    n_total = n_ok = n_warn = 0
    rdate = file_date_from_name(fname)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return _empty_stats(fname, "time-and-sales")
        idx = _header_index(header, TAS_COLUMN_MAP)
        get = _row_getter(idx)
        for row in reader:
            if not row or all((c or "").strip() == "" for c in row):
                continue
            n_total += 1
            symbol = (get(row, "symbol") or "").strip()
            p = parse_symbol(symbol)
            price = parse_price(get(row, "price"))
            qty = parse_int(get(row, "quantity"))
            nap = price * qty if (price is not None and qty) else 0.0
            rows.append((
                rdate, get(row, "business_date_raw"), symbol,
                get(row, "transaction_raw"), parse_et_timestamp(get(row, "transaction_raw")),
                price, qty, nap,
                p.league_raw, p.league, p.league_group, p.market_raw, p.market,
                p.source_raw, p.source, p.event_id, p.outcome_code, p.outcome_kind,
                p.line, fname))
            if p.parse_ok and not p.warning:
                n_ok += 1
            else:
                n_warn += 1
                warnings.append((rdate, symbol, p.n_segments, p.warning,
                                p.unparsed_tokens, fname))
    cur = conn.cursor()
    cur.execute("DELETE FROM time_and_sales WHERE source_file=?", (fname,))
    cur.executemany("INSERT INTO time_and_sales VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    cur.execute("DELETE FROM parse_warnings WHERE source_file=?", (fname,))
    cur.executemany("INSERT INTO parse_warnings VALUES (?,?,?,?,?,?)", warnings)
    _log_ingest(cur, fname, "time-and-sales", rdate, n_total, n_ok, n_warn, now_iso)
    conn.commit()
    return dict(file=fname, report_type="time-and-sales", rows_total=n_total,
                rows_ok=n_ok, rows_warned=n_warn, business_date=rdate)


LOADERS = {
    "daily-market": load_market_file,
    "daily-settlement": load_settlement_file,
    "time-and-sales": load_timesales_file,
}


def _log_ingest(cur, fname, rtype, bdate, n_total, n_ok, n_warn, now_iso):
    cur.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?,?)",
                (fname, rtype, bdate, n_total, n_ok, n_warn, now_iso))


def _empty_stats(fname, rtype):
    return dict(file=fname, report_type=rtype, rows_total=0, rows_ok=0,
                rows_warned=0, business_date=None, skipped_empty=True)


# ------------------------- verified names -------------------------------------
_SPREAD_RE = re.compile(r"\s*[-+]\d+(\.\d+)?\s*$")  # trailing run-line like ' -4.5'


def build_verified_names(conn) -> int:
    """Harvest clean entity names from settlement Market Names.

    Preference order per (league, outcome_code):
      1. WIN / WTRN ("who wins") -- Market Name IS the entity ('LA Dodgers').
      2. MOVY -- strip the trailing run-line ('LA Dodgers -1.5' -> 'LA Dodgers').
    Rebuilt from scratch each run (idempotent).
    """
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT league_raw, market_raw, outcome_code, market_name "
        "FROM settlement_reports "
        "WHERE outcome_code IS NOT NULL AND market_name IS NOT NULL").fetchall()
    primary, fallback = {}, {}
    for league, market, code, name in rows:
        name = (name or "").strip()
        if not name or not code:
            continue
        key = (league, code)
        if market in ("WIN", "WTRN"):
            primary[key] = (name, market)
        elif market == "MOVY":
            fallback.setdefault(key, (_SPREAD_RE.sub("", name).strip(), market))
    merged = dict(fallback)
    merged.update(primary)  # primary wins
    cur.execute("DELETE FROM verified_names")
    cur.executemany(
        "INSERT OR REPLACE INTO verified_names VALUES (?,?,?,?)",
        [(lg, code, nm, mkt) for (lg, code), (nm, mkt) in merged.items()])
    conn.commit()
    return len(merged)


# ------------------------------- driver ---------------------------------------
def discover_files(raw_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(raw_dir, "*.csv")))


def run(raw_dir=RAW_DIR, db=DEFAULT_DB, files=None, force=False) -> list[dict]:
    conn = store.connect(db)
    now_iso = datetime.now(timezone.utc).isoformat()
    targets = files or discover_files(raw_dir)
    if not targets:
        print(f"[load] no CSV files found in {raw_dir}")
        conn.close()
        return []

    done = set()
    if not force:
        done = {r[0] for r in conn.execute("SELECT source_file FROM ingest_log")}

    results = []
    for path in targets:
        fname = os.path.basename(path)
        if not force and fname in done:
            print(f"[load] skip (already ingested): {fname}")
            continue
        rtype = classify(fname)
        stats = LOADERS[rtype](conn, path, fname, now_iso)
        results.append(stats)
        print(f"[load] {rtype:16s} {fname}: date={stats['business_date']} "
              f"rows={stats['rows_total']} ok={stats['rows_ok']} "
              f"warned={stats['rows_warned']}")

    n_names = build_verified_names(conn)
    print(f"[load] verified_names rebuilt: {n_names} entity name(s) "
          "from settlement reports.")
    conn.close()
    return results


def main():
    ap = argparse.ArgumentParser(description="Load Railbird CSVs into SQLite.")
    ap.add_argument("--raw-dir", default=RAW_DIR)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--files", nargs="*", help="specific CSV files to load")
    ap.add_argument("--force", action="store_true",
                    help="re-load even files already in ingest_log")
    args = ap.parse_args()
    run(raw_dir=args.raw_dir, db=args.db, files=args.files, force=args.force)


if __name__ == "__main__":
    main()
