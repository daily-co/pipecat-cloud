
<h1><div align="center">
 <img alt="pipecat cloud" width="500px" height="auto" src="./pipecat-cloud.png">
</div></h1>


[![Docs](https://img.shields.io/badge/documentation-blue)](https://docs.pipecat.cloud)
[![PyPI](https://img.shields.io/pypi/v/pipecat-cloud)](https://pypi.org/project/pipecat-cloud)

# Pipecat Cloud

Python module and CLI for interacting with [Pipecat Cloud](https://pipecat.cloud).

### Requirements

- Python 3.10+
- Docker and a Docker repository (e.g. Docker Hub)
- Active [Pipecat Cloud](https://dashboard.pipecat.cloud)
 account

### Documentation

Documentation for Pipecat Cloud is available [here](https://docs.pipecat.cloud).

### Installation

```shell
pip install pipecatcloud

# Note: you can use `pipecat` or `pcc` interchangeably

pipecat --help
pipecat --version
```

! All CLI commands have a `--help` flag that will display the command usage and options.

## Usage

1. Create an account at [Pipecat Cloud](https://pipecat.cloud)

2. Login to your account `pipecat auth login`

3. (Optional): Clone the starter agent [here](https://github.com/pipecat-ai/pipecat-cloud-starter-agent)

4. Build your agent `docker build --platform linux/arm64 -t your-agent-name .`

5. Push your Docker image to your repository `docker push your-repository/your-agent-name:0.1`

6. Deploy your agent `pipecat deploy starter-agent your-repository/your-agent-name:0.1`

