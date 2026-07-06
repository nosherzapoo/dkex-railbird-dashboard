"""DKEX / Railbird Exchange -- Daily Market Report dashboard.

Run:  streamlit run app/dashboard.py

Correctness-first design notes:
  * Trade Volume is a COUNT OF CONTRACTS, never dollars. Every chart/table labels
    its unit. Two dollar figures are shown, both flagged as ESTIMATES:
      - Notional @ price = Σ(volume × settlement/last price)   (realistic proxy)
      - Max notional     = Σ(volume × $1)                      (absolute ceiling)
  * "Distinct events" is APPROXIMATE -- the event-id token can differ per market
    type for the same real-world game.
  * Team/player names are VERIFIED from the Daily Settlement Report's Market Name
    where available, falling back to a heuristic map, then the raw code.
  * Ingests all three report families: Daily Market (snapshot), Daily Settlement
    (outcomes + verified names), and Time & Sales (the intraday trade tape).
  * One y-axis per chart (contracts and dollars are never mixed on one axis).
  * League colors are fixed per entity so filtering never repaints series.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# --- make the src/ layout importable when run via `streamlit run app/...` -----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dkex import DEFAULT_DB, data as D  # noqa: E402
from dkex.reference import (  # noqa: E402
    LEAGUE_NAMES, MARKET_NAMES, SOURCE_NAMES,
)

st.set_page_config(page_title="DKEX / Railbird Market Reports",
                   layout="wide", page_icon="📊")

# Fixed categorical colors keyed on the ENTITY (league), so a filter that drops a
# series never recolors the survivors. Colorblind-aware set.
LEAGUE_COLORS = {
    "MLB": "#4C78A8", "KBO": "#F58518", "NPB": "#54A24B", "PGA": "#B279A2",
}
CONTRACT_COLOR = "#4C78A8"
NOTIONAL_COLOR = "#54A24B"
MAXNOT_COLOR = "#E45756"
OI_COLOR = "#B279A2"


# ----------------------------- data loading (cached) --------------------------
@st.cache_data(show_spinner=False)
def get_data(db_path: str, _mtime: float):
    """Cached frames + side tables. _mtime busts cache when DB changes."""
    vmap = D.verified_name_map(db_path)
    df = D.attach_verified(D.load_decoded(db_path), vmap)
    settle = D.load_settlement(db_path)
    tas = D.load_timesales(db_path)
    warns = D.load_table(db_path, "parse_warnings")
    ingest = D.load_table(db_path, "ingest_log")
    return df, settle, tas, warns, ingest, vmap


def money(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    x = float(x)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x / div:,.2f}{unit}"
    return f"${x:,.0f}"


def num(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{int(x):,}"


def dl_button(df: pd.DataFrame, label: str, fname: str, key: str):
    st.download_button(label, df.to_csv(index=False).encode(), fname,
                       "text/csv", key=key, use_container_width=False)


# Internal column -> clean, self-documenting header for downloaded CSVs. Units are
# baked into the names so an exported file is unambiguous away from the UI.
CLEAN_NAMES = {
    "business_date": "date", "report_date": "date",
    "contracts": "contracts_traded", "trade_volume": "contracts_traded",
    "value": "value", "notional": "notional_at_price_usd_est",
    "notional_at_price": "notional_at_price_usd_est",
    "max_notional": "max_notional_usd_est",
    "open_interest": "open_interest_contracts",
    "listed": "contracts_listed", "traded": "contracts_traded_distinct",
    "contracts_listed": "contracts_listed",
    "league_raw": "league", "league": "league", "market_raw": "market_type",
    "market": "market_type", "source_raw": "source_period",
    "league_group": "sport_group", "event_id": "event_id",
    "market_types": "market_types_involved", "outcome": "outcome",
    "outcome_label": "outcome_name", "outcome_name": "outcome_name",
    "line": "line", "rank": "rank_by_volume", "cum_share": "cumulative_share",
    "hour": "hour_of_day_et", "trades": "trades", "quantity": "contracts",
    "vwap": "vwap_usd", "min_price": "min_price_usd", "max_price": "max_price_usd",
    "first_price": "first_price_usd", "last_price": "last_price_usd",
    "price": "price_usd", "transaction_ts": "transaction_time_et",
    "settlement_price": "settlement_price_usd", "settlement_ts": "settlement_time_et",
    "bucket": "resolution", "market_name": "market_name", "symbol": "symbol",
    "status": "status",
}


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: CLEAN_NAMES.get(c, c) for c in df.columns})


def chart_csv(df: pd.DataFrame, fname: str, key: str,
              label: str = "⬇️ Download this chart's data (CSV)"):
    """Compact download button placed directly under a chart, with clean headers."""
    if df is None or getattr(df, "empty", True):
        return
    st.download_button(label, clean_df(df).to_csv(index=False).encode(), fname,
                       "text/csv", key=key)


# --- self-bootstrap: build the SQLite store from bundled CSVs if it's missing --
# Lets the app run on a fresh host (e.g. Streamlit Community Cloud) or a fresh
# clone with zero setup — the raw report CSVs are committed to the repo.
@st.cache_resource(show_spinner="Building local database from bundled reports…")
def ensure_db(db_path: str) -> str:
    import glob
    raw_dir = os.path.join(ROOT, "data", "raw")
    if not os.path.exists(db_path) and glob.glob(os.path.join(raw_dir, "*.csv")):
        from dkex import load
        load.run(raw_dir=raw_dir, db=db_path)
    return db_path


# ----------------------------- sidebar controls -------------------------------
st.sidebar.title("DKEX Market Reports")
db_path = st.sidebar.text_input("SQLite DB path", DEFAULT_DB)
ensure_db(db_path)

if not os.path.exists(db_path):
    st.error(f"No database at `{db_path}`, and no CSVs in `data/raw/` to build "
             "one from.\n\nFetch + load first:\n\n"
             "```bash\npython -m dkex.fetch\npython -m dkex.load\n```")
    st.stop()

df_all, settle_all, tas_all, warns, ingest, vmap = get_data(db_path, D.db_mtime(db_path))
if df_all.empty:
    st.warning("Database has no rows yet. Run `python -m dkex.load`.")
    st.stop()

min_d = df_all["business_date"].min().date()
max_d = df_all["business_date"].max().date()

st.sidebar.markdown("### Filters")
date_range = st.sidebar.date_input(
    "Business date range", value=(min_d, max_d),
    min_value=min_d, max_value=max_d)
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_d, end_d = date_range
else:
    start_d, end_d = min_d, max_d


def _opts(col):
    return sorted(df_all[col].dropna().unique().tolist())


sel_leagues = st.sidebar.multiselect(
    "League / sport", _opts("league_raw"),
    format_func=lambda c: LEAGUE_NAMES.get(c, c) + f"  ({c})")
sel_markets = st.sidebar.multiselect(
    "Market type", _opts("market_raw"),
    format_func=lambda c: MARKET_NAMES.get(c, c) + f"  ({c})")
sel_sources = st.sidebar.multiselect(
    "Source / period", _opts("source_raw"),
    format_func=lambda c: SOURCE_NAMES.get(c, c) + f"  ({c})")
sel_status = st.sidebar.multiselect("Status", _opts("status"))
freq = st.sidebar.radio("Time roll-up", ["Daily", "Weekly", "Monthly"],
                        horizontal=True)

st.sidebar.caption(
    "**Units:** *contracts* = count of binary contracts (settle $1/$0). "
    "*Notional* figures are **dollar estimates**, labeled as such.")
st.sidebar.caption(
    f"**Data layers:** Daily Market ({num(len(df_all))} rows) · "
    f"Settlement ({num(len(settle_all))}) · Time & Sales ({num(len(tas_all))} trades). "
    f"Team/player names verified from settlement: **{len(vmap)}** codes.")

df = D.apply_filters(df_all, start=start_d, end=end_d, leagues=sel_leagues,
                     markets=sel_markets, sources=sel_sources,
                     statuses=sel_status)

# ----------------------------- header + What's New ----------------------------
st.title("DKEX / Railbird Exchange — Daily Market Report Explorer")
st.caption(
    f"Coverage: **{min_d} → {max_d}** · {len(ingest)} file(s) ingested · "
    f"{num(len(df_all))} contract-rows. Railbird captures only the "
    "**regulated-exchange** layer of DraftKings Predictions — not DK's full "
    "consumer/market-making activity.")

# What's-new callout (new-sport detector) -- prominent, on the main page.
firsts = D.new_sport_detector(df_all)
if not firsts.empty:
    latest_file_date = df_all["business_date"].max()
    debut_today = firsts[firsts["first_seen"] == latest_file_date]
    unknown = firsts[~firsts["known_label"]]
    if not debut_today.empty or not unknown.empty:
        bits = []
        if not debut_today.empty:
            bits.append("**New league(s) debuting on the latest date:** " +
                        ", ".join(debut_today["league_raw"]))
        if not unknown.empty:
            bits.append("**Unrecognized league code(s) (no friendly name yet):** " +
                        ", ".join(unknown["league_raw"]))
        st.warning("🆕 **What's new** — " + "  ·  ".join(bits) +
                   "  · See the **New / Novelty** tab.")
    else:
        st.info("🆕 **What's new** — no new leagues since the earliest file. "
                "Watch this space for soccer/World Cup, NFL, NBA debuts. "
                "(Details in the **New / Novelty** tab.)")

# ----------------------------- KPIs -------------------------------------------
k = D.kpis(df)
st.markdown("### Headline (selected range)")
c = st.columns(4)
c[0].metric("Contracts traded", num(k["contracts"]))
c[1].metric("Notional @ price (est.)", money(k["notional"]))
c[2].metric("Max notional (est. ceiling)", money(k["max_notional"]))
c[3].metric("Total open interest", num(k["open_interest"]) + " contracts")
c = st.columns(4)
c[0].metric("Distinct traded contracts", num(k["distinct_traded"]))
c[1].metric("Distinct events (approx.)", num(k["distinct_events"]))
c[2].metric("Contracts listed", num(k["listed"]))
c[3].metric("Leagues active", num(k["leagues_active"]))
if k["listed"]:
    st.caption(f"**Breadth:** {k['distinct_traded']:,} of {k['listed']:,} listed "
               f"contracts traded ({100 * k['distinct_traded'] / k['listed']:.1f}%). "
               "Most listed contracts are future events with zero volume — expected.")

# ----------------------------- tabs -------------------------------------------
(tab_ts, tab_mix, tab_lb, tab_tas, tab_liq, tab_settle, tab_recon,
 tab_new, tab_dq, tab_export) = st.tabs([
    "📈 Time series", "🧩 Mix / composition", "🏆 Leaderboards",
    "⏱️ Time & Sales (intraday)", "💧 Liquidity & structure", "⚖️ Settlement",
    "🧮 Reconciliation", "🆕 New / Novelty", "🩺 Data quality", "⬇️ Export",
])

# ---- Time series -------------------------------------------------------------
with tab_ts:
    ts = D.time_series(df, freq)
    if ts.empty:
        st.info("No data for the current filters.")
    else:
        st.subheader("Contracts traded over time")
        st.caption("Unit: **contracts** (count).")
        fig = px.bar(ts, x="business_date", y="contracts",
                     labels={"contracts": "Contracts", "business_date": ""})
        fig.update_traces(marker_color=CONTRACT_COLOR)
        st.plotly_chart(fig, use_container_width=True)
        chart_csv(ts[["business_date", "contracts"]],
                  "dkex_contracts_over_time.csv", "dl_ts_contracts")

        st.subheader("Dollar estimates over time")
        st.caption("Unit: **US dollars — estimates only.** "
                   "Notional@price = Σ(volume×last price); "
                   "Max notional = Σ(volume×$1) ceiling. Same axis (both are $).")
        fig = go.Figure()
        fig.add_bar(x=ts["business_date"], y=ts["max_notional"],
                    name="Max notional ($, ceiling est.)", marker_color=MAXNOT_COLOR,
                    opacity=0.45)
        fig.add_trace(go.Scatter(
            x=ts["business_date"], y=ts["notional"], name="Notional @ price ($, est.)",
            mode="lines+markers", line=dict(color=NOTIONAL_COLOR, width=2)))
        fig.update_layout(yaxis_title="US$ (estimate)", xaxis_title="",
                          legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)
        chart_csv(ts[["business_date", "notional", "max_notional"]],
                  "dkex_dollars_over_time.csv", "dl_ts_dollars")

        cc = st.columns(2)
        with cc[0]:
            st.subheader("Open interest over time")
            st.caption("Unit: **contracts**.")
            fig = px.area(ts, x="business_date", y="open_interest",
                          labels={"open_interest": "Open interest (contracts)",
                                  "business_date": ""})
            fig.update_traces(line_color=OI_COLOR, fillcolor="rgba(178,121,162,.25)")
            st.plotly_chart(fig, use_container_width=True)
            chart_csv(ts[["business_date", "open_interest"]],
                      "dkex_open_interest_over_time.csv", "dl_ts_oi")
        with cc[1]:
            st.subheader("Listed vs. traded contracts")
            st.caption("Breadth / liquidity read. Unit: **contracts**.")
            fig = go.Figure()
            fig.add_bar(x=ts["business_date"], y=ts["listed"], name="Listed",
                        marker_color="#BAB0AC")
            fig.add_bar(x=ts["business_date"], y=ts["traded"], name="Traded (distinct)",
                        marker_color=CONTRACT_COLOR)
            fig.update_layout(barmode="overlay", yaxis_title="Contracts",
                              legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)
            chart_csv(ts[["business_date", "listed", "traded"]],
                      "dkex_listed_vs_traded.csv", "dl_ts_breadth")
        dl_button(ts, "⬇️ Full time-series table CSV", "dkex_timeseries.csv", "dl_ts")

# ---- Mix / composition -------------------------------------------------------
with tab_mix:
    st.caption("Composition over time. Pick the **measure** — contracts *or* a "
               "dollar estimate — and the breakdown dimension.")
    cc = st.columns([2, 2, 1])
    measure_label = cc[0].selectbox("Measure", list(D.MEASURES))
    value_col, is_dollars = D.MEASURES[measure_label]
    dim_map = {"League / sport": "league_raw", "Market type": "market_raw",
               "Source / period": "source_raw", "Baseball vs Golf": "league_group"}
    dim_label = cc[1].selectbox("Break down by", list(dim_map))
    dim = dim_map[dim_label]
    as_share = cc[2].checkbox("100% share", value=False)
    unit_word = "US$ (est.)" if is_dollars else "Contracts"

    mix = D.mix_over_time(df, dim, freq, value_col=value_col)
    if mix.empty or mix["value"].sum() == 0:
        st.info("No traded volume for the current filters.")
    else:
        color_map = LEAGUE_COLORS if dim == "league_raw" else None
        st.caption(f"Showing **{measure_label}**"
                   + (" — a dollar **estimate**." if is_dollars else " (a count)."))
        fig = px.area(mix, x="business_date", y="value", color=dim,
                      groupnorm="fraction" if as_share else None,
                      color_discrete_map=color_map,
                      labels={"value": unit_word, "business_date": "", dim: dim_label})
        fig.update_layout(yaxis_tickformat=".0%" if as_share else None,
                          yaxis_title="Share" if as_share else unit_word)
        st.plotly_chart(fig, use_container_width=True)
        # Download reflects the active measure; column renamed so it's self-explaining.
        mix_dl = mix.rename(columns={"value": value_col})
        chart_csv(mix_dl, f"dkex_mix_{dim}_{value_col}.csv", "dl_mix_ts")

        st.subheader(f"Point-in-time totals — {measure_label}")
        bar = (mix.groupby(dim)["value"].sum().reset_index()
                  .sort_values("value", ascending=False))
        fig2 = px.bar(bar, x=dim, y="value", color=dim,
                      color_discrete_map=color_map,
                      labels={"value": unit_word, dim: dim_label})
        fig2.update_layout(showlegend=False, yaxis_title=unit_word)
        st.plotly_chart(fig2, use_container_width=True)
        chart_csv(bar.rename(columns={"value": value_col}),
                  f"dkex_mix_totals_{dim}_{value_col}.csv", "dl_mix_bar")

# ---- Leaderboards ------------------------------------------------------------
with tab_lb:
    st.caption("Rankings for the selected range. Unit noted per table.")
    n = st.slider("Rows to show", 5, 50, 20, key="lb_n")

    st.subheader("Top events by contracts traded")
    st.caption("An 'event' groups contracts by (league, event-id). "
               "**Approximate** — event-id can differ per market for one game.")
    te = D.top_events(df, n)
    if te.empty:
        st.info("No traded events for filters.")
    else:
        te_disp = te.rename(columns={
            "league_raw": "League", "event_id": "Event ID", "contracts": "Contracts",
            "notional": "Notional@price ($ est.)", "market_types": "Markets",
            "contracts_listed": "Contracts listed"})
        st.dataframe(te_disp, use_container_width=True, hide_index=True)
        dl_button(te, "⬇️ Top events CSV", "dkex_top_events.csv", "dl_te")

    st.subheader("Top individual contracts by volume")
    st.caption("Unit: **contracts**.")
    tc = D.top_contracts(df, n)
    if not tc.empty:
        st.dataframe(tc, use_container_width=True, hide_index=True)
        dl_button(tc, "⬇️ Top contracts CSV", "dkex_top_contracts.csv", "dl_tc")

    cc = st.columns(2)
    with cc[0]:
        st.subheader("Volume by team (baseball)")
        st.caption(f"Team names are **verified** from the settlement report where "
                   f"available ({len(vmap)} codes), else heuristic. Unit: "
                   "**contracts**.")
        vt = D.volume_by_outcome(df, "team")
        if vt.empty:
            st.info("No traded team markets for filters.")
        else:
            fig = px.bar(vt.head(n).sort_values("contracts"),
                         x="contracts", y="outcome", color="league_raw",
                         orientation="h", color_discrete_map=LEAGUE_COLORS,
                         labels={"contracts": "Contracts", "outcome": "",
                                 "league_raw": "League"})
            st.plotly_chart(fig, use_container_width=True)
            dl_button(vt, "⬇️ By-team CSV", "dkex_by_team.csv", "dl_vt")
    with cc[1]:
        st.subheader("Volume by player (golf)")
        st.caption("Player codes shown raw. Unit: **contracts**.")
        vp = D.volume_by_outcome(df, "player")
        if vp.empty:
            st.info("No traded golf markets for filters.")
        else:
            fig = px.bar(vp.head(n).sort_values("contracts"),
                         x="contracts", y="outcome", orientation="h",
                         labels={"contracts": "Contracts", "outcome": ""})
            fig.update_traces(marker_color=LEAGUE_COLORS["PGA"])
            st.plotly_chart(fig, use_container_width=True)
            dl_button(vp, "⬇️ By-player CSV", "dkex_by_player.csv", "dl_vp")

    st.subheader("Volume by line — TRUNS / MOVY thresholds")
    st.caption("Which totals / run-line values get the most action. "
               "Unit: **contracts**.")
    ld = D.line_distribution(df)
    if ld.empty:
        st.info("No traded threshold markets for filters.")
    else:
        fig = px.bar(ld, x="line", y="contracts", color="market_raw", barmode="group",
                     labels={"line": "Line (≥)", "contracts": "Contracts",
                             "market_raw": "Market"})
        st.plotly_chart(fig, use_container_width=True)
        dl_button(ld, "⬇️ Line distribution CSV", "dkex_lines.csv", "dl_ld")

# ---- Time & Sales (intraday trade tape) --------------------------------------
with tab_tas:
    st.subheader("Time & Sales — the trade tape")
    st.caption(
        "Every individual execution: timestamp, **traded price**, and quantity. "
        "Unlike the daily snapshot, prices here are **intraday ($0.01–$0.99) = the "
        "market's implied probability** at the moment of the trade. Respects the "
        "league/market/source and date filters (status doesn't apply).")
    tas = D.filter_timesales(tas_all, start=start_d, end=end_d, leagues=sel_leagues,
                             markets=sel_markets, sources=sel_sources)
    if tas.empty:
        st.info("No trades for the current filters. "
                "(Golf, for instance, had no trades in this window.)")
    else:
        tk = D.ts_kpis(tas)
        cc = st.columns(5)
        cc[0].metric("Trades (executions)", num(tk["trades"]))
        cc[1].metric("Contracts", num(tk["contracts"]))
        cc[2].metric("VWAP (¢, implied prob.)", f"${tk['vwap']:.3f}")
        cc[3].metric("Avg trade size", f"{tk['avg_size']:.1f}")
        cc[4].metric("Notional @ price (est.)", money(tk["notional"]))
        st.caption("VWAP = Σ(price×qty)/Σ(qty); a $0.48 VWAP ≈ a 48% average "
                   "implied probability across traded contracts.")

        st.markdown("#### Intraday price path for one contract")
        top_syms = D.ts_top_symbols(tas, 40)
        def _fmt_sym(s):
            hit = tas[tas["symbol"] == s]
            if hit.empty:            # stale selection after a filter change
                return s
            row = hit.iloc[0]
            lbl = vmap.get((row["league_raw"], row["outcome_code"])) or \
                row["outcome_code"] or ""
            ln = f" {row['line']:g}" if pd.notna(row["line"]) else ""
            return f"{s}  —  {row['market']} {lbl}{ln}"
        sym = st.selectbox("Contract (top-traded first)", top_syms,
                           format_func=_fmt_sym)
        path = D.ts_price_path(tas, sym)
        if not path.empty:
            fig = px.scatter(path, x="transaction_ts", y="price", size="quantity",
                             labels={"transaction_ts": "Trade time (ET)",
                                     "price": "Traded price ($ = implied prob.)",
                                     "quantity": "Contracts"})
            fig.add_trace(go.Scatter(x=path["transaction_ts"], y=path["price"],
                                     mode="lines", line=dict(color=CONTRACT_COLOR,
                                     width=1), showlegend=False, hoverinfo="skip"))
            fig.update_yaxes(range=[0, 1], tickformat="$.2f")
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"{len(path)} trade(s). Marker size = contracts per fill. "
                       "Y-axis $0–$1 = 0–100% implied probability.")
            chart_csv(path[["transaction_ts", "price", "quantity"]],
                      f"dkex_pricepath_{sym}.csv", "dl_path")

        cc = st.columns(2)
        with cc[0]:
            st.markdown("#### Activity by hour of day (ET)")
            byh = D.ts_by_hour(tas)
            if not byh.empty:
                fig = px.bar(byh, x="hour", y="contracts",
                             labels={"hour": "Hour of day (ET)",
                                     "contracts": "Contracts"})
                fig.update_traces(marker_color=CONTRACT_COLOR)
                st.plotly_chart(fig, use_container_width=True)
                chart_csv(byh, "dkex_ts_by_hour.csv", "dl_byh")
        with cc[1]:
            st.markdown("#### Trade-size distribution")
            sizes = D.ts_trade_size_dist(tas)
            fig = px.histogram(sizes, nbins=40,
                               labels={"value": "Contracts per trade"})
            fig.update_traces(marker_color=NOTIONAL_COLOR)
            fig.update_layout(showlegend=False, yaxis_title="# trades",
                              xaxis_title="Contracts per trade")
            st.plotly_chart(fig, use_container_width=True)
            chart_csv(sizes.rename("contracts_per_trade").to_frame(),
                      "dkex_trade_sizes.csv", "dl_sizes")

        st.markdown("#### VWAP & price range by contract")
        st.caption("Per contract over the selected range. Price columns are $ "
                   "(implied prob.); `contracts` is a count.")
        vw = D.ts_vwap_by_contract(tas, 50)
        if not vw.empty:
            st.dataframe(vw, use_container_width=True, hide_index=True)
            dl_button(vw, "⬇️ VWAP-by-contract CSV", "dkex_ts_vwap.csv", "dl_vw")
        dl_button(tas.drop(columns=["league_group"], errors="ignore"),
                  "⬇️ Filtered trade tape CSV", "dkex_time_and_sales.csv", "dl_tas")

# ---- Liquidity & structure ---------------------------------------------------
with tab_liq:
    traded = df[df["traded"]]
    st.subheader("Fill / activity rate")
    listed = len(df)
    n_traded = len(traded)
    rate = (n_traded / listed) if listed else 0
    cols = st.columns(3)
    cols[0].metric("Traded ÷ listed", f"{rate * 100:.2f}%")
    cols[1].metric("Distinct traded contracts", num(n_traded))
    cols[2].metric("Listed contracts", num(listed))

    st.subheader("Concentration of volume")
    hhi = D.hhi(df)
    conc = D.concentration(df)
    if conc.empty:
        st.info("No traded volume for filters.")
    else:
        total_vol = traded["trade_volume"].sum()
        cuts = {}
        v = traded["trade_volume"].sort_values(ascending=False).values
        for kk in (1, 5, 10, 25):
            cuts[kk] = (v[:kk].sum() / total_vol) if total_vol else 0
        cc = st.columns(5)
        cc[0].metric("HHI", f"{hhi:.3f}" if hhi == hhi else "—")
        for i, kk in enumerate((1, 5, 10, 25)):
            cc[i + 1].metric(f"Top {kk} share", f"{cuts[kk] * 100:.1f}%")
        fig = px.line(conc, x="rank", y="cum_share",
                      labels={"rank": "Contract rank (by volume)",
                              "cum_share": "Cumulative share of volume"})
        fig.update_yaxes(tickformat=".0%")
        fig.update_traces(line_color=CONTRACT_COLOR)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"HHI = Σ(share²) across traded contracts. "
                   f"Higher = more concentrated. Top-1 contract = "
                   f"{cuts[1] * 100:.1f}% of all volume.")
        chart_csv(conc, "dkex_concentration_curve.csv", "dl_conc")

    cc = st.columns(2)
    with cc[0]:
        st.subheader("Contracts per event / per line")
        if n_traded:
            per_event = (traded.groupby("event_id")["trade_volume"].sum().mean())
            per_line = traded["trade_volume"].mean()
            st.metric("Avg contracts per traded contract-line", f"{per_line:,.0f}")
            st.metric("Avg contracts per (approx.) event", f"{per_event:,.0f}")
    with cc[1]:
        st.subheader("Settlement outcome distribution")
        st.caption("Among **settled** contracts. Unit: **contracts**.")
        sd = D.settlement_distribution(df)
        if sd.empty:
            st.info("No settled contracts in range.")
        else:
            fig = px.pie(sd, names="bucket", values="contracts", hole=0.5)
            st.plotly_chart(fig, use_container_width=True)
            chart_csv(sd, "dkex_settlement_dist.csv", "dl_sd")

# ---- Settlement (from the Daily Settlement Report) ---------------------------
with tab_settle:
    st.subheader("Settlement outcomes")
    st.caption(
        "From the **Daily Settlement Report** — how each contract resolved. Counts "
        "are **contracts** (rows), not dollars. Respects league/market/source and "
        "date filters. This report is also the source of the **verified names** "
        "used across the dashboard.")
    settle = D.filter_settlement(settle_all, start=start_d, end=end_d,
                                 leagues=sel_leagues, markets=sel_markets,
                                 sources=sel_sources)
    if settle.empty:
        st.info("No settlements for the current filters.")
    else:
        cc = st.columns(4)
        cc[0].metric("Settled contracts", num(len(settle)))
        yes = int((settle["settlement_price"] >= 0.99).sum())
        no = int((settle["settlement_price"] <= 0.01).sum())
        push = int(((settle["settlement_price"] > 0.01) &
                    (settle["settlement_price"] < 0.99)).sum())
        cc[1].metric("Resolved YES ($1)", num(yes))
        cc[2].metric("Resolved NO ($0)", num(no))
        cc[3].metric("Push / void ($0.50)", num(push))

        cc = st.columns(2)
        with cc[0]:
            st.markdown("#### Resolution split")
            split = D.settlement_outcome_split(settle)
            fig = px.pie(split, names="bucket", values="contracts", hole=0.5)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("$0.50 = a push/void — e.g. a first-5-innings (IT5) "
                       "moneyline where the half ended tied; both sides refunded.")
            chart_csv(split, "dkex_settlement_split.csv", "dl_ssplit")
        with cc[1]:
            st.markdown("#### Resolution by market type")
            bym = D.settlement_by_dim(settle, "market_raw")
            if not bym.empty:
                fig = px.bar(bym, x="market_raw", y="contracts", color="bucket",
                             labels={"market_raw": "Market", "contracts": "Contracts",
                                     "bucket": "Resolved"})
                st.plotly_chart(fig, use_container_width=True)
                chart_csv(bym, "dkex_settlement_by_market.csv", "dl_sbym")

        st.markdown("#### Recent settlements")
        st.caption("`market_name` is Railbird's own human-readable label.")
        cols = ["report_date", "market_name", "symbol", "settlement_price",
                "status", "settlement_ts", "league_raw", "market"]
        recent = settle.sort_values("settlement_ts", ascending=False)[cols].head(200)
        st.dataframe(recent, use_container_width=True, hide_index=True)
        dl_button(settle.drop(columns=["league_group"], errors="ignore"),
                  "⬇️ Filtered settlements CSV", "dkex_settlements.csv", "dl_settle")

# ---- Reconciliation ----------------------------------------------------------
with tab_recon:
    st.subheader("Annualized run-rate vs. a benchmark")
    st.caption(
        "Annualizes the **selected range** to a yearly run-rate and compares "
        "against a manually-entered reference. **Caveat:** Railbird reflects only "
        "the regulated-exchange layer — not DraftKings' full Predictions/consumer "
        "volume or market-making. Dollar figures are **estimates**.")
    ndays = max((end_d - start_d).days + 1, 1)
    daily_contracts = k["contracts"] / ndays
    daily_notional = k["notional"] / ndays
    daily_max = k["max_notional"] / ndays
    ann_contracts = daily_contracts * 365
    ann_notional = daily_notional * 365
    ann_max = daily_max * 365
    cc = st.columns(3)
    cc[0].metric("Annualized contracts", num(round(ann_contracts)))
    cc[1].metric("Annualized notional @ price (est.)", money(ann_notional))
    cc[2].metric("Annualized max notional (est.)", money(ann_max))
    st.caption(f"Basis: {ndays} day(s) in range × 365. "
               f"Daily avg: {num(round(daily_contracts))} contracts, "
               f"{money(daily_notional)} notional@price.")

    st.markdown("**Compare to a benchmark** (defaults from DK's Q1'26 call):")
    cc = st.columns(2)
    bench_consumer = cc[0].number_input(
        "Benchmark A ($, e.g. ~$1B annualized consumer volume)",
        value=1_000_000_000.0, step=100_000_000.0, format="%.0f")
    bench_total = cc[1].number_input(
        "Benchmark B ($, e.g. ~$2.3B annualized total volume traded)",
        value=2_300_000_000.0, step=100_000_000.0, format="%.0f")
    basis = st.radio("Exchange figure to compare",
                     ["Notional @ price (est.)", "Max notional (est.)"],
                     horizontal=True)
    ex_val = ann_notional if basis.startswith("Notional") else ann_max
    cc = st.columns(2)
    cc[0].metric(f"Exchange {basis} as % of Benchmark A",
                 f"{100 * ex_val / bench_consumer:.2f}%" if bench_consumer else "—")
    cc[1].metric(f"Exchange {basis} as % of Benchmark B",
                 f"{100 * ex_val / bench_total:.2f}%" if bench_total else "—")
    st.info("These percentages compare an **exchange-only, estimated** figure "
            "against a broader disclosed metric — treat as a floor/sanity check, "
            "not an apples-to-apples reconciliation.")

# ---- New / Novelty -----------------------------------------------------------
with tab_new:
    st.subheader("New-sport / new-market detector")
    st.caption("First date each code appears across ALL ingested files "
               "(ignores the sidebar filters on purpose).")
    firsts = D.new_sport_detector(df_all)
    firsts_disp = firsts.rename(columns={
        "league_raw": "League code", "first_seen": "First seen",
        "known_label": "Has friendly name?"})
    st.markdown("**Leagues — first appearance**")
    st.dataframe(firsts_disp, use_container_width=True, hide_index=True)
    unknown = firsts[~firsts["known_label"]]
    if not unknown.empty:
        st.warning("Unrecognized league code(s): " +
                   ", ".join(unknown["league_raw"]) +
                   " — add them to `reference.LEAGUE_NAMES` for a nicer label.")
    else:
        st.success("All league codes have friendly names. No new sports yet.")

    nov = D.novelty_report(df_all)
    labels = {"league_raw": "Leagues", "market_raw": "Market types",
              "source_raw": "Sources / periods"}
    known_sets = {"league_raw": LEAGUE_NAMES, "market_raw": MARKET_NAMES,
                  "source_raw": SOURCE_NAMES}
    for dim, gg in nov.items():
        gg = gg.copy()
        gg["known"] = gg[dim].isin(known_sets[dim])
        with st.expander(f"{labels[dim]} — first-seen dates "
                         f"({(~gg['known']).sum()} unknown)"):
            st.dataframe(gg.rename(columns={dim: "Code",
                                            "first_seen": "First seen",
                                            "known": "Known?"}),
                         use_container_width=True, hide_index=True)

# ---- Data quality ------------------------------------------------------------
with tab_dq:
    st.subheader("Ingestion & parse quality")
    if not ingest.empty:
        ig = ingest.sort_values(["report_type", "business_date"])
        tot = ig["rows_total"].sum()
        warned = ig["rows_warned"].sum()
        cc = st.columns(4)
        cc[0].metric("Files ingested", num(len(ig)))
        cc[1].metric("Rows total (all reports)", num(tot))
        cc[2].metric("Rows fully parsed", num(tot - warned))
        cc[3].metric("Rows with warnings", num(warned))
        by_type = (ig.groupby("report_type")
                     .agg(files=("source_file", "size"),
                          rows=("rows_total", "sum"),
                          warned=("rows_warned", "sum")).reset_index())
        st.markdown("**By report family**")
        st.dataframe(by_type.rename(columns={
            "report_type": "Report", "files": "Files", "rows": "Rows",
            "warned": "Warned"}), use_container_width=True, hide_index=True)
        with st.expander("Per-file ingest log"):
            st.dataframe(ig.rename(columns={
                "source_file": "File", "report_type": "Report",
                "business_date": "Date", "rows_total": "Rows",
                "rows_parsed_ok": "Parsed OK", "rows_warned": "Warned",
                "ingested_at": "Ingested at (UTC)"}),
                use_container_width=True, hide_index=True)

    st.subheader("Date coverage gaps")
    have = pd.to_datetime(sorted(df_all["business_date"].unique()))
    full = pd.date_range(have.min(), have.max(), freq="D")
    gaps = sorted(set(full) - set(have))
    if gaps:
        st.write(f"{len(gaps)} calendar day(s) with **no report** "
                 "(weekends/holidays are expected):")
        st.write(", ".join(d.date().isoformat() for d in gaps))
    else:
        st.success("No gaps — every calendar day in range has a report.")

    st.subheader("Parse warnings / unparsed tokens")
    if warns.empty:
        st.success("No parse warnings. Every symbol decoded cleanly.")
    else:
        st.write(f"{len(warns)} row(s) flagged:")
        st.dataframe(warns, use_container_width=True, hide_index=True)
        dl_button(warns, "⬇️ Warnings CSV", "dkex_warnings.csv", "dl_warn")

# ---- Export ------------------------------------------------------------------
with tab_export:
    st.subheader("Downloads")
    st.caption("The tidy dataset reflects the **current sidebar filters**.")
    st.write(f"Rows in current selection: **{num(len(df))}**")
    dl_button(df, "⬇️ Download combined tidy dataset (filtered) CSV",
              "dkex_tidy_filtered.csv", "dl_tidy")
    st.write("Or the full unfiltered decoded table:")
    dl_button(df_all, "⬇️ Download full decoded dataset CSV",
              "dkex_tidy_full.csv", "dl_full")
    st.caption("Column key — `trade_volume`: contracts; `notional_at_price`: "
               "Σ(volume×last price) $ **est.**; `max_notional`: Σ(volume×$1) $ "
               "**est.**; `line`: parsed threshold; `outcome_label`: verified name "
               "where available.")

    st.divider()
    st.markdown("**Other report families** (respect the date/league/market/source "
                "filters; status doesn't apply):")
    settle_f = D.filter_settlement(settle_all, start=start_d, end=end_d,
                                   leagues=sel_leagues, markets=sel_markets,
                                   sources=sel_sources)
    tas_f = D.filter_timesales(tas_all, start=start_d, end=end_d, leagues=sel_leagues,
                               markets=sel_markets, sources=sel_sources)
    cc = st.columns(2)
    with cc[0]:
        st.write(f"Settlements in selection: **{num(len(settle_f))}**")
        dl_button(settle_f, "⬇️ Settlement report (filtered) CSV",
                  "dkex_settlements_filtered.csv", "dl_settle_x")
    with cc[1]:
        st.write(f"Trades in selection: **{num(len(tas_f))}**")
        dl_button(tas_f, "⬇️ Time & Sales tape (filtered) CSV",
                  "dkex_time_and_sales_filtered.csv", "dl_tas_x")
    st.write("Verified names table (settlement-derived):")
    dl_button(D.load_table(db_path, "verified_names"),
              "⬇️ Verified names CSV", "dkex_verified_names.csv", "dl_vn")
