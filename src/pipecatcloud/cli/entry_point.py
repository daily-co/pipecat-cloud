import typer

from pipecatcloud.cli.auth import auth_cli
from pipecatcloud.cli.deploy import create_deploy_command
from pipecatcloud.cli.organizations import organization_cli
from pipecatcloud.cli.agent import agent_cli
from pipecatcloud.config import config


def version_callback(value: bool):
    if value:
        from pipecatcloud.__version__ import version

        typer.echo(
            f"ᓚᘏᗢ Pipecat Cloud Client Version: {typer.style(version, fg=typer.colors.GREEN)}")
        raise typer.Exit()


def config_callback(value: bool):
    if value:
        from pipecatcloud.config import config

        typer.echo(config.to_dict())
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
    # The CLI sets the users currently active org in context
    # which is used as a default when an `--org` flag is not provided
    ctx.obj["org"] = config.get("org")
    ctx.obj["token"] = config.get("token")


create_deploy_command(entrypoint_cli_typer)
entrypoint_cli_typer.add_typer(auth_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(organization_cli, rich_help_panel="Commands")
entrypoint_cli_typer.add_typer(agent_cli, rich_help_panel="Commands")
entrypoint_cli = typer.main.get_command(entrypoint_cli_typer)
