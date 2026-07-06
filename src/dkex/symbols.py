"""Decode a Railbird / DKEX contract *Symbol* into structured fields.

Symbol grammar (hyphen-delimited, 5-7 segments):

    LEAGUE-MARKETTYPE-SOURCE-EVENTID-OUTCOME[-OP-P000XX]

Examples (all real / test-covered):
    MLB-WIN-IT5-0146NBSSL8SDU3G-NYTS000            (5 seg: team, no line)
    MLB-TRUNS-IT5-0145BO10L8S8G70-GTE-P00055       (6 seg: line, no team)
    KBO-MOVY-FG-00N8UBQBPIT76T0-DORS000-GTE-P00015 (7 seg: team + line)
    PGA-3BLS-R1-00GPGBS0LP47...-BRAN008            (5 seg: golf player)

Design goals:
  * Never raise on odd input -- return a result with ``parse_ok=False`` and a
    ``warning`` instead, keeping whatever we could extract.
  * Keep BOTH raw tokens and decoded/friendly fields so nothing is lost.
  * Disambiguate threshold (GTE-P000XX) vs. team/player outcomes structurally,
    by locating the ``P#####`` line token rather than trusting segment counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from . import reference as ref

# A threshold "line" token looks like P00055 -> 5.5 (value / 10).
_LINE_RE = re.compile(r"^P(\d{3,})$")
# Comparison operators that may precede the line token. GTE is the only one
# observed, but we tolerate siblings so schema drift doesn't break parsing.
_OP_TOKENS = {"GTE", "LTE", "GT", "LT", "EQ", "GTL", "LTL"}


@dataclass
class ParsedSymbol:
    symbol: str
    league_raw: str | None = None
    league: str | None = None
    league_group: str | None = None
    market_raw: str | None = None
    market: str | None = None
    source_raw: str | None = None
    source: str | None = None
    event_id: str | None = None
    outcome_code: str | None = None
    outcome_name: str | None = None
    outcome_kind: str | None = None  # 'team' | 'player' | 'threshold' | None
    threshold_op: str | None = None  # e.g. 'GTE'
    line: float | None = None
    n_segments: int = 0
    unparsed_tokens: str | None = None  # '|'-joined leftovers, or None
    parse_ok: bool = True
    warning: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _parse_line(token: str) -> float | None:
    m = _LINE_RE.match(token)
    if not m:
        return None
    return int(m.group(1)) / 10.0


def parse_symbol(symbol: str) -> ParsedSymbol:
    """Decode one Symbol string. Always returns a ParsedSymbol (never raises)."""
    if symbol is None or not isinstance(symbol, str) or symbol.strip() == "":
        return ParsedSymbol(symbol=(symbol.strip() if isinstance(symbol, str) else ""),
                            parse_ok=False, warning="empty or non-string symbol")

    sym = symbol.strip()
    segs = sym.split("-")
    n = len(segs)
    res = ParsedSymbol(symbol=sym, n_segments=n)

    # Segments 1-3 are positional and always meaningful when present.
    res.league_raw = segs[0] if n > 0 else None
    res.market_raw = segs[1] if n > 1 else None
    res.source_raw = segs[2] if n > 2 else None

    if res.league_raw:
        res.league = ref.friendly_league(res.league_raw)
        res.league_group = ref.league_group(res.league_raw)
    if res.market_raw:
        res.market = ref.friendly_market(res.market_raw)
    if res.source_raw:
        res.source = ref.friendly_source(res.source_raw)

    if n < 4:
        # No event id / outcome payload -- unusual but load it anyway.
        res.parse_ok = False
        res.warning = f"only {n} segments (expected >= 4)"
        return res

    rest = segs[3:]
    res.event_id = rest[0] if rest else None

    # Locate the threshold line token (P#####) anywhere in the tail.
    line_idx = next((i for i, t in enumerate(rest) if _LINE_RE.match(t)), None)

    leftovers: list[str] = []
    if line_idx is not None:
        res.line = _parse_line(rest[line_idx])
        # The op keyword, if any, immediately precedes the line token.
        if line_idx - 1 >= 1 and rest[line_idx - 1] in _OP_TOKENS:
            res.threshold_op = rest[line_idx - 1]
            outcome_end = line_idx - 1
        else:
            outcome_end = line_idx
        middle = rest[1:outcome_end]  # tokens between event id and op/line
        if middle:
            res.outcome_code = middle[0]
            leftovers.extend(middle[1:])
        leftovers.extend(rest[line_idx + 1:])  # anything after the line token
    else:
        # No line: the token after the event id is the team/player outcome.
        if len(rest) > 1:
            res.outcome_code = rest[1]
            leftovers.extend(rest[2:])

    # Classify the outcome and attach a friendly name.
    if res.outcome_code:
        if res.league_raw == "PGA":
            res.outcome_kind = "player"
            res.outcome_name = res.outcome_code  # players not mapped; keep raw
        else:
            res.outcome_kind = "team"
            res.outcome_name = ref.friendly_team(res.league_raw, res.outcome_code)
    elif res.line is not None:
        res.outcome_kind = "threshold"
        res.outcome_name = None

    if leftovers:
        res.unparsed_tokens = "|".join(leftovers)
        res.warning = (res.warning + "; " if res.warning else "") + \
            f"unrecognized extra tokens: {res.unparsed_tokens}"

    # Flag friendly-name gaps so the diagnostics panel can surface novelty. This
    # does NOT set parse_ok=False -- unknowns are expected, not errors.
    unknown_bits = []
    if res.league_raw and res.league_raw not in ref.LEAGUE_NAMES:
        unknown_bits.append(f"league={res.league_raw}")
    if res.market_raw and res.market_raw not in ref.MARKET_NAMES:
        unknown_bits.append(f"market={res.market_raw}")
    if res.source_raw and res.source_raw not in ref.SOURCE_NAMES:
        unknown_bits.append(f"source={res.source_raw}")
    if unknown_bits:
        note = "unknown token(s): " + ", ".join(unknown_bits)
        res.warning = (res.warning + "; " if res.warning else "") + note

    return res
