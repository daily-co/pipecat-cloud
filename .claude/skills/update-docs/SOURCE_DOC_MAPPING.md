# Source-to-Doc Mapping — pipecatcloud

The profile for the shared `update-docs` skill, which lives in
`pipecat-ai/pipecat` at `.claude/skills/update-docs/SKILL.md` and is published
through the `pipecat-dev-skills` marketplace. `PROFILE_CONTRACT.md` beside it
describes what this file has to provide.

Most of what this package ships is a **command-line surface**, so the mapping is
dominated by command-group-to-page rules rather than the class-to-page rules a
library profile would use.

## Scope

Every `.py` file under `src/pipecatcloud/` is in scope. The package ships two
public surfaces — the `pipecat cloud` CLI and the Python SDK — and both are
documented.

Exclude only:

- `tests/**`
- `__pycache__/`, `*.pyc`, `py.typed`
- `src/pipecatcloud/__version__.py`

Changes outside `src/pipecatcloud/` — examples, CI config, the docs directory —
don't trigger doc updates on their own.

Note that `src/pipecatcloud/__init__.py` is **not** excluded despite being a
re-export file. Its `__all__` is the definition of the SDK's public surface, so
a name added or removed there is a documentation change even when the
implementation moved not at all.

## Skip list

| File | Why |
| --- | --- |
| `_utils/async_utils.py` | `synchronizer` plumbing that turns async commands into blocking ones. No observable surface. |
| `api.py`, `cli/api.py` | The HTTP client. The class is `_API`, underscore-private, and the REST reference is generated from a hand-maintained `openapi.json` in the docs repo with no upstream to diff against. A change here that implies a REST contract change should be **reported as an unmapped finding** rather than edited into the REST pages blind. |
| `cli/__init__.py` | Holds `PIPECAT_CLI_NAME`. Reaches users only through message text already documented elsewhere. |

Nothing else is internal. In particular `_utils/` is not a skip zone — most of
this package's documented behavior lives there.

## Base classes

`pipecatcloud` has no service base classes. Its analogue is the shared console
layer, which every command's output and exit behavior passes through.

| File | Pages to check |
| --- | --- |
| `_utils/console_utils.py` | `api-reference/cli/cloud/output.mdx` first — it defines `OutputMode`, the rich/plain/json rendering, `output_json`, and `require_interactive`, which is where the exit-2-on-no-TTY contract comes from. A change to the table or listing shape also affects every command page that shows example output. |
| `_utils/auth_utils.py` | `api-reference/cli/cloud/output.mdx` (exit behavior when unauthenticated), `pipecat-cloud/guides/personal-access-tokens.mdx` |

## Non-standard locations

| File | Page |
| --- | --- |
| `cli/entry_point.py` | `api-reference/cli/cloud/output.mdx` — global options (`--output`, `--show-cli-config`) and their resolution order |
| `cli/config.py`, `config.py` | `api-reference/cli/cloud/output.mdx` — `PIPECAT_`-prefixed environment variables and config-file keys. `PIPECAT_TOKEN` also appears in `pipecat-cloud/guides/personal-access-tokens.mdx` and `guides/cloud-builds.mdx`. |
| `_utils/deploy_utils.py` | `api-reference/cli/cloud/deploy.mdx` — the `pcc-deploy.toml` reference. `DeployConfigParams` and its nested config classes are the source of truth for every accepted key. |
| `_utils/build_utils.py` | `pipecat-cloud/guides/cloud-builds.mdx` — `DEFAULT_EXCLUSIONS` is enumerated on that page |
| `_utils/github_utils.py` | `api-reference/cli/cloud/github.mdx`, `pipecat-cloud/guides/deploy-from-github.mdx` |
| `_utils/regions.py` | `api-reference/cli/cloud/regions.mdx`, `pipecat-cloud/guides/regions.mdx` |
| `constants.py` | Depends on the constant. Krisp values → `pipecat-cloud/guides/krisp-viva.mdx`. Grep the value itself. |
| `exception.py` | `api-reference/pipecat-cloud/sdk-reference/exceptions.mdx` **and** `pipecat-cloud/fundamentals/error-codes.mdx`. The first documents the classes, the second the `PCC-` codes they carry. |
| `session.py` | `api-reference/pipecat-cloud/sdk-reference/sessions.mdx`, and `api-reference/pipecat-cloud/sdk-reference/examples.mdx` — its first sample constructs a `Session` |
| `agent.py` | `api-reference/pipecat-cloud/sdk-reference/session-arguments.mdx`, and `api-reference/pipecat-cloud/sdk-reference/examples.mdx` — two of its three samples are `bot()` entry points |
| `smallwebrtc/session_manager.py` | `api-reference/pipecat-cloud/sdk-reference/sessions.mdx` |
| `__init__.py` | `api-reference/pipecat-cloud/sdk-reference/overview.mdx` — its Key Components list should name everything in `__all__` |

## Pattern matching

`src/pipecatcloud/cli/commands/<group>.py` →
`api-reference/cli/cloud/<group>.mdx`, with underscores becoming hyphens.

| Source | Page |
| --- | --- |
| `cli/commands/agent.py` | `api-reference/cli/cloud/agent.mdx` |
| `cli/commands/auth.py` | `api-reference/cli/cloud/auth.mdx` |
| `cli/commands/build.py` | `api-reference/cli/cloud/build.mdx` |
| `cli/commands/deploy.py` | `api-reference/cli/cloud/deploy.mdx` |
| `cli/commands/docker.py` | `api-reference/cli/cloud/docker.mdx` |
| `cli/commands/github.py` | `api-reference/cli/cloud/github.mdx` |
| `cli/commands/organizations.py` | `api-reference/cli/cloud/organizations.mdx` |
| `cli/commands/regions.py` | `api-reference/cli/cloud/regions.mdx` |
| `cli/commands/secrets.py` | `api-reference/cli/cloud/secrets.mdx` |
| `cli/commands/spend_limit.py` | `api-reference/cli/cloud/spend-limit.mdx` |

A command group's page is not the whole story. Flags that change how a deploy
behaves usually also appear in a guide — check `pipecat-cloud/fundamentals/` and
`pipecat-cloud/guides/` per Step 7.

### Self-hosted regions — currently being documented separately

`cli/commands/agent_profiles.py` and `cli/commands/registry_keys.py`, and the
self-hosted subcommands within `regions.py` and `secrets.py`
(`regions register|show|delete|enroll-token`, `secrets reference`), have **no
page yet**. They are covered by an in-flight docs PR adding
`pipecat-cloud/self-hosted/`.

Report changes to these as unmapped findings rather than creating pages for
them, until that section exists and this table names it.

## Search

When the tables come up empty, grep `DOCS_PATH` for:

- **CLI files** — the literal command name as the page heading spells it
  (`## agent link`, `### keys revoke`), then the flag string with its dashes
  (`--no-credentials`). Flags are the most reliable anchor: a page that
  documents a command documents its flags.
- **SDK files** — the class name (`AgentStartError`, `SessionParams`).
- **TOML keys** — the bare key (`websocket_auth`, `min_agents`) across
  `api-reference/cli/cloud/deploy.mdx` and the guides.

## Section vocabulary

Two page shapes, and they do not share a vocabulary.

**CLI reference pages** — one `##` heading per subcommand, then:

| Section | Built from | Form |
| --- | --- | --- |
| Usage | the command's argument and option signature | fenced `shell` block |
| Arguments | `typer.Argument(...)` declarations | `<ParamField>` entries |
| Options | `typer.Option(...)` declarations | `<ParamField>` entries, with both long and short forms in `path` (`--organization / -o`) |

A `<ParamField>` `default` must be the Typer default, not what the help text or
spinner displays. `agent logs --level` was documented as defaulting to `ALL`
because that is what the spinner prints; the real default is `None`, and `ALL`
is not a valid value.

**SDK reference pages** — one `##` heading per class, then Constructor
Parameters (`<ParamField>`), Methods (`<ResponseField>`), and Properties.
Exceptions a method raises belong in its `<ResponseField>`.

**`pcc-deploy.toml` keys** live in `api-reference/cli/cloud/deploy.mdx` under
Configuration Options, as `<ParamField>` entries carrying a short TOML example
each. Top-level keys go under Optional Fields; a `[section]` gets its own
`####` heading.

## Guide directories

- `pipecat-cloud/fundamentals/` — deploy, scaling, secrets, active sessions, agent images, error codes, health checks, logging, accounts
- `pipecat-cloud/guides/` — cloud builds, GitHub deploys, CI, telephony, container registries, websockets, regions, Krisp
- `pipecat-cloud/security/` — security and compliance, HIPAA
- `pipecat-cloud/introduction.mdx` — the section landing page; only changes when a whole capability arrives or leaves

These carry a lot of copy-pasteable commands, and a renamed flag breaks every
one of them silently. When a flag changes, grep all three directories for the
old spelling before finishing.

## New pages

### Location and template

A new command group goes at `DOCS_PATH/api-reference/cli/cloud/<group>.mdx`:

````
---
title: "pipecat cloud <group>"
sidebarTitle: "<group>"
description: "pipecat cloud <group> ..., 110-140 chars naming what it manages"
---

[One or two sentences on what the group is for, and when to reach for it.]

## <subcommand>

[What it does.]

**Usage:**

```shell
pipecat cloud <group> <subcommand> [ARGS] [OPTIONS]
```

**Arguments:**

<ParamField path="arg-name" type="string" required>
  [From the typer.Argument help text, expanded.]
</ParamField>

**Options:**

<ParamField path="--flag / -f" type="string">
  [From the typer.Option help text, expanded.]
</ParamField>

[Behavior worth stating: what prompts, what is required in CI, what the command
refuses to do.]
````

### Registration

Add the path, without `.mdx`, to `DOCS_PATH/docs.json` under the CLI tab's
`cloud` group. There is no support-matrix page to update — unlike pipecat, this
is the only registration step.

The group's pages are ordered roughly by workflow rather than alphabetically
(`output` first as the cross-cutting one, then auth, build, deploy, and so on).
Place a new page where a reader would look for it.
