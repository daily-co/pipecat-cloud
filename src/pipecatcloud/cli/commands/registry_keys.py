#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Registry pull keys (PCC-1082/PCC-1103): org-scoped, pull-only credentials
for fetching the region package chart from the Daily container registry.

Org-scoped, so the group lives under `organizations` beside API keys — but a
key's only consumer today is the self-hosted region install (the workstation
`helm registry login` before pulling the pcc-region chart). The key material
is shown exactly once, at mint; the cluster's own image-pull credential is
delivered by enrollment and never passes through here.
"""

import questionary
import typer
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

PROD_REGISTRY_HOST = "registry.pipecat.daily.co"

registry_keys_cli = typer.Typer(
    name="registry-keys",
    help="Registry pull keys for fetching the region package chart",
    no_args_is_help=True,
)


@registry_keys_cli.command(name="mint", help="Mint a registry pull key (shown exactly once)")
@synchronizer.create_blocking
@requires_login
async def mint_key(
    name: str = typer.Option(
        None,
        "--name",
        help="Human label for the key, e.g. acme-us-east-workstation",
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    # The API requires a name — it's how keys are told apart in `list` and
    # revoked safely. Prompt when interactive; otherwise require --name.
    if name is None and console.is_terminal and not console.json_output:
        answer = await questionary.text(
            "Name for this key (e.g. acme-us-east-workstation):"
        ).ask_async()
        if answer and answer.strip():
            name = answer.strip()
    if not name:
        console.error("A key name is required. Pass --name.")
        raise typer.Exit(2)

    with console.status("[dim]Minting registry key...[/dim]", spinner="dots"):
        data, error = await API.registry_key_mint(org=org, name=name)
        if error:
            raise typer.Exit(1)

    key = (data or {}).get("key")
    if not key:
        console.error("The API did not return a key")
        raise typer.Exit(1)

    username = data.get("username", "pcc")
    login_cmd = f"helm registry login {PROD_REGISTRY_HOST} -u {username} -p {key}"
    if console.json_output:
        # Shown exactly once — stdout carries it, chrome goes to stderr.
        console.output_json(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "key": key,
                "username": username,
                "helmLoginCommand": login_cmd,
            }
        )
        return
    console.success("Registry key minted — it is shown ONCE and cannot be retrieved again.")
    console.print("\nLog your workstation's helm in before pulling the chart:\n")
    console.print(f"  {login_cmd}\n", soft_wrap=True)


@registry_keys_cli.command(name="list", help="List registry keys (no key material)")
@synchronizer.create_blocking
@requires_login
async def list_keys(
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    with console.status("[dim]Fetching registry keys...[/dim]", spinner="dots"):
        data, error = await API.registry_keys(org=org)
        if error:
            raise typer.Exit(1)

    keys = (data or {}).get("registry_keys") or []
    if console.json_output:
        console.output_json({"keys": keys})
        return
    if not keys:
        console.print("[yellow]No registry keys[/yellow]")
        return

    headers = ["ID", "Name", "Prefix", "Created", "Last used", "Revoked"]
    rows = [
        (
            str(k.get("id", "")),
            str(k.get("name") or "—"),
            str(k.get("key_prefix") or "—"),
            str(k.get("created_at") or "—"),
            str(k.get("last_used_at") or "—"),
            "yes" if k.get("revoked") else "no",
        )
        for k in keys
    ]
    if not console.rich_output:
        console.print_records(headers, rows)
        return
    table = Table(show_header=True, header_style="bold")
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*row)
    console.print(table)


@registry_keys_cli.command(name="revoke", help="Revoke a registry key")
@synchronizer.create_blocking
@requires_login
async def revoke_key(
    key_id: str = typer.Argument(..., help="The key's id (see list)"),
    force: bool = typer.Option(False, "--force", "-f", help="Bypass prompt for confirmation"),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    if not force:
        console.require_interactive("--force")
        if not await questionary.confirm(
            "Revoke this registry key? Workstations using it lose chart-pull "
            "access immediately; enrolled clusters are unaffected."
        ).ask_async():
            console.print("[bold]Aborting revoke request[/bold]")
            raise typer.Exit(1)

    with console.status("[dim]Revoking registry key...[/dim]", spinner="dots"):
        _, error = await API.registry_key_revoke(org=org, key_id=key_id)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"revoked": key_id})
        return
    console.success("Registry key revoked")
