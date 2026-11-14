# Contributing to Cladia

Cladia is a Python/FastAPI desktop application with a JavaScript frontend.
The desktop window uses pywebview; Linux analysis tools run through WSL 2.
Read the [current priorities](docs/PROJECT_REVIEW.md) before choosing larger work.

## Local setup

On Windows, follow the [installation guide](docs/INSTALLATION.md). `setup.bat`
creates `%LOCALAPPDATA%\Cladia\venv`. From the repository root, you can run the
application explicitly in PowerShell:

```powershell
& "$env:LOCALAPPDATA\Cladia\venv\Scripts\python.exe" -m cladia
```

For a browser window instead of the desktop shell, add `--browser`. Native file
dialogs and desktop-window integration still need testing in the desktop app.
Use a separate data directory for development so experiments cannot affect your
normal library:

```powershell
$env:CLADIA_DATA = Join-Path $env:TEMP "cladia-dev-library"
& "$env:LOCALAPPDATA\Cladia\venv\Scripts\python.exe" -m cladia --browser
```

Keep the local application server private; it is not a hosted multi-user service.

## Running checks

`requirements.txt` pins what the application runs on (the versions tested; `setup.bat`
installs them). The checks also need `requirements-dev.txt`:

```powershell
& "$env:LOCALAPPDATA\Cladia\venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
```

Run the Python/API and JavaScript model checks from the repository root:

```powershell
& "$env:LOCALAPPDATA\Cladia\venv\Scripts\python.exe" -m pytest -q
```

`tests/test_js.py` runs the JavaScript checks through Node.js. Without Node,
those checks are skipped; a Python-only pass does not validate viewer mechanics.
Most analysis tests use controlled runners rather than executing the installed
scientific tools. A real tool run is still needed when changing an algorithm’s
installation or execution path.

Real-browser regression scripts live in `scripts/test_*_ui.cjs`. They require
Node.js, Playwright and Chrome. For example, with Playwright available to Node:

```powershell
$env:CLADIA_PYTHON = "$env:LOCALAPPDATA\Cladia\venv\Scripts\python.exe"
$env:CLADIA_CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"
node scripts/test_tree_ui.cjs
```

If Playwright is installed outside the normal Node module search path, set
`NODE_PATH` to its containing `node_modules` directory. The browser scripts
start private servers and temporary libraries; they do not connect to your
running app. Read a script before running it: tool-specific checks can build or
run native tools. Relevant checks include:

| Area | Script |
| --- | --- |
| Folder navigation, keyboard/drag behavior, mutation guards and persistence | `scripts/test_folders_ui.cjs` |
| Tree selection, search markers, zoom and saved choices | `scripts/test_tree_ui.cjs` |
| Shared viewer zoom controls | `scripts/test_viewer_zoom_ui.cjs` |
| Genome figures | `scripts/test_genome_figure_ui.cjs` |
| GC-content and skew tracks | `scripts/test_composition_ui.cjs` |
| Settings, tool grid and Trash | `scripts/test_settings_ui.cjs` |
| Metadata import and project ZIPs | `scripts/test_project_ui.cjs` |
| Generated tool dialogs | `scripts/test_engine_ui.cjs` |
| BLAST Local/Online tabs | `scripts/test_blast_ui.cjs` |
| Result destinations | `scripts/test_destinations_ui.cjs` |
| Optional tools from bioconda (real install, run, uninstall) | `scripts/check_conda_tools.py` |

## Code organization

| Location | Responsibility |
| --- | --- |
| `cladia/__main__.py`, `cladia/window.py` | Startup, desktop windows and native dialogs |
| `cladia/server.py`, `cladia/api_*.py`, `cladia/project_api.py` | HTTP composition and routes |
| `cladia/library.py`, `cladia/history.py`, `cladia/projects.py` | Library storage, saved versions and project transfer |
| `cladia/jobs.py`, `cladia/execution.py`, `cladia/job_progress.py`, `cladia/job_outputs.py` | Job lifecycle, execution, progress and publication |
| `cladia/tool_registry.py`, `cladia/tool_jobs.py`, `cladia/tool_validation.py` | Operation definitions, input preparation and validation |
| `cladia/tool_plugins/` | Engines (built-in and optional) and their scientific workflows; `locks/` the optional tools' bioconda lock files |
| `cladia/engine_api.py` | Public host services available to engines |
| `cladia/conda.py`, `cladia/tool_install.py`, `scripts/make_conda_lock.py` | Optional tools: download from bioconda, install, update, uninstall; making lock files |
| `cladia/linux.py`, `containers/` | Managed Linux environments and tool installation |
| `cladia/static/` | Application UI (including `app-folders.js` for navigation), dedicated viewers and figure editors |
| `scripts/`, `tests/` | Packaging, browser checks and automated tests |

Keep tool logic within its engine and use the public engine API for host
services. See the [integration guide](docs/FEATURE_GUIDE.md) and
[optional tools from bioconda](docs/PLAN_BIOCONDA_TOOLS.md). Extract shared host
responsibilities when needed; avoid splitting files solely to meet a line count.

## Preparing a change

Explain the resulting behavior, update the relevant user documentation, and run
checks appropriate to the affected area. Exercise zoom, selection, scrolling and
search together when modifying a viewer. Preserve saved libraries, history,
attachments and source links; use scratch data for destructive or failure tests.

An optional tool's new version is a new lock file (`scripts/make_conda_lock.py`)
committed with the engine change; installed copies then offer **Update**.

Historical design plans document decisions made at the time. Their implemented
sections are not a backlog; use the current project review for outstanding work.

## Third-party attribution

Scientific tools and their dependencies retain their own licenses and citation
requirements; consult their source projects (optional tools are downloaded from
bioconda, not redistributed by Cladia) and the
[tool audit](docs/TOOL_VERSION_AUDIT.md). The CDS translation colour palette in
`cladia/static/app-viewer.js` comes from
[OpenVectorEditor](https://github.com/TeselaGen/openVectorEditor) (MIT, TeselaGen).
