"""Unit tests for the DKEX symbol parser and CSV field coercion.

Run:  pytest -q   (from the project root, with PYTHONPATH=src)
The conftest.py in this dir puts src/ on the path.
"""

import os

import pytest

from dkex.symbols import parse_symbol
from dkex.load import parse_price, parse_int, parse_business_date, parse_maturity


# --- The exact strings from the objective, with asserted decoded fields -------

def test_mlb_win_it5_team_no_line():
    p = parse_symbol("MLB-WIN-IT5-0146NBSSL8SDU3G-NYTS000")
    assert p.league_raw == "MLB"
    assert p.market_raw == "WIN"
    assert p.source_raw == "IT5"
    assert p.event_id == "0146NBSSL8SDU3G"
    assert p.outcome_code == "NYTS000"
    assert p.outcome_kind == "team"
    assert p.line is None
    assert p.threshold_op is None
    assert p.parse_ok is True


def test_mlb_truns_line_no_team():
    p = parse_symbol("MLB-TRUNS-IT5-0145BO10L8S8G70-GTE-P00055")
    assert p.market_raw == "TRUNS"
    assert p.line == 5.5
    assert p.threshold_op == "GTE"
    assert p.outcome_code is None       # no team on total-runs contracts
    assert p.outcome_kind == "threshold"
    assert p.event_id == "0145BO10L8S8G70"


def test_kbo_movy_team_and_line():
    p = parse_symbol("KBO-MOVY-FG-00N8UBQBPIT76T0-DORS000-GTE-P00015")
    assert p.league_raw == "KBO"
    assert p.market_raw == "MOVY"
    assert p.source_raw == "FG"
    assert p.event_id == "00N8UBQBPIT76T0"
    assert p.outcome_code == "DORS000"  # team present alongside the line
    assert p.outcome_kind == "team"
    assert p.line == 1.5
    assert p.threshold_op == "GTE"


def test_pga_3bls_round_player():
    p = parse_symbol("PGA-3BLS-R1-00GPGBS0LP470000000-BRAN008")
    assert p.league_raw == "PGA"
    assert p.market_raw == "3BLS"
    assert p.source_raw == "R1"
    assert p.outcome_code == "BRAN008"
    assert p.outcome_kind == "player"
    assert p.line is None


def test_unknown_league_parses_and_is_flagged():
    # Synthetic future sport (World Cup). Must load fine AND be surfaced as new.
    p = parse_symbol("WCUP-WIN-FG-0146NBSSL8SDU3G-USAS000")
    assert p.league_raw == "WCUP"
    assert p.event_id == "0146NBSSL8SDU3G"
    assert p.outcome_code == "USAS000"
    # parse itself succeeds; the friendly-name warning marks the novel token
    assert p.parse_ok is True
    assert p.warning is not None and "WCUP" in p.warning


# --- Threshold line decoding across the observed range ------------------------

@pytest.mark.parametrize("code,expected", [
    ("P00005", 0.5), ("P00015", 1.5), ("P00055", 5.5),
    ("P00105", 10.5), ("P00155", 15.5),
])
def test_line_values(code, expected):
    p = parse_symbol(f"MLB-TRUNS-FG-0145ABCDEFGHIJK-GTE-{code}")
    assert p.line == expected


# --- Defensive / edge cases (must never raise) --------------------------------

def test_empty_and_garbage_symbols_do_not_crash():
    for bad in ["", "   ", "JUSTONE", "TWO-SEG", "MLB-WIN-IT5"]:
        p = parse_symbol(bad)
        assert p.symbol == (bad.strip() if isinstance(bad, str) else bad)
        # too-few-segments cases are flagged, not fatal
        if len(bad.split("-")) < 4:
            assert p.parse_ok is False


def test_friendly_names_present():
    p = parse_symbol("MLB-WIN-FG-0145ABCDEFGHIJK-LARS000")
    assert p.league == "MLB (Baseball)"
    assert p.market == "Moneyline (Who Wins)"
    assert p.source == "Full Game"
    assert p.league_group == "Baseball"
    assert p.outcome_name == "Los Angeles Dodgers"   # heuristic team map


def test_golf_group_is_golf():
    p = parse_symbol("PGA-WTRN-TRN-00GPGBS0LP470000000-TIGE000")
    assert p.league_group == "Golf"


# --- CSV field coercion -------------------------------------------------------

def test_price_parsing():
    assert parse_price("$1.00") == 1.0
    assert parse_price("$0.00") == 0.0
    assert parse_price("") is None
    assert parse_price(None) is None
    assert parse_price("$1,234.50") == 1234.5


def test_int_parsing():
    assert parse_int("1896") == 1896
    assert parse_int("") == 0
    assert parse_int(None) == 0
    assert parse_int("2,000") == 2000


def test_business_date_parsing():
    assert parse_business_date("20260705") == "2026-07-05"
    assert parse_business_date("2026-07-05") == "2026-07-05"
    assert parse_business_date("") is None


def test_maturity_parsing():
    assert parse_maturity("07/24/26 10:30 AM EDT") == "2026-07-24T10:30:00"
    assert parse_maturity("") is None


# --- Sanity check against the real sample file, if present --------------------

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "raw",
                      "Daily_Market_Report_-__2026_07_05_.csv")


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="sample CSV not present")
def test_real_sample_every_row_parses_without_error():
    import csv
    raised = 0
    total = 0
    with open(SAMPLE, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            total += 1
            try:
                parse_symbol(row["Symbol"])
            except Exception:  # noqa: BLE001
                raised += 1
    assert total > 1000            # the real file is ~16.7k rows
    assert raised == 0             # parser must never raise on real data
