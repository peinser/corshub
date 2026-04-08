# CORSHub

Continuously Operating Reference Station Hub - Python-based NTRIP (V2) caster for aggregating RTK corrections from a network of CORS base stations and distributing them to NTRIP clients.

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Running Tests](#running-tests)
- [Code Quality](#code-quality)
- [Documentation](#documentation)
- [Docker](#docker)
- [CI/CD](#cicd)
- [Publishing to PyPI](#publishing-to-pypi)
- [Contributing](#contributing)

---

## Features

| Area | Tooling |
|---|---|
| Package manager | [uv](https://github.com/astral-sh/uv) — fast, lock-file-based |
| Linting & formatting | [Ruff](https://github.com/astral-sh/ruff) |
| Type checking | [MyPy](https://mypy-lang.org/) (strict mode) |
| Testing | [pytest](https://pytest.org) + [pytest-asyncio](https://pytest-asyncio.readthedocs.io) + [pytest-cov](https://pytest-cov.readthedocs.io) |
| Security scanning | [Bandit](https://bandit.readthedocs.io) |
| Documentation | [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) with auto-generated API reference |
| Changelog | [towncrier](https://towncrier.readthedocs.io) |
| Build system | [Hatchling](https://hatch.pypa.io) |
| Containerization | Multi-stage Docker build (validate → production) |
| CI/CD | GitHub Actions (docs, Docker image, PyPI publish) |
| Dev environment | VS Code Dev Container with `act` for local workflow testing |

---

## Prerequisites

**For Option A (Dev Container):**
- [Docker](https://docs.docker.com/get-docker/) (Desktop or Engine)
- [VS Code](https://code.visualstudio.com/) with the [Dev Containers extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)

**For Option B (local):**
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

---

## Getting Started

### Option A — Dev Container (recommended)

1. **Clone the repository:**

   ```bash
   git clone git@github.com:peinser/corshub.git
   cd corshub
   ```

2. **Open in VS Code and reopen in container:**

   ```
   Ctrl+Shift+P  →  Dev Containers: Reopen in Container
   ```

   VS Code will build the container image and run the post-creation script, which installs all dependencies automatically via `uv sync --locked`.

3. **Verify the setup:**

   ```bash
   make help
   ```

### Option B — Local Setup

1. **Clone the repository:**

   ```bash
   git clone git@github.com:peinser/corshub.git
   cd corshub
   ```

2. **Install uv** (if not already installed):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. **Install dependencies:**

   ```bash
   make setup
   ```

4. **Verify the setup:**

   ```bash
   make help
   ```

---

## Project Structure

```
corshub/
├── .changelog/          # Pending changelog entries (towncrier)
├── .devcontainer/       # VS Code dev container definition
├── .github/
│   ├── dependabot.yml   # Automated dependency updates (weekly)
│   └── workflows/
│       ├── docs.yml     # Build and deploy documentation to GitHub Pages
│       ├── image.yml    # Build and push Docker image to registry
│       └── pypi.yml     # Build and publish package to PyPI
├── docker/
│   ├── Dockerfile       # Multi-stage build: builder → validate → production
│   └── entrypoint.sh    # Container entrypoint
├── docs/                # MkDocs source
├── examples/            # Usage examples (add yours here)
├── src/
│   └── corshub/   # Package source code
│       ├── __init__.py
│       └── __version__.py
├── tests/               # pytest test suite
├── CHANGELOG.md         # Auto-generated changelog
├── CONTRIBUTING.md      # Contribution guidelines
├── Makefile             # Common development tasks
├── mkdocs.yml           # Documentation configuration
├── pyproject.toml       # Project metadata, dependencies, and tool config
└── uv.lock              # Locked dependency versions (do not edit manually)
```

---

## Development Workflow

All common tasks are available through `make`. Run `make help` to see the full list.

| Command | Description |
|---|---|
| `make setup` | Install all dependencies (first-time setup) |
| `make sync` | Re-sync dependencies after editing `pyproject.toml` |
| `make lock` | Update `uv.lock` after adding or removing dependencies |
| `make format` | Auto-format code with Ruff |
| `make lint` | Run Ruff (linter) and MyPy (type checker) |
| `make test` | Run the test suite with coverage |
| `make clean` | Remove build artefacts and caches |
| `make all` | Full local CI pipeline: clean → install → lint → test |

### Adding a dependency

```bash
uv add <package>          # runtime dependency
uv add --dev <package>    # development-only dependency
make lock                 # update uv.lock
```

---

## Running Tests

```bash
make test
```

This runs `pytest` with branch coverage enabled. A minimum of **75%** coverage is required. To view a detailed HTML report:

```bash
uv run pytest --cov=src --cov-report=html
open htmlcov/index.html
```

Tests requiring async support use `pytest-asyncio`. Mark async test functions with `@pytest.mark.asyncio`.

---

## Code Quality

### Format

```bash
make format
```

### Lint

```bash
make lint
```

Runs two checks in sequence:

1. **Ruff** — covers flake8, isort, pyupgrade, bugbear, and more.
2. **MyPy** — strict type checking across `src/corshub/` and `tests/`.

### Security scan

```bash
uv run bandit -r src/
```

---

## Documentation

### Serve locally

```bash
uv run --group docs mkdocs serve
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### Build

```bash
uv run --group docs mkdocs build
```

### Deploy

Documentation is deployed to GitHub Pages automatically by the `docs.yml` workflow on every push to `main`.

---

## Docker

| Stage | Purpose |
|---|---|
| `builder-base` | Installs locked dependencies (no dev extras) |
| `validate` | Runs format check, Ruff, MyPy, pytest, and Bandit |
| `production` | Minimal runtime image; runs as a non-root user (UID 1001) |

```bash
# Run only the validation stage
docker build --target validate -f docker/Dockerfile .

# Build the final production image
docker build -f docker/Dockerfile -t corshub:local .

# Run
docker run --rm corshub:local
```

---

## CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| `docs.yml` | Push to `main` (docs/src/mkdocs) or manual | Builds and publishes documentation to GitHub Pages |
| `image.yml` | Push to `main`/`development` (docker/src/tests) or manual | Validates and builds the Docker image, pushes to registry |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide.
