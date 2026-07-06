"""Query helpers between the SQLite store and the Streamlit dashboard.

The decoded table is small enough (a few million rows even after a year of
files) to load into a single pandas DataFrame and filter in-memory, which keeps
the dashboard code simple and fast. Callers are expected to cache
``load_decoded`` (the Streamlit app does, keyed on the DB file mtime).

Every returned frame keeps units explicit: contract counts vs. the two dollar
ESTIMATES (notional-at-price, max-notional).
"""

from __future__ import annotations

import os
import sqlite3

import pandas as pd

from . import DEFAULT_DB

# Canonical column labels used throughout the UI so units are never ambiguous.
CONTRACTS = "Contracts Traded"
NOTIONAL = "Notional @ Price ($, est.)"
MAX_NOTIONAL = "Max Notional ($, est. ceiling)"
OI = "Open Interest (contracts)"


def load_decoded(db_path: str = DEFAULT_DB) -> pd.DataFrame:
    """Return the full decoded table as a typed DataFrame (empty if no DB)."""
    if not os.path.exists(db_path):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query("SELECT * FROM decoded_reports", conn)
    finally:
        conn.close()
    if df.empty:
        return df
    df["business_date"] = pd.to_datetime(df["business_date"])
    for c in ("open_interest", "trade_volume", "n_segments"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype("int64")
    for c in ("high_price", "low_price", "settlement_price", "line",
              "notional_at_price", "max_notional"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["traded"] = df["trade_volume"] > 0
    return df


def load_table(db_path: str, name: str) -> pd.DataFrame:
    if not os.path.exists(db_path):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(f"SELECT * FROM {name}", conn)
    finally:
        conn.close()


def db_mtime(db_path: str = DEFAULT_DB) -> float:
    return os.path.getmtime(db_path) if os.path.exists(db_path) else 0.0


def apply_filters(df: pd.DataFrame, start=None, end=None, leagues=None,
                  markets=None, sources=None, statuses=None) -> pd.DataFrame:
    """Filter the decoded frame by the dashboard's global controls."""
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if start is not None:
        m &= df["business_date"] >= pd.Timestamp(start)
    if end is not None:
        m &= df["business_date"] <= pd.Timestamp(end)
    if leagues:
        m &= df["league_raw"].isin(leagues)
    if markets:
        m &= df["market_raw"].isin(markets)
    if sources:
        m &= df["source_raw"].isin(sources)
    if statuses:
        m &= df["status"].isin(statuses)
    return df[m]


def kpis(df: pd.DataFrame) -> dict:
    """Headline metrics for the (already filtered) frame."""
    if df.empty:
        return dict(contracts=0, notional=0.0, max_notional=0.0,
                    distinct_traded=0, distinct_events=0, open_interest=0,
                    leagues_active=0, listed=0)
    traded = df[df["traded"]]
    return dict(
        contracts=int(df["trade_volume"].sum()),
        notional=float(df["notional_at_price"].sum()),
        max_notional=float(df["max_notional"].sum()),
        distinct_traded=int((df["traded"]).sum()),
        # "events" is APPROXIMATE: event_id can differ per market for one game.
        distinct_events=int(traded["event_id"].nunique()),
        open_interest=int(df["open_interest"].sum()),
        leagues_active=int(traded["league_raw"].nunique()),
        listed=int(len(df)),
    )


def _rollup_freq(freq: str) -> str:
    return {"Daily": "D", "Weekly": "W", "Monthly": "MS"}.get(freq, "D")


def time_series(df: pd.DataFrame, freq: str = "Daily") -> pd.DataFrame:
    """Per-period totals: contracts, both dollar estimates, OI, listed vs traded."""
    if df.empty:
        return pd.DataFrame()
    g = df.set_index("business_date").groupby(pd.Grouper(freq=_rollup_freq(freq)))
    out = g.agg(
        contracts=("trade_volume", "sum"),
        notional=("notional_at_price", "sum"),
        max_notional=("max_notional", "sum"),
        open_interest=("open_interest", "sum"),
        listed=("symbol", "size"),
        traded=("traded", "sum"),
    ).reset_index()
    return out


# Measures selectable in the Mix view: (value column, clean label, is_dollars).
MEASURES = {
    "Contracts traded": ("trade_volume", False),
    "Notional @ price ($, est.)": ("notional_at_price", True),
    "Max notional ($, est.)": ("max_notional", True),
}


def mix_over_time(df: pd.DataFrame, dim: str, freq: str = "Daily",
                  value_col: str = "trade_volume") -> pd.DataFrame:
    """Chosen measure by a dimension over time (long form for stacked charts).

    Returns columns [business_date, <dim>, value]. `value_col` is one of
    trade_volume / notional_at_price / max_notional (see MEASURES)."""
    if df.empty:
        return pd.DataFrame()
    g = (df.set_index("business_date")
           .groupby([pd.Grouper(freq=_rollup_freq(freq)), dim])[value_col]
           .sum().reset_index())
    return g.rename(columns={value_col: "value"})


def top_events(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    t = df[df["traded"]]
    if t.empty:
        return pd.DataFrame()
    g = t.groupby(["league_raw", "event_id"]).agg(
        contracts=("trade_volume", "sum"),
        notional=("notional_at_price", "sum"),
        market_types=("market_raw", lambda s: ", ".join(sorted(set(s)))),
        contracts_listed=("symbol", "size"),
    ).reset_index().sort_values("contracts", ascending=False).head(n)
    return g


def top_contracts(df: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    cols = ["symbol", "league_raw", "market", "source", "outcome_name",
            "line", "trade_volume", "notional_at_price", "settlement_price",
            "status", "business_date"]
    return (df[df["traded"]].sort_values("trade_volume", ascending=False)
            .head(n)[cols].rename(columns={"trade_volume": "contracts"}))


def volume_by_outcome(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Contracts by team (kind='team') or player (kind='player')."""
    if df.empty:
        return pd.DataFrame()
    sub = df[(df["traded"]) & (df["outcome_kind"] == kind)]
    if sub.empty:
        return pd.DataFrame()
    # Prefer the settlement-verified label when it's been attached.
    base = sub["outcome_label"] if "outcome_label" in sub.columns else sub["outcome_name"]
    label = base.fillna(sub["outcome_code"])
    g = (sub.assign(_label=label)
            .groupby(["league_raw", "_label"])["trade_volume"].sum()
            .reset_index().rename(columns={"_label": "outcome",
                                           "trade_volume": "contracts"}))
    return g.sort_values("contracts", ascending=False)


def line_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Volume across parsed threshold lines (TRUNS/MOVY)."""
    if df.empty:
        return pd.DataFrame()
    sub = df[(df["traded"]) & (df["line"].notna())]
    if sub.empty:
        return pd.DataFrame()
    g = (sub.groupby(["market_raw", "line"])["trade_volume"].sum()
            .reset_index().rename(columns={"trade_volume": "contracts"}))
    return g.sort_values(["market_raw", "line"])


def concentration(df: pd.DataFrame) -> pd.DataFrame:
    """Cumulative share of volume by rank (for a concentration curve)."""
    if df.empty:
        return pd.DataFrame()
    v = df.loc[df["traded"], "trade_volume"].sort_values(ascending=False).values
    if len(v) == 0:
        return pd.DataFrame()
    total = v.sum()
    cum = v.cumsum() / total
    return pd.DataFrame({"rank": range(1, len(v) + 1), "cum_share": cum})


def hhi(df: pd.DataFrame) -> float:
    """Herfindahl-Hirschman index of volume concentration across contracts."""
    if df.empty:
        return float("nan")
    v = df.loc[df["traded"], "trade_volume"].astype(float)
    total = v.sum()
    if total <= 0:
        return float("nan")
    shares = v / total
    return float((shares ** 2).sum())


def settlement_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Among SETTLED contracts, split volume by settle price ($1 YES vs $0 NO)."""
    if df.empty:
        return pd.DataFrame()
    sub = df[(df["status"] == "Settled") & (df["settlement_price"].notna())]
    if sub.empty:
        return pd.DataFrame()
    def bucket(p):
        if p >= 0.99:
            return "$1.00 (YES resolved)"
        if p <= 0.01:
            return "$0.00 (NO resolved)"
        return "other"
    g = (sub.assign(bucket=sub["settlement_price"].map(bucket))
            .groupby("bucket")["trade_volume"].sum().reset_index()
            .rename(columns={"trade_volume": "contracts"}))
    return g


def new_sport_detector(df: pd.DataFrame) -> pd.DataFrame:
    """First business date each LEAGUE appears in the full dataset (unfiltered)."""
    if df.empty:
        return pd.DataFrame()
    g = (df.groupby("league_raw")["business_date"].min().reset_index()
           .rename(columns={"business_date": "first_seen"})
           .sort_values("first_seen"))
    g["known_label"] = g["league_raw"].map(
        lambda c: c in _known_leagues())
    return g


def novelty_report(df: pd.DataFrame) -> dict:
    """First-seen date for each league/market/source code, flagging unknowns."""
    if df.empty:
        return {}
    out = {}
    for dim in ("league_raw", "market_raw", "source_raw"):
        g = (df.groupby(dim)["business_date"].min().reset_index()
               .rename(columns={"business_date": "first_seen"})
               .sort_values("first_seen"))
        out[dim] = g
    return out


def _known_leagues():
    from .reference import LEAGUE_NAMES
    return set(LEAGUE_NAMES)


# =============================================================================
# Verified names (harvested from the Daily Settlement Report's Market Name)
# =============================================================================
def verified_name_map(db_path: str = DEFAULT_DB) -> dict:
    """{(league_raw, outcome_code): name} of settlement-verified entity names."""
    vn = load_table(db_path, "verified_names")
    if vn.empty:
        return {}
    return {(r.league_raw, r.outcome_code): r.name for r in vn.itertuples()}


def attach_verified(df: pd.DataFrame, vmap: dict) -> pd.DataFrame:
    """Add `outcome_label` (verified > heuristic > raw code) and `name_verified`."""
    if df.empty:
        return df
    df = df.copy()
    keys = list(zip(df["league_raw"], df["outcome_code"]))
    verified = pd.Series([vmap.get(k) for k in keys], index=df.index)
    df["name_verified"] = verified.notna()
    heuristic = df["outcome_name"] if "outcome_name" in df.columns else pd.NA
    df["outcome_label"] = verified.fillna(heuristic).fillna(df["outcome_code"])
    return df


# =============================================================================
# Daily Settlement Report
# =============================================================================
def load_settlement(db_path: str = DEFAULT_DB) -> pd.DataFrame:
    df = load_table(db_path, "settlement_reports")
    if df.empty:
        return df
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["settlement_ts"] = pd.to_datetime(df["settlement_ts"], errors="coerce")
    df["settlement_price"] = pd.to_numeric(df["settlement_price"], errors="coerce")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    return df


def filter_settlement(df, start=None, end=None, leagues=None, markets=None,
                      sources=None) -> pd.DataFrame:
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if start is not None:
        m &= df["report_date"] >= pd.Timestamp(start)
    if end is not None:
        m &= df["report_date"] <= pd.Timestamp(end)
    if leagues:
        m &= df["league_raw"].isin(leagues)
    if markets:
        m &= df["market_raw"].isin(markets)
    if sources:
        m &= df["source_raw"].isin(sources)
    return df[m]


def settlement_outcome_split(df: pd.DataFrame) -> pd.DataFrame:
    """Count of settled contracts by resolved price ($1 YES / $0 NO / $0.5 push)."""
    if df.empty:
        return pd.DataFrame()
    def bucket(p):
        if pd.isna(p):
            return "unknown"
        if p >= 0.99:
            return "$1.00 (YES resolved)"
        if p <= 0.01:
            return "$0.00 (NO resolved)"
        return "$0.50 (push / void)"
    g = (df.assign(bucket=df["settlement_price"].map(bucket))
           .groupby("bucket").size().reset_index(name="contracts"))
    return g


def settlement_by_dim(df: pd.DataFrame, dim: str) -> pd.DataFrame:
    """Settled contract counts by a dimension, split by resolved bucket."""
    if df.empty:
        return pd.DataFrame()
    def bucket(p):
        if pd.isna(p):
            return "unknown"
        if p >= 0.99:
            return "YES ($1)"
        if p <= 0.01:
            return "NO ($0)"
        return "push ($0.5)"
    g = (df.assign(bucket=df["settlement_price"].map(bucket))
           .groupby([dim, "bucket"]).size().reset_index(name="contracts"))
    return g


# =============================================================================
# Time & Sales Report (the trade tape)
# =============================================================================
def load_timesales(db_path: str = DEFAULT_DB) -> pd.DataFrame:
    df = load_table(db_path, "time_and_sales")
    if df.empty:
        return df
    df["report_date"] = pd.to_datetime(df["report_date"])
    df["transaction_ts"] = pd.to_datetime(df["transaction_ts"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0).astype("int64")
    df["notional_at_price"] = pd.to_numeric(df["notional_at_price"], errors="coerce")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    return df


def filter_timesales(df, start=None, end=None, leagues=None, markets=None,
                     sources=None) -> pd.DataFrame:
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if start is not None:
        m &= df["report_date"] >= pd.Timestamp(start)
    if end is not None:
        m &= df["report_date"] <= pd.Timestamp(end)
    if leagues:
        m &= df["league_raw"].isin(leagues)
    if markets:
        m &= df["market_raw"].isin(markets)
    if sources:
        m &= df["source_raw"].isin(sources)
    return df[m]


def ts_kpis(df: pd.DataFrame) -> dict:
    if df.empty:
        return dict(trades=0, contracts=0, vwap=float("nan"), avg_size=0.0,
                    distinct_symbols=0, notional=0.0)
    contracts = int(df["quantity"].sum())
    notional = float((df["price"] * df["quantity"]).sum())
    return dict(
        trades=int(len(df)),
        contracts=contracts,
        vwap=(notional / contracts) if contracts else float("nan"),
        avg_size=float(df["quantity"].mean()),
        distinct_symbols=int(df["symbol"].nunique()),
        notional=notional,
    )


def ts_trades_over_time(df: pd.DataFrame, freq: str = "Daily") -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    g = df.set_index("report_date").groupby(pd.Grouper(freq=_rollup_freq(freq)))
    return g.agg(trades=("symbol", "size"), contracts=("quantity", "sum"),
                 notional=("notional_at_price", "sum")).reset_index()


def ts_by_hour(df: pd.DataFrame) -> pd.DataFrame:
    """Activity by ET hour-of-day (from transaction timestamps)."""
    if df.empty or df["transaction_ts"].isna().all():
        return pd.DataFrame()
    d = df.dropna(subset=["transaction_ts"]).copy()
    d["hour"] = d["transaction_ts"].dt.hour
    g = d.groupby("hour").agg(trades=("symbol", "size"),
                              contracts=("quantity", "sum")).reset_index()
    return g


def ts_vwap_by_contract(df: pd.DataFrame, n: int = 25) -> pd.DataFrame:
    """Per-symbol VWAP + price range + first/last price, top-N by contracts."""
    if df.empty:
        return pd.DataFrame()
    d = df.sort_values("transaction_ts")
    def agg(g):
        qty = g["quantity"].sum()
        vwap = (g["price"] * g["quantity"]).sum() / qty if qty else float("nan")
        return pd.Series({
            "trades": len(g), "contracts": int(qty), "vwap": vwap,
            "min_price": g["price"].min(), "max_price": g["price"].max(),
            "first_price": g["price"].iloc[0], "last_price": g["price"].iloc[-1],
            "league": g["league_raw"].iloc[0], "market": g["market"].iloc[0],
        })
    out = (d.groupby("symbol").apply(agg, include_groups=False).reset_index()
             .sort_values("contracts", ascending=False).head(n))
    return out


def ts_price_path(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Time-ordered trades for one symbol (for an intraday price chart)."""
    if df.empty:
        return pd.DataFrame()
    d = df[df["symbol"] == symbol].dropna(subset=["transaction_ts"])
    return d.sort_values("transaction_ts")[
        ["transaction_ts", "price", "quantity", "report_date"]]


def ts_top_symbols(df: pd.DataFrame, n: int = 30) -> list[str]:
    if df.empty:
        return []
    return (df.groupby("symbol")["quantity"].sum()
              .sort_values(ascending=False).head(n).index.tolist())


def ts_trade_size_dist(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="int64")
    return df["quantity"]
