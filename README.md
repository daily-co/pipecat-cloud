# Pipecat Cloud CLI

- [Overview](#getting-started)
    - [Requirements](#requirements)
    - [Documentation](#documentation)
    - [Installation](#installation)
- [Usage](#usage)
- [Accounts and organizations](#accounts-and-organizations)
- [Deployment](#deploying-an-agent)
- [Observation](#observing-an-agent)
- [Secrets](#secrets)
- [Further reading](#further-reading)
    - [Local development](#local-development)
    - [Getting help](#getting-help)

## Getting started

### Requirements

- Python 3.10+
- Docker and Docker repository (e.g. Docker Hub)
- Linux or MacOS (Windows not currently supported)
- Active Pipecat Cloud account

### Documentation

Documentation for Pipecat Cloud is available [here](https://pipecat.cloud/docs).

### Installation

```shell
pip install pipecatcloud

pipecatcloud --help
pipecatcloud --version

pipecatcloud auth login
pipecatcloud auth logout
pipecatcloud auth whoami

pipecatcloud organizations list
pipecatcloud organizations select

pipecatcloud deploy agent-name image-repository/image:0.1

pipecatcloud run your-agent.py

pipecatcloud agent logs agent-name
pipecatcloud agent delete agent-name
pipecatcloud agent status agent-name

pipecatcloud secrets list
pipecatcloud secrets set secret-set SECRET_NAME secret-value
pipecatcloud secrets unset secret-set SECRET_NAME
```

! All CLI commands have a `--help` flag that will display the command usage and options.

## Usage

1. Create an account at [Pipecat Cloud](https://pipecat.cloud)

2. Login to your account `pipecatcloud auth login`

3. (Optional): Clone the starter agent [here](https://github.com/pipecat-ai/pipecat-cloud-starter-agent)

4. Build your agent `docker build -t your-agent-name .`

5. Push your Docker image to your repository `docker push your-repository/your-agent-name:0.1`

6. Deploy your agent `pipecatcloud deploy starter-agent your-repository/your-agent-name:0.1`


## Accounts and organizations

@TODO: Pipecat Cloud introduction

You must log in to your Pipecat Cloud account before you can use the CLI.

`pipcatcloud auth login` 

Logging in to Pipecat Cloud will store credentials on your device.

You can remove the stored credentials with the following command:

`pipecatcloud auth logout`

### Organizations

By default the CLI will use your personal user workspace when making deployments.

If you are looking to collaborate with other developers on an agent, you will need to create or join an organization which can be done via the Pipecat Cloud dashboard [here](https://pipecat.cloud/dashboard/organizations/create).

Once you have joined an organization, you can select it as your default workspace with the following command:

`pipecatcloud organizations select your-organization-name`

You can optionally specify which organization to use for all commands by passing the `--org` flag e.g:

`pipecatcloud deploy x y z --org your-organization-name.`


## Deploying an agent

To deploy an agent, you will need to have a Docker image built and pushed to a repository.

Agents must be deployed:
1. With a unique identifier (e.g. `your-agent-name`)
2. With a Docker image reference (e.g. `your-repository/your-agent-name:0.1`)

```shell
pipecatcloud deploy your-agent-name your-repository/your-agent-name:0.1
```

### Deployment configuration

| Flag | Description | Default |
|------|-------------|---------|
| `--min-instances` | The minimum number of instances to run for your agent | 1 |
| `--max-instances` | The maximum number of instances your agent can run | 50 |
| `--cpu` | The number of CPU cores to allocate to each instance | 1 |
| `--memory` | The amount of memory to allocate to each instance | 512Mi |


### Updating an existing deployment

Deploying to the same agent name will update the existing deployment. Once the updated deployment is complete, the following will happen:

1. Idle agent instances will be scaled down to 0.
2. Auto-scaler will create new instances to meet the desired number of instances.
3. Active agent sessions will not be affected by the deployment update until they are concluded.

You will be prompted to confirm the update before it is applied.

### Auto-scaling and cold starts

Pipecat Cloud will dynamically scale your agent up and down based on the velocity of requests it receives. Auto-scaling can be optimized by the min and max instance flags of your deployment.

Pipecat Cloud will support a maximum of @TODO X instances per deployment. If you need more, please contact us.

We recommend having a minimum of 1 instance running to avoid cold starts when your end-users request your agent. 

For more information on how Pipecat Cloud scales your agent, please see our [documentation](https://pipecat.cloud/docs/scaling).

### Using a pcc-deploy.toml file

You can optionally specify a `pcc-deploy.toml` file in the root of your project to configure your deployment. For example:

```toml
name = "your-agent-name"
image = "your-repository/your-agent-name:0.1"

[config]
    min_instances = 1
    max_instances = 50
    cpu = 1
    memory = "512Mi"

[secrets]
    [secret-set-name]
        SECRET_NAME = "SECRET_VALUE"
```

Values set in a `pcc-deploy.toml` file will override any default values but are superceded by any flags passed to the `pipecatcloud deploy` command.

## Observing an agent

@TODO

## Secrets

@TODO

## Further reading

### Local development

@TODO

### Getting help

@TODO