#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import sys

import typer
from loguru import logger

from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli.commands.agent import agent_cli
from pipecatcloud.cli.commands.auth import auth_cli
from pipecatcloud.cli.commands.deploy import create_deploy_command
from pipecatcloud.cli.commands.init import create_init_command
from pipecatcloud.cli.commands.organizations import organization_cli
from pipecatcloud.cli.commands.run import create_run_command
from pipecatcloud.cli.commands.secrets import secrets_cli
from pipecatcloud.cli.config import config
from pipecatcloud.exception import ConfigFileError

logger.remove()
logger.add(sys.stderr, level=str(config.get("cli_log_level", "INFO")).upper())


def version_callback(value: bool):
    if value:
        from pipecatcloud.__version__ import version

        typer.echo(
            f"ᓚᘏᗢ Pipecat Cloud Client Version: {typer.style(version, fg=typer.colors.GREEN)}"
        )
        raise typer.Exit()


def config_callback(value: bool):
    if value:
        from rich.pretty import pprint

        from pipecatcloud._utils.deploy_utils import load_deploy_config_file

        # Print local config
        pprint(config.to_dict())

        # Check for deploy config
        try:
            deploy_config = load_deploy_config_file()
            if deploy_config:
                console.print("Deploy config [dim](pcc-deploy.toml)[/dim]:")
                console.print_json(data=deploy_config.to_dict())
        except ConfigFileError as e:
            console.error(
                f"Malformed pcc-deploy.toml - Please correct errors and try again.\n\n{e}"
            )

        raise typer.Exit()


entrypoint_cli_typer = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
    help="""
    ᓚᘏᗢ Pipecat Cloud CLI
    See website at https://pipecat.daily.co
    """,
)


@entrypoint_cli_typer.callback()
def cli(
    ctx: typer.Context,
    _version: bool = typer.Option(None, "--version", callback=version_callback, help="CLI version"),
    _config: bool = typer.Option(None, "--config", callback=config_callback, help="CLI config"),
):
    pass


create_run_command(entrypoint_cli_typer)
create_init_command(entrypoint_cli_typer)
create_deploy_command(entrypoint_cli_typer)
entrypoint_cli_typer.add_typer(auth_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(organization_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(secrets_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(agent_cli, rich_help_panel="Commands")

entrypoint_cli = typer.main.get_command(entrypoint_cli_typer)
