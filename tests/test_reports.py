"""Tests for the settlement + time-and-sales loaders and verified-name builder.

Uses small synthetic CSVs in a temp dir + a temp SQLite DB, so the loaders are
exercised end-to-end deterministically (no network, no reliance on real files).
"""

import pytest

from dkex import store, load
from dkex.load import (classify, file_date_from_name, parse_et_timestamp,
                       build_verified_names)


# --- pure helpers -------------------------------------------------------------

def test_classify_by_filename():
    assert classify("Daily_Market_Report_-__2026_07_05_.csv") == "daily-market"
    assert classify("Daily_Settlement_Report_-__2026_07_05_.csv") == "daily-settlement"
    assert classify("Time_and_Sales_Report_-__2026_07_05_.csv") == "time-and-sales"
    # the live server name for T&S uses '&' — still classified correctly
    assert classify("Time & Sales Report - (2026.07.05).csv") == "time-and-sales"


def test_file_date_from_name():
    assert file_date_from_name("Daily_Settlement_Report_-__2026_07_05_.csv") == "2026-07-05"
    assert file_date_from_name("no_date_here.csv") is None


def test_et_timestamp_subsecond_and_minute():
    # settlement (sub-second) and time-and-sales (minute) both parse; tz dropped
    assert parse_et_timestamp("07/05/26 12:20:29.156 AM EDT") == "2026-07-05T00:20:29.156000"
    assert parse_et_timestamp("07/05/26 12:08 AM EDT") == "2026-07-05T00:08:00"
    assert parse_et_timestamp("") is None


# --- end-to-end loaders on synthetic files ------------------------------------

SETTLE_CSV = """Market Name,Ticker,Status,Date and Time of Settlement (ET),Price (USD)
LA Dodgers,MLB-WIN-FG-0146ABCDEFGHIJK-LARS000,Settled Early,07/05/26 09:30:00.100 PM EDT,$1.00
ARI Diamondbacks -4.5,MLB-MOVY-FG-0146ABCDEFGHIJK-ARKS000-GTE-P00045,Settled Early,07/05/26 09:30:01.200 PM EDT,$0.00
Over 8.5,MLB-TRUNS-FG-0146ABCDEFGHIJK-GTE-P00085,Settled Early,07/05/26 09:30:02.300 PM EDT,$1.00
NYY Yankees,MLB-WIN-IT5-0146ZZZZZZZZZZZ-NYES000,Settled Early,07/05/26 09:31:00.000 PM EDT,$0.50
"""

TAS_CSV = """Business Date,Symbol,Transaction Date and Time,Last Price (USD),Last Quantity
20260706,MLB-WIN-FG-0146ABCDEFGHIJK-LARS000,07/05/26 07:15 PM EDT,$0.60,10
20260706,MLB-WIN-FG-0146ABCDEFGHIJK-LARS000,07/05/26 07:20 PM EDT,$0.65,20
20260706,MLB-TRUNS-FG-0146ABCDEFGHIJK-GTE-P00085,07/05/26 07:25 PM EDT,$0.40,5
"""


@pytest.fixture()
def loaded_db(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Daily_Settlement_Report_-__2026_07_05_.csv").write_text(SETTLE_CSV)
    (raw / "Time_and_Sales_Report_-__2026_07_05_.csv").write_text(TAS_CSV)
    db = str(tmp_path / "test.sqlite")
    load.run(raw_dir=str(raw), db=db)
    return db


def test_settlement_loaded_with_decoded_fields(loaded_db):
    conn = store.connect(loaded_db)
    rows = conn.execute(
        "SELECT symbol, settlement_price, market_raw, line FROM settlement_reports "
        "ORDER BY symbol").fetchall()
    assert len(rows) == 4
    by_sym = {r[0]: r for r in rows}
    truns = by_sym["MLB-TRUNS-FG-0146ABCDEFGHIJK-GTE-P00085"]
    assert truns[1] == 1.0 and truns[2] == "TRUNS" and truns[3] == 8.5
    # sub-second settlement timestamp parsed
    ts = conn.execute("SELECT settlement_ts FROM settlement_reports "
                      "WHERE symbol LIKE '%LARS000'").fetchone()[0]
    assert ts.startswith("2026-07-05T21:30:00")
    conn.close()


def test_timesales_idempotent_and_notional(loaded_db):
    conn = store.connect(loaded_db)
    n1 = conn.execute("SELECT COUNT(*) FROM time_and_sales").fetchone()[0]
    assert n1 == 3
    qty = conn.execute("SELECT SUM(quantity) FROM time_and_sales").fetchone()[0]
    assert qty == 35
    # price*qty notional: 0.6*10 + 0.65*20 + 0.4*5 = 21.0
    nap = conn.execute("SELECT ROUND(SUM(notional_at_price),2) FROM time_and_sales").fetchone()[0]
    assert nap == 21.0
    # report_date comes from the FILENAME (not the +1 internal Business Date)
    rdate = conn.execute("SELECT DISTINCT report_date FROM time_and_sales").fetchone()[0]
    assert rdate == "2026-07-05"
    braw = conn.execute("SELECT DISTINCT business_date_raw FROM time_and_sales").fetchone()[0]
    assert braw == "20260706"  # preserved verbatim, and intentionally != report_date
    conn.close()


def test_timesales_reload_force_no_duplicates(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "Time_and_Sales_Report_-__2026_07_05_.csv").write_text(TAS_CSV)
    db = str(tmp_path / "t.sqlite")
    load.run(raw_dir=str(raw), db=db)
    load.run(raw_dir=str(raw), db=db, force=True)  # reload same file
    conn = store.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM time_and_sales").fetchone()[0] == 3
    conn.close()


def test_verified_names_prefers_win_over_movy(loaded_db):
    conn = store.connect(loaded_db)
    names = dict(conn.execute(
        "SELECT outcome_code, name FROM verified_names WHERE league_raw='MLB'"))
    # WIN gives clean team names
    assert names["LARS000"] == "LA Dodgers"
    assert names["NYES000"] == "NYY Yankees"
    # MOVY name has its run-line stripped ('ARI Diamondbacks -4.5' -> team only)
    assert names["ARKS000"] == "ARI Diamondbacks"
    # TRUNS has no team outcome -> not a verified entity
    assert all(not c.startswith("GTE") for c in names)
    conn.close()


def test_ingest_log_records_report_type(loaded_db):
    conn = store.connect(loaded_db)
    types = dict(conn.execute(
        "SELECT report_type, COUNT(*) FROM ingest_log GROUP BY report_type"))
    assert types.get("daily-settlement") == 1
    assert types.get("time-and-sales") == 1
    conn.close()
