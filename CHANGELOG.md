# Pipecat Cloud Changelog

All notable changes to **Pipecat Cloud** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-03-05

- `pipecatcloud.toml` moved to `$HOME/.config/pipecatcloud/pipecatcloud.toml`

### Added

- `pcc auth whoami` now shows the namespace Daily API key for convenience

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