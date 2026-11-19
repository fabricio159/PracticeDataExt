# DataExtProject

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`-e` installs the project in editable mode, so code in `src/` (e.g. `week01_http`) is importable and changes take effect without reinstalling.

## Running the scripts

```bash
python -m week01_http.login    # log in to quotes.toscrape.com and check the session
python -m week01_http.scrape   # log in, scrape pages 1-3, write quotes.json
python -m week01_http.status_probe  # hit httpbin /status/{200,301,404,429,503}, log the client's reaction (~15 s)
python -m week01_http.job_watch     # open jobs at Bolivian banks/financial entities, diffed against the last run
python -m week01_http.job_watch --watch tech  # software companies hiring LATAM engineers (data/tech/)
```

Run them with `-m` (module path), not `python src/week01_http/scrape.py`, so the `week01_http.*` imports resolve.

## job_watch sources

The pages `job_watch` reads are listed in `src/week01_http/job_watch.py`: `SOURCES` (banks, the default)
and `TECH_SOURCES` (`--watch tech`). To add one, append a `Source`:

```python
Source("Bank X · Evaluar", "https://bankx.evaluar.com/")                     # evaluar.com job board
Source("Bank X · web", "https://bankx.com/empleos", kind="html",             # any page with a list
       item="table.jobs tbody tr", title="td:nth-child(2)",                  # CSS, or "xpath:..."
       location="td:nth-child(3)", link="a::attr(href)",
       key="td:nth-child(1)",              # identifies a row when links are shared/unstable
       expect="table.jobs",                # must exist, else the layout changed
       published="td:nth-child(4)")        # publication date, if the list shows one
                                           # (or detail_published=r"regex (group)" to read
                                           #  it from each posting's own page)
Source("Company Y", "https://y.com/careers", kind="greenhouse", board="y")  # hiring-platform feed:
       # greenhouse, lever, ashby, workable, smartrecruiters or bamboohr; board = account name
       # there (e.g. boards.greenhouse.io/<board>). Add region=LATAM to keep only LATAM roles.
Source("Bank X · web", "https://bankx.com/trabaja", kind="manual",           # no readable list
       note="CV form only")
```

A new source is saved as a baseline on its first run and compared from the next run on.
Exit code: `0` all sources OK, `2` some source failed (its previous results are kept and reported).

## job_watch web page

A TypeScript page (Vite, no UI framework) that shows the snapshots as a sortable table with a filter
per column, NEW badges, removed/changed postings, failed sources, and this/previous run times.
Two tabs: banks (`data/jobs/`) and tech (`data/tech/`). Needs Node.js 18+.

```bash
cd web
npm install          # first time only
npm run dev          # http://localhost:5173
```

It reads `data/jobs/` and `data/tech/` directly through a small API in `web/vite.config.ts`, so run
`job_watch` (and `job_watch --watch tech`) first and reload the page after each run. Use other
folders with `JOBS_DIR=... TECH_DIR=... npm run dev`. `npm run typecheck` checks the types.

## Scheduled runs (Jenkins)

`Jenkinsfile` runs both lists (`banks`, then `tech`) **Monday to Friday at 08:00 La Paz time**
(`TZ=America/La_Paz`, cron `0 8 * * 1-5`). Each build installs the project into a fresh `.venv` and
calls `ci/run_job_watch.sh` once per list.

Setup:

1. Push this repo to a git server Jenkins can reach (GitHub, GitLab, ...).
2. In Jenkins: **New Item → Pipeline**. Under *Pipeline*, pick **Pipeline script from SCM**, choose Git,
   enter the repo URL, branch and credentials, and leave *Script Path* as `Jenkinsfile`.
3. Click **Build Now** once. Jenkins only registers the schedule and the `DATA_ROOT` parameter
   after it has read the `Jenkinsfile` in a first build.

The agent needs Linux, `python3` >= 3.11 (with `venv`; on Debian/Ubuntu: `apt install python3-venv`)
and internet access.

**Snapshots** are kept outside the workspace, in `DATA_ROOT` (default: `$HOME/job-watch-data` of the
user running the agent): `jobs/` for banks and `tech/` for tech. To keep your local history, copy
`data/jobs` and `data/tech` there once. To point the web page at them:
`JOBS_DIR=<DATA_ROOT>/jobs TECH_DIR=<DATA_ROOT>/tech npm run dev`.

**Build result**: SUCCESS when every source worked; UNSTABLE when some source failed (the snapshot is
still saved, with that source's previous results); FAILURE when a run itself broke. Each build archives
`reports/banks.txt`, `reports/tech.txt` and the newest snapshot of each list.

To run the same step by hand: `sh ci/run_job_watch.sh tech data/tech reports`.

## Running tests

```bash
pytest              # offline tests only (HTTP is faked with pytest-httpx)
pytest -m network   # tests that hit the real quotes.toscrape.com
```

## Adding packages

All dependencies are declared in `pyproject.toml`. That file is the single source of truth; there is no `requirements.txt` to keep in sync.

### Runtime dependency (used by code in `src/`)

1. Add it to the `dependencies` list in `pyproject.toml`:

   ```toml
   [project]
   dependencies = [
       "httpx[http2]",
       "parsel",
       "new-package",        # or pin a minimum: "new-package>=1.2"
   ]
   ```

2. Reinstall:

   ```bash
   pip install -e ".[dev]"
   ```

### Dev-only dependency (tests, linters, tooling)

1. Add it to the `dev` list under `[project.optional-dependencies]`:

   ```toml
   [project.optional-dependencies]
   dev = ["pytest", "pytest-httpx", "new-dev-tool"]
   ```

2. Reinstall:

   ```bash
   pip install -e ".[dev]"
   ```

### Optional: pinned `requirements.txt`

If you need an exact, reproducible snapshot of installed versions (e.g. for deployment or CI), generate one from the venv after installing:

```bash
pip freeze --exclude-editable > requirements.txt
```

Regenerate it whenever you change `pyproject.toml`. Don't edit it by hand. Install from it with `pip install -r requirements.txt`.
