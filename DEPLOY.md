# Deploying the dashboard

GitHub Pages **cannot** run this app — it only serves static files, and Streamlit
is a live Python server. Use **Streamlit Community Cloud** (free) for the live,
interactive dashboard; this repo also ships a static **GitHub Pages landing page**
(in `docs/`) that links to it.

## 1. Live app — Streamlit Community Cloud (free)

The app **self-bootstraps**: the raw report CSVs are committed under `data/raw/`,
and on first launch the app builds its SQLite store from them (no network needed).

1. Go to <https://share.streamlit.io> and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub**, and choose:
   - **Repository:** `nosherzapoo/<this-repo>`
   - **Branch:** `main`
   - **Main file path:** `app/dashboard.py`
3. Click **Deploy**. You'll get a URL like `https://<name>.streamlit.app`.

One-click prefill (replace `REPO`):
`https://share.streamlit.io/deploy?repository=nosherzapoo/REPO&branch=main&mainModule=app/dashboard.py`

### Keeping the hosted data fresh
The committed CSVs are a snapshot. To refresh: run `python -m dkex.fetch` locally,
commit the new files in `data/raw/`, and push — Streamlit Cloud redeploys and the
app rebuilds its store on next launch. (Or wire the fetcher into a scheduled
GitHub Action — see the nice-to-haves in the README.)

## 2. GitHub Pages — interactive "volume by sport" (no server needed)

`docs/index.html` is a **self-contained, dependency-free** page: a stacked bar of
trade volume by sport over time, a measure toggle (contracts / notional $ / max $),
clickable legend, and a **Download CSV** button. It runs entirely in the browser
on GitHub Pages — served at `https://nosherzapoo.github.io/<repo>/`.

Its data lives in `docs/data.js` (a pre-aggregated `window.DKEX_DATA`) plus a plain
`docs/dkex_sports_breakdown.csv`. **Regenerate both** whenever the store changes:

```bash
PYTHONPATH=src python scripts/build_site_data.py   # reads data/processed/dkex.sqlite
git add docs/data.js docs/dkex_sports_breakdown.csv && git commit -m "refresh site data" && git push
```

Pages is configured to serve the `/docs` folder on `main`. (The full multi-tab
Streamlit dashboard from step 1 is the deeper tool; this page is the quick,
always-on public view.)

## 3. Run locally (no hosting)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/dashboard.py      # builds the DB from data/raw on first run
```
