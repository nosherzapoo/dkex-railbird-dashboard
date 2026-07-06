"""Download Railbird Exchange report CSVs (idempotent, polite) for all 3 families.

Report families (each served by its own manifest, same reverse-engineered scheme):
    daily-market      -> per-contract end-of-day snapshot
    daily-settlement  -> how each contract resolved (+ human-readable Market Name)
    time-and-sales    -> the trade tape (one row per execution)

Mechanism:
    manifest : GET https://railbirdexchange.com/reports/<family>/manifest.json
               -> [{ "name", "date": "YYYY-MM-DD", "href": "/reports/.../*.csv" }, ...]
    file     : GET https://railbirdexchange.com<href>   (spaces/parens/& URL-encoded)

Behavior:
  * Defaults to "download any dates in the manifest not already on disk", for
    all three families (--report all).
  * Optional --start / --end (inclusive ISO) and --report to scope one family.
  * Polite: descriptive User-Agent, 1 request/sec, retry w/ backoff.
  * Idempotent: skips files already present locally (unless --force). Caches each
    manifest to data/processed/<family>_manifest_cache.json.

Usage:
    python -m dkex.fetch                              # all families, new dates
    python -m dkex.fetch --report time-and-sales      # one family
    python -m dkex.fetch --start 2026-06-11 --end 2026-06-20
    python -m dkex.fetch --list                       # show manifests, download nothing
    python -m dkex.fetch --force                      # re-download listed dates
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

from . import RAW_DIR

BASE = "https://railbirdexchange.com"
USER_AGENT = ("dkex-research/0.2 (personal DKEX daily-report analytics; "
              "polite 1rps; contact via local use)")
RATE_LIMIT_SEC = 1.0
MAX_RETRIES = 4

# Per-family config. `prefix` is the normalized local filename stem.
REPORTS = {
    "daily-market":     {"prefix": "Daily_Market_Report"},
    "daily-settlement": {"prefix": "Daily_Settlement_Report"},
    "time-and-sales":   {"prefix": "Time_and_Sales_Report"},
}
ALL_FAMILIES = list(REPORTS)


def _manifest_url(family: str) -> str:
    return f"{BASE}/reports/{family}/manifest.json"


def _manifest_cache(family: str) -> str:
    return f"data/processed/{family}_manifest_cache.json"


def _http_get(url: str, timeout: int = 60) -> bytes:
    """GET with retry + exponential backoff. Raises on final failure."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:  # noqa: BLE001 -- network errors vary; back off & retry
            last_err = e
            wait = 2 ** attempt
            print(f"[fetch] GET failed ({e}); retry {attempt + 1}/{MAX_RETRIES} "
                  f"in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} attempts: {last_err}")


def _encode_href(href: str) -> str:
    """Percent-encode the path (spaces, parens, &) while keeping '/' separators."""
    return urllib.parse.quote(href, safe="/")


def local_name_for(family: str, d: str) -> str:
    """Normalized on-disk filename for a family + date, e.g.
    'Daily_Settlement_Report_-__2026_07_05_.csv'."""
    y, m, day = d.split("-")
    return f"{REPORTS[family]['prefix']}_-__{y}_{m}_{day}_.csv"


def fetch_manifest(family: str, use_cache_on_fail: bool = True) -> list[dict]:
    """Return a family's report entries; falls back to the cached copy offline."""
    cache = _manifest_cache(family)
    try:
        data = _http_get(_manifest_url(family))
        reports = json.loads(data).get("reports", [])
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w") as f:
            json.dump({"reports": reports,
                       "cached_at": datetime.now(timezone.utc).isoformat()},
                      f, indent=2)
        return reports
    except Exception as e:  # noqa: BLE001
        if use_cache_on_fail and os.path.exists(cache):
            print(f"[fetch] {family} manifest fetch failed ({e}); using cache.")
            return json.load(open(cache)).get("reports", [])
        raise


def _in_range(d: str, start: str | None, end: str | None) -> bool:
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def run(raw_dir=RAW_DIR, report="all", start=None, end=None, force=False,
        list_only=False) -> list[str]:
    os.makedirs(raw_dir, exist_ok=True)
    families = ALL_FAMILIES if report in ("all", None) else [report]
    downloaded: list[str] = []

    for family in families:
        reports = [r for r in fetch_manifest(family)
                   if _in_range(r["date"], start, end)]
        reports.sort(key=lambda r: r["date"])

        if list_only:
            print(f"\n[fetch] {family}: {len(reports)} report(s) in range:")
            for r in reports:
                local = os.path.join(raw_dir, local_name_for(family, r["date"]))
                mark = "on-disk" if os.path.exists(local) else "MISSING"
                print(f"   {r['date']}  [{mark}]  {r['href']}")
            continue

        if start and end:  # warn about requested dates the venue didn't publish
            have = {r["date"] for r in reports}
            for d in sorted(_dates_between(start, end) - have):
                print(f"[fetch] {family}: no report for {d} "
                      "(weekend/holiday?) -- skipping")

        new_here = 0
        for i, r in enumerate(reports):
            local = os.path.join(raw_dir, local_name_for(family, r["date"]))
            if os.path.exists(local) and not force:
                continue
            url = BASE + _encode_href(r["href"])
            try:
                content = _http_get(url)
            except Exception as e:  # noqa: BLE001 -- one bad file shouldn't kill it
                print(f"[fetch] {family} FAILED {r['date']}: {e}")
                continue
            with open(local, "wb") as f:
                f.write(content)
            downloaded.append(local)
            new_here += 1
            print(f"[fetch] {family}: saved {r['date']} -> "
                  f"{os.path.basename(local)} ({len(content):,} bytes)")
            if i < len(reports) - 1:
                time.sleep(RATE_LIMIT_SEC)  # polite pacing between requests
        if not list_only:
            print(f"[fetch] {family}: {new_here} new file(s).")

    if not list_only:
        print(f"\n[fetch] done: {len(downloaded)} new file(s) across "
              f"{len(families)} family(ies).")
    return downloaded


def _dates_between(start: str, end: str) -> set[str]:
    s = datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date()
    out, cur = set(), s
    while cur <= e:
        out.add(cur.isoformat())
        cur = date.fromordinal(cur.toordinal() + 1)
    return out


def main():
    ap = argparse.ArgumentParser(description="Fetch Railbird report CSVs.")
    ap.add_argument("--raw-dir", default=RAW_DIR)
    ap.add_argument("--report", default="all",
                    choices=["all", *ALL_FAMILIES],
                    help="which report family to fetch (default: all)")
    ap.add_argument("--start", help="inclusive start date YYYY-MM-DD")
    ap.add_argument("--end", help="inclusive end date YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="list manifest entries and exit (no download)")
    args = ap.parse_args()
    run(raw_dir=args.raw_dir, report=args.report, start=args.start,
        end=args.end, force=args.force, list_only=args.list_only)


if __name__ == "__main__":
    main()
