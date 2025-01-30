<h1><div align="center">
 <img alt="pipecat cloud" width="500px" height="auto" src="./pipecat-cloud.png">
</div></h1>

[![Docs](https://img.shields.io/badge/documentation-blue)](https://docs.pipecat.daily.co)
[![PyPI](https://img.shields.io/pypi/v/pipecatcloud)](https://pypi.org/project/pipecatcloud)

# Pipecat Cloud

Python module and CLI for interacting with [Pipecat Cloud](https://pipecat.daily.co).

### Requirements

- Python 3.10+
- Docker and a Docker repository (e.g. Docker Hub)
- Active [Pipecat Cloud](https://pipecat.daily.co)
  account

### Documentation

Documentation for Pipecat Cloud is available [here](https://docs.pipecat.daily.co).

### Installation

```shell
pip install pipecatcloud

pcc --version
pcc --help

# Note: you can use `pcc` or `pipecatcloud` interchangeably
pcc auth login
```

! All CLI commands have a `--help` flag that will display the command usage and options.

## Usage

1. Create an account at [Pipecat Cloud](https://pipecat.daily.co)

2. Login to your account `pcc auth login`

3. (Optional): Clone the starter agent [here](https://github.com/daily-co/pipecat-cloud-starter)

4. Build your agent `docker build --platform linux/arm64 -t your-agent-name .`

5. Push your Docker image to your repository `docker push your-repository/your-agent-name:0.1`

6. Deploy your agent `pcc deploy starter-agent your-repository/your-agent-name:0.1`

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
