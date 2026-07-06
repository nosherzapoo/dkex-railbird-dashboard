"""Friendly-name lookup tables for Railbird / DKEX symbol tokens.

Everything here is *best-effort*. The design rule (see the objective) is:
    unknown code -> fall back to the raw token, never crash.

LEAGUE / MARKET / SOURCE maps below are derived from the documented, observed
values and are reasonably solid. The TEAM maps, however, decode *obfuscated*
outcome codes (e.g. ``LARS000``) whose real-world mapping is a heuristic guess,
not something Railbird publishes. They are marked UNVERIFIED and surfaced as
such in the dashboard. Treat them as convenience labels, not ground truth --
edit the dicts below as you confirm codes.
"""

from __future__ import annotations

# --- Segment 1: LEAGUE / sport -------------------------------------------------
# NOT a closed set. New leagues (soccer, NFL, NBA, ...) will appear and must load
# fine; the new-sport detector keys off exactly this "is it in the map?" question.
LEAGUE_NAMES: dict[str, str] = {
    "MLB": "MLB (Baseball)",
    "KBO": "KBO (Korean Baseball)",
    "NPB": "NPB (Japanese Baseball)",
    "PGA": "PGA (Golf)",
    # NOTE: deliberately a small, honest set. New leagues (WCUP/NFL/NBA/...) are
    # left OUT so they fall back to their raw code AND trip the "unknown token"
    # warning + the new-sport detector when they first appear. Add them here once
    # you want a nicer label -- doing so does not affect the date-based detector.
}

# Coarse grouping used by the "baseball vs golf" split and mix charts.
LEAGUE_GROUP: dict[str, str] = {
    "MLB": "Baseball",
    "KBO": "Baseball",
    "NPB": "Baseball",
    "PGA": "Golf",
}


def league_group(league_raw: str) -> str:
    """Coarse sport bucket; unknowns -> 'Other' so new sports are visible."""
    return LEAGUE_GROUP.get(league_raw, "Other")


# --- Segment 2: MARKET TYPE ----------------------------------------------------
MARKET_NAMES: dict[str, str] = {
    # Baseball
    "WIN": "Moneyline (Who Wins)",
    "TRUNS": "Total Runs (Over/Under)",
    "MOVY": "Margin of Victory (Run Line)",
    # Golf
    "WTRN": "Win Tournament",
    "MCUT": "Make the Cut",
    "T5": "Top 5 Finish",
    "T10": "Top 10 Finish",
    "T20": "Top 20 Finish",
    "T30": "Top 30 Finish",
    "T40": "Top 40 Finish",
    "3BLS": "3 Balls (Group Matchup)",
    "EORL": "Each-way / Outright (EORL)",
}

# Which markets carry a numeric threshold line (TRUNS/MOVY use GTE-P000XX).
THRESHOLD_MARKETS = {"TRUNS", "MOVY"}

# --- Segment 3: SOURCE / PERIOD ------------------------------------------------
SOURCE_NAMES: dict[str, str] = {
    "FG": "Full Game",
    "IT5": "First 5 Innings",
    "TRN": "Tournament Outright",
    "R1": "Round 1",
    "R2": "Round 2",
    "R3": "Round 3",
    "R4": "Round 4",
}

# ------------------------------------------------------------------------------
# OUTCOME / TEAM codes.  *** HEURISTIC, UNVERIFIED. ***
# Codes are obfuscated (4 letters + numeric suffix). The dashboard labels these
# as best-effort. Golf player codes are far too many to map and are left raw.
# ------------------------------------------------------------------------------
MLB_TEAMS: dict[str, str] = {
    "ARKS000": "Arizona Diamondbacks",
    "ATCS000": "Atlanta Braves",
    "ATES000": "Athletics",
    "BAES000": "Baltimore Orioles",
    "BOOX000": "Boston Red Sox",
    "CHBS000": "Chicago Cubs",
    "CHOX000": "Chicago White Sox",
    "CIDS000": "Cincinnati Reds",
    "CLNS000": "Cleveland Guardians",
    "COES000": "Colorado Rockies",
    "DERS000": "Detroit Tigers",
    "HOOS000": "Houston Astros",
    "KCLS000": "Kansas City Royals",
    "LALS000": "Los Angeles Angels",
    "LARS000": "Los Angeles Dodgers",
    "MINS000": "Minnesota Twins",
    "MINS001": "Miami Marlins",
    "MIRS000": "Milwaukee Brewers",
    "NYES000": "New York Yankees",
    "NYTS000": "New York Mets",
    "PHES000": "Philadelphia Phillies",
    "PIES000": "Pittsburgh Pirates",
    "SDES000": "San Diego Padres",
    "SERS000": "Seattle Mariners",
    "SFTS000": "San Francisco Giants",
    "STLS000": "St. Louis Cardinals",
    "TERS000": "Texas Rangers",
    "TBYS000": "Tampa Bay Rays",
    "TOYS000": "Toronto Blue Jays",
    "WALS000": "Washington Nationals",
}

KBO_TEAMS: dict[str, str] = {
    "DORS000": "Doosan Bears",
    "HAES000": "Hanwha Eagles",
    "KIES000": "Kia Tigers",
    "KIRS000": "Kiwoom Heroes",
    "KTON000": "KT Wiz",
    "LGNS000": "LG Twins",
    "LOTS001": "Lotte Giants",
    "NCOS000": "NC Dinos",
    "SANS000": "Samsung Lions",
    "SSRS000": "SSG Landers",
}

NPB_TEAMS: dict[str, str] = {
    "CHES000": "Chunichi Dragons",
    "CHNS000": "Chiba Lotte Marines",
    "FUKS000": "Fukuoka SoftBank Hawks",
    "HARS001": "Hanshin Tigers",
    "HIRP000": "Hiroshima Toyo Carp",
    "HORS000": "Hokkaido Nippon-Ham Fighters",
    "ORES000": "Orix Buffaloes",
    "SANS002": "Saitama Seibu Lions",
    "TOES000": "Tohoku Rakuten Golden Eagles",
    "TOWS000": "Tokyo Yakult Swallows",
    "YORS001": "Yokohama DeNA BayStars",
    "YOTS000": "Yomiuri Giants",
}

# Combined team lookup keyed by league.
TEAM_NAMES_BY_LEAGUE: dict[str, dict[str, str]] = {
    "MLB": MLB_TEAMS,
    "KBO": KBO_TEAMS,
    "NPB": NPB_TEAMS,
}

# True for team maps that are unverified heuristic decodes (all of them, today).
TEAM_MAP_IS_HEURISTIC = True


def friendly_league(code: str) -> str:
    return LEAGUE_NAMES.get(code, code)


def friendly_market(code: str) -> str:
    return MARKET_NAMES.get(code, code)


def friendly_source(code: str) -> str:
    return SOURCE_NAMES.get(code, code)


def friendly_team(league_raw: str, code: str | None) -> str | None:
    """Best-effort team name for a baseball outcome code; raw fallback."""
    if not code:
        return None
    return TEAM_NAMES_BY_LEAGUE.get(league_raw, {}).get(code, code)
