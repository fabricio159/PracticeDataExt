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
```

Run them with `-m` (module path), not `python src/week01_http/scrape.py`, so the `week01_http.*` imports resolve.

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
