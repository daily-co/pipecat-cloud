# Pipecat Cloud Changelog

All notable changes to **Pipecat Cloud** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.9] - 2025-02-27

### Added
- `agent status [agent-name]` now shows deployment info and scaling configuration
- `agent sessions [agent-name]` lists active session count for an agent (will list session details in future)
- `agent start [agent-name] -D` now shows the Daily room URL (and token) to join in the terminal output

### Changed
- Changed CLI command from `pipecat` to `pipecatcloud` or `pcc`
- `agent delete` prompts the user for confirmation first
- `agent start` now checks the target deployment first to ensure it exists and is in a healthy state
- Changed the information order of `agent status` so the health badge is clearly visible in terminal
- `agent deploy` now defaults to "Y" for the confirmation prompts

### Fixed
- Fixed lint error with payload data in `agent start`
- Fixed a bug where `pcc-deploy.toml` files were required to be present
- `deploy` command now correctly passes the secret set to the deployment from the `pcc-deploy.toml` file

## [0.0.8] - 2025-02-03

### Added
- `secrets set [set-name] --file` allowing you to create a secret set from .env file
- `agent scale` to modify agent configuration without pushing a new image
- `run` command for running bot.py files locally via FastAPI. This initial implementation is very basic and will be expanded in the future.
- `organizations select` allows passing an `--org / -o` option to bypass prompt

### Fixed
- All commands use the correct shorthand flag syntax (`-` vs. `--`)
- CLI config extends from module config to avoid loading config TOML with package imports
- `secrets list` now correctly lists all secrets vs. just a single entry
- `deploy` command displays live status to prevent skewed terminal output
- `deploy` command no longer allows pushing images without a tag
- `agent logs` paginate / filter / sort and tidy up display for logs
- `start` command correctly clears the live terminal text
- `pcc-deploy.toml` files now work as intended with `deploy` command
- `deploy` method now passes configuration parameters correctly
- Switching organizations will fully remove any default API tokens from the previously selected organization

### Changed
- Local config now retains default keys when switching between organizations
- Local config now retains defaukt keys when logging in again
- `deploy` will check passed secret set exists before deployment
- `secrets list` now takes an optional `--sets / -s` parameter that filters by secret sets or image pull secrets
- `organizations keys create` now prompts you to set the newly created key as the default after creation (if active org matches)
- `organizations keys delete` not prompts to confirm if selected key is currently your local config default
- `Agent` module import has been renamed to `Session` to better reflect API


## [0.0.7] - 2025-02-01

### Added

- `run` command for running an agent locally. This initial implementation is very basic and will be expanded in the future.

### Changed

- `start_agent` method no longer requires an organization. If not provided, it will assume the namespace of the API token owner.
- `start` command no longer looks up the agent before starting (health check is handled by the proxy route.)

## [0.0.6] - 2025-02-01

### Added

- `pipecatcloud.agent` module for working with agents via Python scripts
    - `start_agent` method for starting an agent

### Backlog

- `deploy` make image pull secrets non-optional, but allow for bypassing
- Fix: update docs to assert that the bot method must be async
- Fix: Secrets are not upserting (overriding each time, which may be preferrable?)
- Fix: deployment process needs better output
- `organizations keys create` should ask if you'd like to set the created key as default after creation (if org matches)
- Sense check image parameter of the deploy command (does it have a tag, etc)
- `agent logs` paginate / filter / sort and tidy up display for logs
- Fix: start command should clear the live text
- Secrets from .env method
- Fix: .pcc-deploy.toml should read correctly 
- New conveience method to add account associated Daily API Key to a secret set
- Add: concurrency support for agent deployments