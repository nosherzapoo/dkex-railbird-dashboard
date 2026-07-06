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

## 2. Landing page — GitHub Pages

`docs/index.html` is a static overview page with the project summary, caveats, and
a **"Launch the live dashboard"** button. After you have the Streamlit URL from
step 1, put it in `docs/index.html` (search for `STREAMLIT_APP_URL`) and push;
Pages will serve it at `https://nosherzapoo.github.io/<repo>/`.

Pages is configured to serve the `/docs` folder on `main`.

## 3. Run locally (no hosting)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/dashboard.py      # builds the DB from data/raw on first run
```
