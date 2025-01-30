import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from pipecatcloud import PIPECAT_DEPLOY_CONFIG_PATH

console = Console()

TEST_CONFIG_FILE = {
}


def _deploy(agent_name: str, image: str, config: dict):
    console.print(f"Deploying {agent_name} with image {image} and config {config}")

# ----- Deploy


def create_deploy_command(app: typer.Typer):
    # Note we wrap the deploy command to avoid circular imports
    @app.command(name="deploy", help="Deploy to Pipecat Cloud")
    def deploy(
        ctx: typer.Context,
        agent_name: str = typer.Argument(
            None,
            help="Name of the agent to deploy e.g. 'my-agent'",
            show_default=False),
        image: str = typer.Argument(
            None,
            help="Docker image location e.g. 'my-image:latest'",
            show_default=False),
        min_instances: int = typer.Option(
            1,
            "--min-instances",
            help="Minimum number of instances to keep warm",
            rich_help_panel="Deployment Configuration",
            min=1),
        max_instances: int = typer.Option(
            20,
            "--max-instances",
            help="Maximum number of allowed instances",
            rich_help_panel="Deployment Configuration",
            min=0,
            max=50),
        cpu: int = typer.Option(
            1,
            "--cpu",
            help="Number of CPU cores to allocate",
            rich_help_panel="Deployment Configuration",
            min=1,
            max=10),
        memory: str = typer.Option(
            "512mi",
            "--memory",
            help="Memory to allocate",
            rich_help_panel="Deployment Configuration",
        ),
        organization: str = typer.Option(
            None,
            "--organization",
            "--org",
            "-o",
            help="Organization to deploy to",
            rich_help_panel="Deployment Configuration",
        ),

    ):
        # Compose deployment config from CLI options and config file (if provided)
        # Order of precedence:
        #   1. Arguments provided to the CLI deploy command
        #   2. Values from the config toml file
        #   3. CLI command defaults

        deployment_config = {
            "cpu": cpu,
            "memory": memory,
            "min_instances": min_instances or 1,
            "max_instances": max_instances or 20,
            "organization": organization or ctx.obj.get("org"),
        }

        # @TODO: check for deployment config file
        console.print(f"[dim]No {PIPECAT_DEPLOY_CONFIG_PATH} file provided, using defaults[/dim]")

        # Collect passed values from CLI arguments (ignoring defaults)
        passed_values = {}
        for param in ctx.command.params:
            if param.name == "agent_name" or param.name == "image":
                continue
            value = ctx.params.get(str(param.name))
            # Only include if the value is different from the parameter's default
            if value != param.default:
                passed_values[param.name] = value
        deployment_config.update(passed_values)

        # Merge with values from deployment config file
        for key, value in TEST_CONFIG_FILE.items():
            deployment_config.setdefault(key, value)

        final_agent_name = agent_name or deployment_config.get("agent_name")
        final_image = image or deployment_config.get("image")

        # Assert agent name and image are provided
        if not final_agent_name:
            raise typer.BadParameter("Agent name is required")
        if not final_image:
            console.print("[red]Error:[/red] Image location is required", style="bold red")
            raise typer.BadParameter("Image location is required")

        # Create and display table
        table = Table(
            show_header=False,
            border_style="dim",
            show_edge=True,
            show_lines=True)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Min instances", str(deployment_config['min_instances']))
        table.add_row("Max instances", str(deployment_config['max_instances']))
        table.add_row("CPU", str(deployment_config['cpu']))
        table.add_row("Memory", str(deployment_config['memory']))

        content = Group(
            f"[bold white]Agent name:[/bold white] [green]{final_agent_name}[/green]   [bold white]Image:[/bold white] [green]{final_image}[/green]   [bold white]Organization:[/bold white] [green]{deployment_config['organization']}[/green]",
            "\n[dim]Deployment configuration:[/dim]",
            table)

        console.print(
            Panel(
                content,
                title="Review deployment",
                title_align="left",
                padding=1,
                style="yellow",
                border_style="yellow"))

        if not typer.confirm("\nDo you want to proceed with deployment?", default=True):
            raise typer.Abort()

        _deploy(final_agent_name, final_image, deployment_config)
