# CORSHub

Continuously Operating Reference Station Hub: Python-based NTRIP (V2) caster for aggregating RTK corrections from a network of CORS base stations and distributing them to NTRIP clients.

The network is open. Anyone can connect a rover to receive RTK corrections, or contribute a base station to extend coverage.

---

## Joining the Network

### Caster

```
https://corshub.peinser.com
```

### Anonymous access

You can connect a rover without registering using the credentials `anonymous` / `anonymous`. Anonymous access is limited:

- one concurrent connection
- nearest base station only (mountpoint `*`), no pinning to a specific station
- sessions are time-limited (1 minute)

This is intended for evaluating the network. For production use, register a rover to get a persistent identity, unlimited sessions, and the ability to pin to a specific mountpoint.

---

### How registration works

Access is managed through a pull request to this repository. You add your entry to `ops/values.yaml`, open the PR, and a bot generates your credentials automatically. The password is encrypted with your SSH public key from GitHub and posted as a comment on the PR. Only you can decrypt it.

You do not need to be a collaborator. Fork the repository, make your change, and open the PR from your fork.

No `password_hash` or `github_user` field is required in your entry. The bot fills in the hash; your GitHub identity is taken from whoever opens the PR.

The PR is closed automatically by the bot after credentials are issued. Your entry is committed directly to main by the bot, not by merging your PR. This is intentional: it ensures only the specific entry you added lands on main, with no risk of other files from your fork being merged in.

---

### Joining as a rover

A rover receives RTK corrections from any registered base station. Use the mountpoint `*` to accept corrections from the nearest available base station, or specify a mountpoint name to pin to a specific one.

#### 1. Fork this repository and open `ops/values.yaml`

Add your entry under `opa.registry.rovers`:

```yaml
opa:
  registry:
    rovers:
      your-chosen-username:
        mountpoints: ["*"]
        valid_from: "YYYY-MM-DD"
        valid_until: "YYYY-MM-DD"
```

Replace `your-chosen-username` with whatever NTRIP username you want to use. Set `valid_until` to when you expect to stop using the service (you can always rotate or extend with another PR).

To pin to a specific base station instead of any:

```yaml
        mountpoints: ["MOUNTPOINT_NAME"]
```

See the [available mountpoints](#available-mountpoints) section below.

#### 2. Open a PR using the New NTRIP registration template

[Open a new registration PR](https://github.com/peinser/corshub/compare?template=onboard.md)

#### 3. Wait for the bot

The bot runs automatically when your PR is opened. It validates the PR, issues a credential, commits your entry directly to main, posts your encrypted password as a comment, and then closes the PR. You do not need to wait for a maintainer and you should not try to merge the PR yourself.

#### 4. Decrypt your password

Install [age](https://github.com/FiloSottile/age) if you do not already have it:

```sh
# macOS
brew install age

# Linux
apt install age   # or download from https://github.com/FiloSottile/age/releases
```

The PR comment contains a ready-to-run command with the encrypted block already inlined. Copy and run it, replacing `~/.ssh/your-private-key` with the private key matching one of the fingerprints listed in the comment:

```sh
age -d -i ~/.ssh/your-private-key <<'EOF'
-----BEGIN AGE ENCRYPTED FILE-----
...
-----END AGE ENCRYPTED FILE-----
EOF
```

#### 5. Configure your NTRIP client

| Setting | Value |
|---|---|
| Host | `corshub.peinser.com` |
| Port | `443` |
| TLS | yes |
| Mountpoint | the mountpoint(s) you listed, or any available one |
| Username | the username you chose in `ops/values.yaml` |
| Password | the decrypted password from step 4 |

Store the password securely. It will not be shown again. To get a new one, see [rotating credentials](#rotating-credentials) below.

---

### Joining as a base station

A base station streams RTCM corrections from a fixed antenna to the caster. Other users' rovers can then use your corrections for RTK positioning. The practical range of a single base station is roughly 30-50 km.

#### 1. Choose a mountpoint name

The mountpoint identifies your base station on the caster. Pick something descriptive, for example `BRUSSELS-0` or `GHENT-ROOFTOP`. It must be unique across all registered base stations.

#### 2. Fork this repository and open `ops/values.yaml`

Add your entry under `opa.registry.base_stations`:

```yaml
opa:
  registry:
    base_stations:
      your-chosen-username:
        mountpoint: YOUR_MOUNTPOINT
        valid_from: "YYYY-MM-DD"
        valid_until: "YYYY-MM-DD"
```

The `username` field (the map key) is the NTRIP username your base station software will use to authenticate when pushing corrections. The `mountpoint` field is the name rovers will see and connect to.

#### 3. Open a PR and receive your credentials

Follow steps 2-4 from the rover guide above. The process is identical.

#### 4. Stream corrections to the caster

The `tools/here4-base-caster.py` script handles this automatically for Here4 u-blox receivers. See the [Tools](#tools) section for full usage. For other receivers, configure your NTRIP client in push mode:

| Setting | Value |
|---|---|
| Host | `corshub.peinser.com` |
| Port | `443` |
| TLS | yes |
| Mountpoint | the mountpoint you registered |
| Username | the username you chose |
| Password | the decrypted password |

The connection uses HTTP PUT (NTRIP v2). Ensure your client supports NTRIP v2 and is not buffering the stream.

---

### Rotating credentials

To get a new password for an existing entry, open a PR that removes only the `password_hash` field from your entry in `ops/values.yaml`. Leave everything else unchanged.

[Open a credential rotation PR](https://github.com/peinser/corshub/compare?template=rotate.md)

The bot verifies that the PR is opened by the same GitHub account that originally registered the entry, then issues a new credential. The old password stops working as soon as the PR is merged and the deployment rolls out.

---

### Available mountpoints

The list of active base stations and their approximate locations can be found in `ops/values.yaml` under `opa.registry.base_stations`. The `mountpoint` field of each entry is the name to use in your NTRIP client.

---

## Table of Contents

- [Joining the Network](#joining-the-network)
- [Tools](#tools)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Running Tests](#running-tests)
- [Code Quality](#code-quality)
- [Documentation](#documentation)
- [Docker](#docker)
- [CI/CD](#cicd)
- [Contributing](#contributing)

---

## Tools

### Here4 Base Station Caster (`tools/here4-base-caster.py`)

A terminal tool that turns a [Here4](https://docs.cubepilot.org/user-guides/here-4/here-4-base) u-blox receiver into a live NTRIP v2 base station. It handles the full lifecycle automatically: device discovery, initial configuration, survey-in, and streaming RTCM corrections to a CORSHub caster.

![Here4 Base Station Caster](.github/images/here4-caster.png)

#### How it works

| Phase | What happens |
|-------|-------------|
| **Searching** | Scans serial ports for a u-blox device (USB VID `0x1546` or `/dev/ttyACM*`). |
| **Connecting** | Opens the port at 115 200 baud and enables NAV-PVT, NAV-SAT, and NAV-SVIN messages at 1 Hz. |
| **Monitoring** | Streams live position, satellite C/N0, and accuracy data. Waits for a valid 3D GNSS fix. |
| **Survey-In** | Starts survey-in (CFG-TMODE3). Accumulates observations until the mean position accuracy drops below 2 m for at least 60 s. Enables RTCM 3.3 output messages in parallel (1005, 1074, 1084, 1094, 1124, 1230). |
| **Fixed** | Streams every RTCM correction frame from the serial port to the configured CORSHub mountpoint over NTRIP v2 HTTP PUT. |

The live display refreshes at 2 Hz and shows position, velocity, pDOP, UTC time, survey-in progress, RTCM output statistics, NTRIP caster push status,  and a per-satellite C/N0 table.

#### Usage

```bash
# Survey-in mode (automatic position estimation):
python tools/here4-base-caster.py \
    --caster-url https://corshub.peinser.com \
    --mountpoint HERE4 \
    --username HERE4 \
    --password <password>

# Fixed mode (known surveyed position, best absolute accuracy):
python tools/here4-base-caster.py \
    --lat 50.85034 --lon 4.35171 --alt 65.4 \
    --caster-url https://corshub.peinser.com \
    --mountpoint HERE4 \
    --username HERE4 \
    --password <password>
```

> **Survey-in vs. fixed mode:** Survey-in gives ~2 m absolute base accuracy, which translates to ~2 m absolute rover accuracy (RTK relative accuracy is always centimetre-level regardless). For sub-metre absolute accuracy, place the antenna on a surveyed mark and supply `--lat`, `--lon`, `--alt`.

#### Dependencies

All required packages are included in the project's main dependency set (`aiohttp`, `pyubx2`, `pyrtcm`, `pyserial`, `rich`). No separate install step is needed if the project virtualenv is active.

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

### Option A: Dev Container (recommended)

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

### Option B: Local Setup

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
uv add <package>          # Runtime dependency
uv add --dev <package>    # Development-only dependency
make lock                 # Update uv.lock
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

1. **Ruff** - covers flake8, isort, pyupgrade, bugbear, and more.
2. **MyPy** - strict type checking across `src/corshub/` and `tests/`.

### Security scan

```bash
uv run bandit -r src/
```

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

## TODO

- [ ] Add token bucket rate-limiter for auth based on connection fingerprint

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide.

---

## Attribution

Thanks to [SEMU Consulting](https://github.com/semuconsulting) for their excellent GEO Python libraries.

---