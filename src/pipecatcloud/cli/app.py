import aiohttp
import typer
from rich import box
from rich.console import Console
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud.config import config

app_cli = typer.Typer(name="apps", help="Manage Pipecat Cloud apps", no_args_is_help=True)


@app_cli.command(name="list", help="List all your apps")
@synchronizer.create_blocking
async def list_apps():
    console = Console()

    user_id = config.get("user_id")
    if user_id is None:
        console.print("[red]Not logged in[/red]")
        return

    headers = {"Authorization": f"Bearer {user_id}", "Content-Type": "application/json"}

    with console.status("[bold blue]Fetching your apps...", spinner="dots") as status:
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(
                    f"{config.get('server_url')}{config.get('apps_path')}"
                ) as resp:
                    if resp.status == 401:
                        console.print("[red]Authentication failed. Please login again.[/red]")
                        return
                    if resp.status != 200:
                        raise Exception(f"Failed to obtain apps (HTTP {resp.status})")

                    apps = await resp.json()

            except aiohttp.ClientError as e:
                console.print(f"[red]Unable to connect to Pipecat Cloud API:[/red] {str(e)}")
                return
            except Exception as e:
                console.print(f"[red]Error:[/red] {str(e)}")
                return

    if not apps:
        console.print("\n[yellow]No apps found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold blue", border_style="blue", box=box.ROUNDED)

    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Emphemeral", style="green")
    table.add_column("Status", style="red")

    for app in apps:
        table.add_row(
            app.get("app_id", "N/A"),
            app.get("app_name", "N/A"),
            str(app.get("emphemeral", True)),
            app.get("status"),
        )

    # Print summary and table
    console.print(f"\n[bold blue]Found {len(apps)} apps[/bold blue]\n")
    console.print(table)
