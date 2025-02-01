# Changelog

All notable changes to **Pipecat Cloud** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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