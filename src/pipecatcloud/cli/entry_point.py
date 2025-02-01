import sys

import typer
from loguru import logger

from pipecatcloud.cli.agent import agent_cli
from pipecatcloud.cli.auth import auth_cli
from pipecatcloud.cli.deploy import create_deploy_command
from pipecatcloud.cli.organizations import organization_cli
from pipecatcloud.cli.run import create_run_command
from pipecatcloud.cli.secrets import secrets_cli
from pipecatcloud.config import config

logger.remove()
logger.add(sys.stderr, level=str(config.get("cli_log_level", "INFO")))


def version_callback(value: bool):
    if value:
        from pipecatcloud.__version__ import version

        typer.echo(
            f"ᓚᘏᗢ Pipecat Cloud Client Version: {typer.style(version, fg=typer.colors.GREEN)}")
        raise typer.Exit()


def config_callback(value: bool):
    if value:
        from rich import print_json
        from rich.pretty import pprint

        from pipecatcloud._utils.deploy_utils import load_deploy_config_file
        from pipecatcloud.config import config

        # Check for deploy config
        deploy_config = load_deploy_config_file()
        if deploy_config:
            print("Deploy config:")
            print_json(data=deploy_config)

            print("Config:")
            print_json(data=config.to_dict())

        pprint(config.to_dict())
        raise typer.Exit()


entrypoint_cli_typer = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
    help="""
    ᓚᘏᗢ Pipecat Cloud CLI
    See website at https://pipecat.cloud
    """,
)


@entrypoint_cli_typer.callback()
def pipecat(
    ctx: typer.Context,
    _version: bool = typer.Option(None, "--version", callback=version_callback, help="CLI version"),
    _config: bool = typer.Option(None, "--config", callback=config_callback, help="CLI config"),
):
    if not ctx.obj:
        ctx.obj = {}

    # All commands require an active namespace (organization)
    # The CLI reads config data and sets the users currently active org in context
    # which is used as a default when an `--org` flag is not provided
    ctx.obj["org"] = config.get("org")
    ctx.obj["token"] = config.get("token")
    ctx.obj["default_public_key"] = config.get("default_public_key", None)
    ctx.obj["default_public_key_name"] = config.get("default_public_key_name", None)


create_deploy_command(entrypoint_cli_typer)
create_run_command(entrypoint_cli_typer)
entrypoint_cli_typer.add_typer(auth_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(organization_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(agent_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(secrets_cli, rich_help_panel="Commands")
entrypoint_cli = typer.main.get_command(entrypoint_cli_typer)
