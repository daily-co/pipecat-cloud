# Pipecat Cloud Changelog

All notable changes to **Pipecat Cloud** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.13] - 2025-12-03

### Added

- Added `pcc organizations properties` commands for managing organization-level
  configuration:

  - `properties list` - Display current property values
  - `properties set <name> <value>` - Update a property value
  - `properties schema` - Show available properties with metadata and allowed
    values

- Added `pcc organizations default-region [region]` convenience command to get or
  set the organization's default region.

### Changed

- Region is now optional for `secrets set`, `secrets image-pull-secret`, and
  `deploy` commands. When `--region` is not specified, the API now uses the
  organization's configured default region instead of the CLI defaulting to
  `us-west`. The confirmation display shows the actual region that will be used
  (e.g., "us-west (organization default)").

- Removed deprecation warnings about region defaulting to `us-west`. The
  organization's default region is now the source of truth.

## [0.2.12] - 2025-11-26

### Added

- Added regional support for secrets and services/agents. Services and secrets
  must be in the same region. Available regions are fetched dynamically from
  the API and can be viewed with `pcc regions list`.

- Added `pcc regions list` command to display available regions with their
  display names (e.g., "US West (Oregon)", "Europe (Frankfurt)",
  "Asia Pacific (Mumbai)").

- Added `--region` / `-r` option to the `secrets set` command to specify the
  region when creating or updating secret sets.

- Added `--region` / `-r` filter option to the `secrets list` command to filter
  secret sets by region. The Region column is now displayed in the output
  table.

- Added `--region` / `-r` option to the `secrets image-pull-secret` command to
  specify the region for image pull secrets.

- Added `--region` / `-r` option to the `deploy` command to specify the region
  for service deployments. Region can also be configured via `pcc-deploy.toml`
  by adding the `region` attribute. The region is displayed in the deployment
  review panel.

- Added `--region` / `-r` filter option to the `agent list` command to filter
  agents by region. The Region column is now displayed in the output table.

- Added region validation that checks user-provided region codes against the
  live API region list, with helpful error messages showing available regions.

### Changed

- When the `--region` option is not specified for create/update operations
  (secrets, deployments), the CLI now defaults to `us-west` with a deprecation
  warning. Users are encouraged to explicitly specify the region as this
  default will be required in a future version.

- All error messages reference the Pipecat CLI (e.g. `pipecat cloud`) in place
  of `pipecatcloud` or `pcc`.

### Fixed

- Improved error handling when building a docker image on an x86 machine.

## [0.2.11] - 2025-11-12

### Added

- Added an option to retrieve logs by session ID using `--session-id` / `-s`
  with the `agent logs` command.

### Changed

- Bumped the `fastapi` dependency's upperbound to `<1.0.0`.

## [0.2.10] - 2025-10-25

### Removed

- The `init` command was removed. Use the `pipecat-ai-cli`'s `init` command to
  scaffold your project.

## [0.2.9] - 2025-10-24

### Added

- Added Krisp VIVA audio filtering support with the `--krisp-viva-audio-filter`
  option to the `deploy` command. Supports `tel` (telephone) and `pro` (professional)
  audio filter models. Can also be configured via `pcc-deploy.toml` by adding a
  `[krisp_viva]` section with `audio_filter = "tel"` or `audio_filter = "pro"`.

- `agent status` now displays the Krisp VIVA configuration state, showing whether
  it's enabled and which audio filter model is active.

## [0.2.8] - 2025-10-24

### Added

- Register pipecat cloud commands as `pipecat-cli` extensions.

## [0.2.7] - 2025-10-21

### Added

- Added a `stop` command to `pcc agent` that allows stopping an ongoing Pipecat
  Cloud session.

### Changed

- Fallback `RunnerArguments` now include a `body` field in the base class.

### Deprecated

- The `pcc init` command is now deprecated and will be removed in a future
  version. Use the [Pipecat CLI](https://github.com/pipecat-ai/pipecat-cli)
  instead.

## [0.2.6] - 2025-10-09

### Added

- Added an optional `--profile` option to the `deploy` command which selects between
  `agent-1x`, with 0.5 vCPUs / 1 GB RAM, and `agent-2x`, with 1 vCPU / 2 GB RAM. The
  default profile is `agent-1x`.

### Fixed

- Fixed an issue where the `agent status` CLI command would return incorrect
  values for Min and Max Agents.

## [0.2.5] - 2025-10-02

### Added

- Added a `body` parameter to the fallback type, `WebsocketRunnerArguments`.

- Added `SmallWebRTCSessionManager`, a Session Manager to simplify bot creation
  in Pipecat Cloud using `SmallWebRTCTransport`.
- Added a `SmallWebRTCSessionArguments` dataclass.

- `deploy` command now accepts a `--enable-managed-keys` flag which enables
  properly configured agents to use Daily's API keys for supported upstream
  services. This can also be enabled via `pcc-deploy.toml` by adding the
  `enable_managed_keys` attribute and a boolean value.

- `agent status` now reflects the state of the managed keys feature.

## [0.2.4] - 2025-08-26

### Added

- Added `docker build-push` command for building, tagging, and pushing Docker
  images. Automatically parses registry information from the `image` field in
  `pcc-deploy.toml` and supports both Docker Hub and custom registries. Includes
  real-time build output and helpful authentication error hints.

### Changed

- `pcc init` now points to https://github.com/pipecat-ai/pipecat-quickstart.

## [0.2.3] - 2025-08-19

### Fixed

- Fixed an issue where DailySessionArguments required positional args for
  `handle_sigint`, `handle_sigterm`, and `pipeline_idle_timeout_secs`.

## [0.2.2] - 2025-08-18

### Added

- Added `RunnerArguments` as a fallback type in the event that the Pipecat
  development runner is not imported.

### Changed

- Updated the `typer` dependency to support a range of versions in order to
  resolve conflicts with other packages. The new range supported is:
  `"typer>=0.15.3,<0.17.0"`.

- Relaxed the package requirements for `python-dotenv` and `uvicorn` to make it
  easier to work with Pipecat Cloud. The new supported versions are:

  - `"python-dotenv>=1.0.1,<2.0.0"`
  - `"uvicorn>=0.32.0,<1.0.0"`

- Updated all types to inherit from `RunnerArguments` to align the types with
  the Pipecat development runner types. This adds types:

  - `handle_sigint`
  - `handle_sigterm`
  - `pipeline_idle_timeout_secs`

### Fixed

- Fixed `ZeroDivisionError` in `agent sessions` command when calculating
  metrics for agents with zero sessions.

- Fixed `UnboundLocalError` in `agent sessions` command where
  `metric_renderables` was referenced before assignment due to earlier logic
  failures.

- `agent sessions` command previously threw `AttributeError` when no agent name
  was provided via command line or `pcc-deploy.toml`, it now exits gracefully
  with a clear error message.

## [0.2.1] - 2025-08-02

### Added

- Added a `pipecatcloud[pipecat]` extra for installing `pipecat-ai` dependency.

### Changed

- Session argument types now inherit from pipecat-ai runner types when available.
  `DailySessionArguments` and `WebSocketSessionArguments` now inherit from
  `pipecat.runner.types` when `pipecat-ai>=0.0.77` is installed to provide
  compatibility with the pipecat-ai development runner.

- Add clarifying information to the `pcc secrets image-pull-secret` setup.

- Modified the `aiohttp` minimum version to `3.11.12` and expanded `fastapi` to
  `>=0.115.6,<0.117.0` in order to align with `pipecat-ai`.

### Deprecated

- When `pipecat-ai` is not installed, session arguments fall back to standalone
  implementations. This fallback behavior is deprecated and will be removed in
  a future version. Install with `pip install pipecatcloud[pipecat]` for the
  preferred implementation.

## [0.2.0] - 2025-07-09

### Changed

- `deploy` command now requires valid image pull credentials (`--credentials`). Most repositories and use-cases require authorized image pulls, so this change aims to guide correct usage.
  - Deploying without credentials can be achieved with the `--no-credentials` / `-nc` or `--force` flags.
  - It is always recommend to provide a an image pull secret as part of your deployment.

## [0.1.8] - 2025-07-09

### Changed

- `deploy` command now shows a warning when image pull credentials are not provided.

### Added

- Add py.typed marker for static type checking support.

## [0.1.7] - 2025-06-12

### Added

- `agent sessions` command lists session history and various statistics (avg. start times, cold starts etc.)

### Fixed

- Bumped `typer` dependency to `0.15` to fix errors when using `--help` flag.

## [0.1.6] - 2025-04-25

### Changed

- `min-instances` and `max-instances` has been changed to reflect API terminology changes.

## [0.1.5] - 2025-04-09

### Added

- `deploy` command now accepts a `--enable-krisp / -krisp` which enables Krisp integration for your pipeline.

### Changed

- `start` command now takes agent name from `pcc-deploy.toml` where applicable
- API error handling now checks `response.ok` and properly checks for error codes for all non-ok responses

### Fixed

- REST and cURL requests now render errors correctly

## [0.1.4] - 2025-03-28

### Changed

- `deploy` now shows a confirmation when `min-instances` is greater than 0 to assert usage will be billed.

### Fixed

- `auth login` now accepts a `--headless` / `-h` flag to skip automatic browser opening during authentication. This is particularly useful for:
  - Systems running in headless environments
  - WSL installations where browser context may not match the terminal
  - CI/CD pipelines
  - Users who prefer to manually copy and paste the authentication URL

## [0.1.3] - 2025-03-13

### Fixed

- `deploy` now correctly handles error states returned by the API

- `deploy` checks revision status vs. general service ready status (when updating)

### Added

- `agent logs` now accepts a `--deployment_id` / `-d` argument for filtering
  by specific deployment ID

- `agent start` now accepts `--daily-properties` / `-p` for customizing Daily
  room settings when used with `--use-daily`.

- Added `daily_room_properties` to `SessionParams` in SDK for configuring Daily
  rooms when creating sessions.

- Added an export for the `PipecatSessionArguments` class.

### Fixed

- Fix an issue where custom `data` resulted in an agent not starting.

- Fixed an issue where the link returned by `pcc agent start` was not clickable
  in IDEs.

## [0.1.2] - 2025-03-12

### Added

- `agent.py` data classes for use in base images, providing guidance on params.

### Fixed

- Lookup issue when passing an image pull secret to the `deploy` command.

### Changed

- Change the of deploy checks from 30 to 18, reducing the overall time for a
  deployment.

- Added a `--format / -f` option for `agent logs`. Options are `TEXT` and
  `JSON`.

- Improved error messaging for `ConfigError` to improve debugging.

## [0.1.0] - 2025-03-05

- `pipecatcloud.toml` moved to `$HOME/.config/pipecatcloud/pipecatcloud.toml`.

### Added

- `pcc auth whoami` now shows the namespace Daily API key for convenience.

## [0.0.11] - 2025-03-04

### Changed

- `session.py` now returns the response body from the `start()` method.

### Fixed

- Fixed an issue in `session.py` where a bot wouldn't start due to malformed
  `data`.

## [0.0.10] - 2025-03-04

### Added

- `init` convenience command will now populate the working directory with files
  from the starter project.

- `agent log` allows for optional severity level filtering.

### Changed

- `agent status` and `deploy` no longer show superfluous data.

- `session.py` now correctly handles errors when starting agents.

- `secrets set` no longer prompts twice for confirmation if the secret set does
  not exist.

### Removed

- `errors.py` removed as redundant (error message and code returned via API).

- `agent_utils.py` removed as redundant (error message and code returned via
  API).

## [0.0.9] - 2025-02-27

### Added

- `agent status [agent-name]` now shows deployment info and scaling
  configuration.

- `agent sessions [agent-name]` lists active session count for an agent (will
  list session details in future).

- `agent start [agent-name] -D` now shows the Daily room URL (and token) to
  join in the terminal output.

### Changed

- Changed CLI command from `pipecat` to `pipecatcloud` or `pcc`.

- `agent delete` prompts the user for confirmation first.

- `agent start` now checks the target deployment first to ensure it exists and
  is in a healthy state.

- Changed the information order of `agent status` so the health badge is
  clearly visible in terminal.

- `agent deploy` now defaults to "Y" for the confirmation prompts.

### Fixed

- Fixed lint error with payload data in `agent start`.

- Fixed a bug where `pcc-deploy.toml` files were required to be present.

- `deploy` command now correctly passes the secret set to the deployment from
  the `pcc-deploy.toml` file.
