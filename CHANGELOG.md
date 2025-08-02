# Pipecat Cloud Changelog

All notable changes to **Pipecat Cloud** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


##  [0.2.1] - 2025-08-02

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
