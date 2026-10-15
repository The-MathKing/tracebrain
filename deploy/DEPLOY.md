# Deploying TraceBrain

One container serves both the API (`/api/*`) and the built web UI (`/`) on a single port. FalkorDB is the only
datastore. The demo graph ships with the repo as a snapshot (`deploy/snapshot/org_falkordb.json.gz`), so a new,
empty FalkorDB is filled on first boot. No ingest run or GitHub token is needed for the demo.

| Path | DB | Time |
|---|---|---|
| A. Live: FalkorDB Cloud + Render (recommended) | FalkorDB Cloud free instance | ~15 min, all clicks |
| B. Local without Docker | embedded falkordblite (or any FalkorDB) | ~5 min |
| C. Local with Docker | `falkordb/falkordb` container | ~5 min |

## Environment variables

| Var | Needed | Notes |
|---|---|---|
| `FALKORDB_URL` | live / Docker | `redis://user:pass@host:port` (or `rediss://` for TLS). Unset means embedded falkordblite at `data/falkordb.db`. |
| `OPENROUTER_API_KEY` | for chat | Or set `ANTHROPIC_API_KEY` instead. Without a key the UI and graph still work, but chat returns an error. |
| `GITHUB_TOKEN` | optional | Read-only. Used only for live ingest/sync from the UI or API. |
| `TRACEBRAIN_SEED_ON_START` | `1` on deploys | On boot, if the tenant graph is empty, imports the bundled snapshot in a background thread. |
| `TRACEBRAIN_TENANT` | optional | Default `falkordb`, which means graph `org_falkordb`. |
| `TRACEBRAIN_MODEL` | optional | Default `anthropic/claude-opus-5.5` on OpenRouter, `claude-opus-5-5` on Anthropic. |
| `TRACEBRAIN_ALLOW_GITHUB_WRITE` | keep `0` | Posting triage comments to GitHub stays off. |

---

## A. Live deploy: FalkorDB Cloud + Render

### A1. Push the repo to GitHub
1. On github.com, click **New repository**. Name it, e.g. `tracebrain`. Don't add a README. Public or private both work.
2. From the repo root, run:
   ```bash
   git add -A && git commit -m "Deploy config"      # .env, data/, web/dist, node_modules are gitignored
   git remote add origin https://github.com/<you>/tracebrain.git
   git push -u origin HEAD:main
   ```
   Check that `deploy/snapshot/org_falkordb.json.gz`, `Dockerfile` and `render.yaml` are on GitHub.

### A2. Create the FalkorDB Cloud database
1. Go to https://app.falkordb.cloud and sign up (Google or GitHub sign-in works).
2. Click **Create Database** (or **+ New instance**) and choose the **Free** tier.
3. Pick any cloud and region. For low latency, choose the same region you'll pick on Render (e.g. AWS `us-east-1` with Render Virginia/Ohio, or GCP `us-central1` with Render Ohio).
4. Give it a name. Set the **username** (default `falkordb`) and a **password**. Write the password down.
5. Click **Create** and wait until the status says *Running* (1 to 3 minutes).
6. Open the instance and copy its **Host** and **Port** from the *Connect* / connection details panel. The host looks like `r-xxxxxxxx.instance-xxxx....cloud`. The port is usually not 6379.
7. Build the URL:
   ```
   redis://<username>:<password>@<host>:<port>
   ```
   - If the instance shows **TLS enabled** (TLS is a paid-tier option; free instances are usually plain TCP), use `rediss://` (two s's) instead.
   - URL-encode special characters in the password (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `#` → `%23`). For example: `python3 -c "import urllib.parse;print(urllib.parse.quote('p@ss/w:rd', safe=''))"`.
8. Optional check from your laptop: `FALKORDB_URL='redis://...' .venv/bin/python -m tracebrain.seed counts` should print empty `labels`/`rels` and no error.

**How the URL is handled.** This was checked against the installed `falkordb` 1.8.0 and `redis` 8.1 sources. `FalkorDB.from_url`
rewrites `falkor://` → `redis://` and `falkors://` → `rediss://`, then calls `redis.from_url(url, decode_responses=True)`.
`redis.connection.parse_url` takes the username and password from the URL (percent-decoded), and the port and `/db` from the path.
For `rediss://` it sets `connection_class=SSLConnection`. The defaults are `ssl_cert_reqs="required"` and `ssl_check_hostname=True`,
with CAs from the system store (`python:3.12-slim` ships `ca-certificates`). Unknown query parameters are passed through as
keyword arguments. So if you ever hit a private-CA certificate error, `rediss://...:port?ssl_cert_reqs=none` disables verification.
That is a last resort. All four schemes (`redis`, `rediss`, `falkor`, `falkors`) work in `FALKORDB_URL`.
Verified locally: `rediss://falkordb:p%40ss@host:61234` gives `SSLConnection`, user `falkordb`, password `p@ss`, `CERT_REQUIRED`
and hostname checking on. Also note that the `FalkorDB(...)` constructor connects right away (it sends `INFO` to detect
Sentinel/Cluster). A wrong host or password therefore fails at app startup, and the Render logs show the redis error.

### A3. Deploy on Render
1. Go to https://dashboard.render.com and sign up with **GitHub**. Give Render access to the `tracebrain` repo.
2. Click **New +** → **Blueprint**, select the repo, and click **Connect**. Render reads `render.yaml`, which defines a free Docker web service named `tracebrain` with health check `/api/health`.
3. Render asks for the `sync: false` secrets. Paste:
   - `OPENROUTER_API_KEY`: your OpenRouter key (`sk-or-...`).
   - `FALKORDB_URL`: the URL from A2.7.
   - `GITHUB_TOKEN`: optional. Leave blank, or paste a read-only token (`gh auth token`).

   `TRACEBRAIN_SEED_ON_START=1`, `TRACEBRAIN_TENANT=falkordb` and `TRACEBRAIN_ALLOW_GITHUB_WRITE=0` are set already.
   To use Anthropic directly, add `ANTHROPIC_API_KEY` later under the service's **Environment** tab.
4. Click **Apply**. The first build (npm build, then pip install) takes about 4 to 6 minutes. Watch the **Logs** tab.
5. When the service is *Live*, open `https://tracebrain-xxxx.onrender.com/api/health`. Expect
   `{"ok":true,"falkordb":true,"llm_configured":true,...}`. The logs show
   `seed-on-start: graph is empty, importing ...` and then `seed-on-start: done {...}` within a few seconds.
6. Open `https://tracebrain-xxxx.onrender.com/`. The overview should show People, PRs, Issues, Files and Decisions.
   Later boots find the graph already filled and skip the import. Memory written by demo users persists in FalkorDB Cloud.

**Free-tier notes.** A free Render service sleeps after 15 minutes without traffic, and the next request takes about a minute
to wake it. Open the URL a minute before you demo or judge. Re-seeding is never automatic once the graph has data. To reset,
delete the graph in FalkorDB Browser (`GRAPH.DELETE org_falkordb`) and redeploy, or run `make seed-import` against the URL.

**Manual seeding (alternative to seed-on-start):** `FALKORDB_URL='redis://...' make seed-import`. MERGE on `key` makes it idempotent.

---

## B. Local, no Docker (venv + one uvicorn on one port)
Requires Python 3.12+ and Node 20.19+ (or 22.12+). Embedded FalkorDB (`falkordblite`) has wheels for macOS and for Linux with glibc ≥ 2.39, Python 3.12 to 3.14.
```bash
python3 -m venv .venv
.venv/bin/pip install -e './backend[lite,dev]'      # `lite` = embedded FalkorDB; skip it if you set FALKORDB_URL
echo "OPENROUTER_API_KEY=sk-or-..." >> .env         # or ANTHROPIC_API_KEY=...; .env is gitignored
(cd web && npm ci && npm run build)                 # -> web/dist, served by the API at /
make seed-import                                    # loads deploy/snapshot into data/falkordb.db (idempotent)
.venv/bin/uvicorn tracebrain.api:app --app-dir backend --port 8000
```
Open http://localhost:8000. The UI and API share that one port, and deep links fall back to `index.html`.
`make install`, `make build` and `make serve` run the same steps. For UI development with hot reload, run
`make dev-api` and `make dev-web` and open http://localhost:5173. `make mock` runs a fake backend that needs no Python or DB.

To use a local FalkorDB server instead of the embedded one, put `FALKORDB_URL=redis://localhost:6379` in `.env`.

## C. Local with Docker
```bash
echo "OPENROUTER_API_KEY=sk-or-..." >> .env   # compose reads .env if present
docker compose up --build                     # or: make docker
```
- App (UI + API): http://localhost:8000. It self-seeds the empty FalkorDB on first boot.
- FalkorDB Browser: http://localhost:3000. Connect to `localhost:6379` with no password and pick graph `org_falkordb`.
- Data persists in the `falkordb-data` volume. `docker compose down -v` resets it.

To run the image alone against FalkorDB Cloud:
`docker build -t tracebrain . && docker run -p 8000:8000 -e FALKORDB_URL='redis://...' -e OPENROUTER_API_KEY=... -e TRACEBRAIN_SEED_ON_START=1 tracebrain`.
Build with `--build-arg EXTRAS=lite` to include embedded falkordblite in the image (only for running without `FALKORDB_URL`).

---

## The snapshot
- `python -m tracebrain.seed export|import|counts [--tenant falkordb] [--path ...] [--include-memory]`, also available as `make seed-export` and `make seed-import`.
- The format is gzipped JSON: `{version, graph, exported_at, counts, nodes:[{labels, props}], edges:[{type, src, dst, src_label, dst_label, props}]}`. Node identity is `props.key`.
- Import runs `ensure_schema` (range index on `key` per label, plus the full-text indexes), then `UNWIND $rows MERGE (n:Label {key: row.key}) SET n += row.props` in batches of 500. Edges go in per (type, source label, target label) group with `MATCH ... MATCH ... MERGE`. Import works against any FalkorDB: embedded, Docker or Cloud.
- By default, memory and coordination nodes (`User`, `Session`, `Episode`, `Fact`, `Task`, `Agent`) and their edges are left out, so every deploy starts with a clean memory.
- To refresh the snapshot after a new ingest or extraction run: `make seed-export`, then commit `deploy/snapshot/org_falkordb.json.gz` and push. Render redeploys on its own. Seed-on-start won't overwrite a graph that already has data, so re-import with `make seed-import` against the Cloud URL (it adds and updates, never deletes).
