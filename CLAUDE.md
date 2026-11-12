# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

**Pipecat Cloud** is the Python SDK and CLI for deploying and managing Pipecat voice agents on Pipecat Cloud infrastructure. It ships to PyPI as `pipecatcloud`. Users authenticate, build and push container images, deploy agents, manage secrets and scaling, and open sessions against running agents. The CLI is not a standalone command — its commands are exposed through the Pipecat CLI as `pipecat cloud ...`.

- Package root: `src/pipecatcloud/`
- CLI: registered as a Pipecat CLI extension via the `pipecat_cli.extensions:cloud` entry point (surfaced as `pipecat cloud`). There is no standalone entry point — no `[project.scripts]` and no `__main__`; the CLI runs only through the Pipecat CLI.
- Public docs: https://docs.pipecat.ai/pipecat-cloud

## Toolchain

- **Python 3.11+** (bumped from 3.10 in v0.5.0 to match Pipecat 1.0). Type hints use `X | Y` syntax, not `Union[X, Y]`.
- **uv** for dependency management. `uv.lock` is committed.
- **setuptools** build backend. Version is derived from git tags via `setuptools_scm` (tag format `v0.6.0`), so there is no hand-edited version file.
- **Ruff** for lint and format (100-char line length, `I` and `UP` rule sets).
- **Pyright** for type checking.
- **Pytest** with `pytest-asyncio` in strict mode: an async test needs `@pytest.mark.asyncio`. (`pytest.ini` asks for auto mode, but see Testing conventions.)

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
  agent.py               Session argument types, subclasses of pipecat-ai's runner types
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
pcc-deploy.toml          Example deployment config consumed by `pipecat cloud deploy`
```

## Testing conventions

- Layout mirrors `src/`. New tests go in `tests/test_<module>.py`.
- `tests/conftest.py` sets `PIPECAT_CONFIG_PATH` to an isolated temp file **before imports**, pre-populated with `token = "test-token"` and `org = "test-org"`. This prevents tests from reading or clobbering real credentials. Do not bypass it.
- Async tests use `@pytest.mark.asyncio`. Mock async calls with `unittest.mock.AsyncMock`.
- `pytest.ini` opens with `[tool:pytest]`, the `setup.cfg` spelling, so pytest reads none of its settings (the file still marks the rootdir): its `--import-mode=importlib`, `asyncio_mode = auto` and `testpaths` do not apply, and pytest runs with its defaults. Some tests manually insert `src/` onto `sys.path`, which is expected.

## CI

- `tests.yml` runs the full suite (`uv run pytest -v`) on push to main and PRs, and runs `tests/test_agent_args.py` again with pipecat-ai 1.11.0 and 1.5.0 layered over the lock (`uv run --with ... python -m pytest`, with `PCC_EXPECTED_PIPECAT_AI` so the tests confirm the version they run on).
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
- The session argument types in `agent.py` subclass pipecat-ai's runner types and need pipecat-ai (>= 1.0.0); there are no fallback definitions. `MOQSessionArguments` needs 1.12.0 (`MOQRunnerArguments.relay_url`), so it is imported and defined under its own guard: on older pipecat-ai the module still imports and only that name is missing. It stays out of `__all__` for the same reason.
- Krisp VIVA audio filter names are `Literal["tel", "pro"]` in `constants.py`. These must match the server-side ConfigMap, so coordinate changes with the backend.
- Never log a request or response body, a header, or an exception's text: they can hold secret values and tokens. Use `_utils/log_utils.py`. `tests/test_log_redaction.py` fails on a log call that reads a name or attribute it knows can hold one, however it is formatted, or that uses `logger.opt`, `logger.exception` or `logger.catch`. Only `describe_*` and `len` calls are exempt.

## Changelog entries

Don't edit `CHANGELOG.md` in a feature PR. Each PR with a user-facing change adds **changelog fragments** to `changelog/`, which towncrier assembles at release time, so parallel PRs never conflict on the changelog. The `pipecat-dev:changelog` skill writes them.

- Name a fragment `<PR number>.<type>.md`, with type one of `added`, `changed`, `deprecated`, `removed`, `fixed`, `performance`, `security`, `other`. A second entry of the same type is `<PR number>.<type>.2.md`.
- The content is one `- ` bullet on a single line, no wrapping, starting with ⚠️ for a breaking change. Code blocks and sub-bullets go below it, indented two spaces. The release section links each entry to its PR.
- Skip a fragment for docs, tests, CI, internal refactors, and fixes to changes that haven't shipped (edit that change's fragment instead).
- Preview the next section with `uv run towncrier build --draft --version X.Y.Z`. Don't run it without `--draft`: it rewrites `CHANGELOG.md`, deletes the fragments and stages both.

## Release flow

1. Pick the version by semantic versioning from the pending fragments: a breaking change (its entry starts with ⚠️) bumps major, `added` or `deprecated` bumps minor, otherwise patch. A `removed` entry is not breaking by itself: 1.4.0 and 1.6.0 removed things that never worked.
2. Run the **Generate Changelog for Release** workflow (`generate-changelog.yml`) with the version. It builds the `CHANGELOG.md` section, deletes the fragments and opens a `Release X.Y.Z` PR with a preview of the release notes. Fix wording on that branch, then merge it.
3. Tag the merge commit with `v<MAJOR>.<MINOR>.<PATCH>` and push the tag.
4. Trigger `publish-pypi.yml` manually with that tag. `setuptools_scm` picks up the version from the tag.

After publishing to PyPI, the workflow creates (or updates) the GitHub Release for the tag. Its notes are the tag's `CHANGELOG.md` section, reformatted by `scripts/release-changelog.py`, so fix release-note wording in the changelog rather than on GitHub: a re-run overwrites edits made there. Preview the notes locally with `uv run python scripts/release-changelog.py --no-heading 1.3.0`.

## Writing style for generated content in this repo

- No emdashes.
- Clear mid-length sentences. Short paragraphs.
- Bold for key terms on first use only.
- Don't hedge, don't hype, don't pad with "let's dive in" style openers.
