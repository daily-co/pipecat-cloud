# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

**Pipecat Cloud** is the Python SDK and `pcc` CLI for deploying and managing Pipecat voice agents on Pipecat Cloud infrastructure. It ships to PyPI as `pipecatcloud`. Users install it to authenticate, build and push container images, deploy agents, manage secrets and scaling, and open sessions against running agents.

- Package root: `src/pipecatcloud/`
- CLI entry points: `pcc` and `pipecatcloud` (also registered as a Pipecat CLI extension via the `pipecat_cli.extensions:cloud` entry point)
- Public docs: https://docs.pipecat.daily.co

## Toolchain

- **Python 3.11+** (bumped from 3.10 in v0.5.0 to match Pipecat 1.0). Type hints use `X | Y` syntax, not `Union[X, Y]`.
- **uv** for dependency management. `uv.lock` is committed.
- **setuptools** build backend. Version is derived from git tags via `setuptools_scm` (tag format `v0.6.0`), so there is no hand-edited version file.
- **Ruff** for lint and format (100-char line length, `I` and `UP` rule sets).
- **Pyright** for type checking.
- **Pytest** with `pytest-asyncio` in `auto` mode.

## Common commands

Always use `uv run` rather than activating a venv.

```bash
# First-time setup
uv sync --group dev
uv run pre-commit install

# Tests
uv run pytest                                    # full suite
uv run pytest tests/test_auth.py                 # single file
uv run pytest tests/test_auth.py::TestPKCE       # single class
uv run pytest -v                                 # verbose

# Lint / format / typecheck
uv run ruff format .
uv run ruff check --fix .
uv run pyright
bash scripts/fix-ruff.sh                         # format + check --fix

# Build (usually CI-only)
uv build
```

Pre-commit runs `ruff format` and `ruff check --fix` on every commit. If the hook fails, fix the issue and make a **new** commit rather than amending.

## Repository layout

```
src/pipecatcloud/
  __init__.py            Public SDK surface (Session, exceptions, session argument types)
  api.py                 Async aiohttp API client (_API class)
  session.py             User-facing Session client
  agent.py               Session argument types with fallbacks when pipecat-ai is absent
  config.py              Base settings, API path constants
  constants.py           Krisp VIVA models, region-related literals
  exception.py           Error hierarchy (AuthError, AgentStartError, etc.)
  cli/
    entry_point.py       Typer app, wired as a Pipecat CLI extension
    config.py            Reads/writes ~/.config/pipecatcloud/pipecatcloud.toml
    api.py               CLI-flavored wrapper around _API
    commands/            auth, agent, deploy, docker, build, secrets, regions, organizations
  _utils/
    auth_utils.py        @requires_login decorator, default-org resolution
    build_utils.py       Deterministic tarballs, S3 upload, build polling
    deploy_utils.py      pcc-deploy.toml parsing, deployment-status interpretation
    regions.py           Region fetch and validation
    async_utils.py       synchronizer for bridging async into the sync Typer CLI
    console_utils.py     Rich console helpers
  smallwebrtc/
    session_manager.py   SmallWebRTC support for non-Pipecat agents
tests/                   Pytest suite, parallel to src/
.github/workflows/       tests.yml, format.yml, publish-pypi.yml, publish-test.yml
pcc-deploy.toml          Example deployment config consumed by `pcc deploy`
```

## Testing conventions

- Layout mirrors `src/`. New tests go in `tests/test_<module>.py`.
- `tests/conftest.py` sets `PIPECAT_CONFIG_PATH` to an isolated temp file **before imports**, pre-populated with `token = "test-token"` and `org = "test-org"`. This prevents tests from reading or clobbering real credentials. Do not bypass it.
- Async tests use `@pytest.mark.asyncio`. Mock async calls with `unittest.mock.AsyncMock`.
- `tests/test_agent_sessions.py` is **skipped in CI** because it needs real auth or heavier mocking. Run it locally if you touch session code.
- `pytest.ini` uses `--import-mode=importlib`. Some tests manually insert `src/` onto `sys.path`, which is expected.

## CI

- `tests.yml` runs on push to main and PRs. It currently runs `uv run pytest tests/test_docker_commands.py -v` rather than the full suite, so green CI does not guarantee the whole suite passes. Run `uv run pytest` locally before opening PRs.
- `format.yml` runs `ruff format --diff` and `ruff check`. Both must pass.
- `publish-pypi.yml` and `publish-test.yml` are manual dispatches that build and publish a specific git tag.

## Config and environment

- User config lives at `~/.config/pipecatcloud/pipecatcloud.toml`. Overridable with `PIPECAT_CONFIG_PATH`.
- Deployment config is `pcc-deploy.toml` in the project root. Overridable with `PIPECAT_DEPLOY_CONFIG_PATH`.
- Other overrides follow the `PIPECAT_<UPPERCASE_SETTING>` pattern (e.g. `PIPECAT_API_HOST`).
- `PCC_LOG_LEVEL` controls the loguru log level (default `INFO`). This codebase uses **loguru**, not the stdlib `logging` module.
- The credentials file is force-chmod'd to `0600` on read and written atomically (temp file, fsync, rename). Preserve this when touching `cli/config.py`.

## Gotchas

- The CLI is sync (Typer) but the API client is async. Bridge with `@synchronizer` from `_utils/async_utils.py`. Do not invent a new bridging pattern.
- Auth uses OAuth2 with PKCE (RFC 7636). OAuth endpoints are discovered from the API server, not hardcoded. The callback server tries ports 8400-8404.
- `SmallWebRTCSessionArguments` and `SmallWebRTCRunnerArguments` have fallback definitions in `agent.py` for when `pipecat-ai` isn't installed. Keep the two paths in sync.
- Krisp VIVA audio filter names are `Literal["tel", "pro"]` in `constants.py`. These must match the server-side ConfigMap, so coordinate changes with the backend.

## Release flow

1. Update `CHANGELOG.md` following Keep a Changelog format.
2. Tag the commit with `v<MAJOR>.<MINOR>.<PATCH>`.
3. Trigger `publish-pypi.yml` manually with that tag. `setuptools_scm` picks up the version from the tag.

## Writing style for generated content in this repo

- No emdashes.
- Clear mid-length sentences. Short paragraphs.
- Bold for key terms on first use only.
- Don't hedge, don't hype, don't pad with "let's dive in" style openers.
