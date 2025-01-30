import aiohttp
import typer
from rich.console import Console
from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud.config import config

services_cli = typer.Typer(name="services", help="Manage Pipecat Cloud Services.", no_args_is_help=True)

@services_cli.command(name="create", help="Create a service.")
@synchronizer.create_blocking
async def _create(organization: str, service: str, image: str, timeout: float = 40.0, network_timeout: float = 5.0,):
    console = Console()
    token = config.get("token")
    if token is None:
        console.print("[red]Not logged in[/red]")
        return

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{config.get('server_url')}/v1/organizations/{organization}/services/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout + network_timeout,
            json={"serviceName": service, "image": image},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                console.print(data)
            else:
                console.print("[red]Failed to get service data[/red]")

@services_cli.command(name="list", help="Display data about the current user's organization services.")
@synchronizer.create_blocking
async def _list(organization: str, timeout: float = 40.0, network_timeout: float = 5.0,):
    console = Console()
    token = config.get("token")
    if token is None:
        console.print("[red]Not logged in[/red]")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{config.get('server_url')}/v1/organizations/{organization}/services/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout + network_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                console.print(data)
            else:
                console.print("[red]Failed to get service data[/red]")


@services_cli.command(name="delete", help="Delete a service.")
@synchronizer.create_blocking
async def _delete(organization: str, service: str, timeout: float = 40.0, network_timeout: float = 5.0,):
    console = Console()
    token = config.get("token")
    if token is None:
        console.print("[red]Not logged in[/red]")
        return

    async with aiohttp.ClientSession() as session:
        async with session.delete(
            f"{config.get('server_url')}/v1/organizations/{organization}/services/{service}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout + network_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                console.print(data)
            else:
                console.print("[red]Failed to get service data[/red]")

@services_cli.command(name="start", help="Send a POST request to the service's start endpoint.")
@synchronizer.create_blocking
async def _start(organization: str, service: str, timeout: float = 40.0, network_timeout: float = 5.0,):
    console = Console()
    token = config.get("token")
    if token is None:
        console.print("[red]Not logged in[/red]")
        return

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{config.get('server_url')}/v1/organizations/{organization}/services/{service}/proxy",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout + network_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                console.print(data)
            else:
                console.print("[red]Failed to get service data[/red]")


@services_cli.command(name="logs", help="Get logs for the given service.")
@synchronizer.create_blocking
async def _logs(organization: str, service: str, timeout: float = 40.0, network_timeout: float = 5.0,):
    console = Console()
    token = config.get("token")
    if token is None:
        console.print("[red]Not logged in[/red]")
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{config.get('server_url')}/v1/organizations/{organization}/services/{service}/logs",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout + network_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                console.print(data)
            else:
                console.print("[red]Failed to get service data[/red]")
