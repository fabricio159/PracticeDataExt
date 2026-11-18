# DataExtProject

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`-e` installs the project in editable mode, so code in `src/` (e.g. `week01_http`) is importable and changes take effect without reinstalling.

## Running tests

```bash
pytest
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
