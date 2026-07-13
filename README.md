<h1><div align="center">
 <img alt="pipecat cloud" width="500px" height="auto" src="https://raw.githubusercontent.com/daily-co/pipecat-cloud/main/pipecat-cloud.png">
</div></h1>

[![Docs](https://img.shields.io/badge/documentation-blue)](https://docs.pipecat.ai/pipecat-cloud)
[![PyPI](https://img.shields.io/pypi/v/pipecatcloud)](https://pypi.org/project/pipecatcloud)

# Pipecat Cloud

Python module and CLI for interacting with [Pipecat Cloud](https://pipecat.daily.co).

### Requirements

- Python 3.11+
- Docker and a Docker repository (e.g. Docker Hub)
- Active [Pipecat Cloud](https://pipecat.daily.co)
  account

### Documentation

Documentation for Pipecat Cloud is available [here](https://docs.pipecat.ai/pipecat-cloud).

### Installation

The Pipecat Cloud CLI ships as part of the [Pipecat CLI](https://github.com/pipecat-ai/pipecat-cli) — its commands are available under `pipecat cloud`. Install the Pipecat CLI:

```shell
uv tool install pipecat-ai-cli
```

```shell
pipecat cloud --help
pipecat cloud auth login
```

! All CLI commands have a `--help` flag that will display the command usage and options.

To use Pipecat Cloud programmatically (without the CLI), install the package directly with `uv add pipecatcloud` and see [Usage in Python scripts](#usage-in-python-scripts).

## Usage

1. Create an account at [Pipecat Cloud](https://pipecat.daily.co)

2. Login to your account `pipecat cloud auth login`

3. Generate secrets `pipecat cloud secrets set`

4. Build and deploy your agent `pipecat cloud deploy`

### Usage in Python scripts

If want to programmatically start an agent within a Python script, you can use the `pipecatcloud.session` module.

```python
from pipecatcloud.session import Session
from pipecatcloud.exception import AgentStartError
import asyncio

async def main():
    session = Session(
        agent_name="your-agent-name",
        api_key="pk_...",
    )

    try:
        await session.start()
    except AgentStartError as e:
        print(e)
    except Exception as e:
        raise (e)

if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting

### SSL certificate errors on macOS

If `pipecat cloud auth login` fails with an SSL certificate verification error, your Python
installation may not have access to the macOS system certificate store. This is
common with Python installed via pyenv, conda, or the python.org installer.

To diagnose:

```python
import ssl, sys, os
print(ssl.get_default_verify_paths())
print(os.path.realpath(sys.executable))
```

To fix, install `certifi` and point Python to its certificates:

```shell
pip install certifi
export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")
```

For the python.org installer, you can also run the bundled
`Install Certificates.command` script found in `/Applications/Python X.Y/`.

## 🛠️ Contributing

### Setup Steps

1. Clone the repository and navigate to it:

   ```bash
   git clone https://github.com/daily-co/pipecat-cloud.git
   cd pipecat-cloud
   ```

2. Install development and testing dependencies:

   ```bash
   uv sync --group dev
   ```

3. Install the git pre-commit hooks:

   ```bash
   uv run pre-commit install
   ```

### Running tests

To run all tests, from the root directory:

```bash
uv run pytest
```

Run a specific test suite:

```bash
uv run pytest tests/test_name.py
```
