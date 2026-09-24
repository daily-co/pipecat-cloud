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

# Used only when the API does not name its registry: the mint response carries
# `registry_host` from the API version that added it on.
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
    # The registry of the environment the key was minted against, so a key
    # minted on staging is not paired with production's host.
    host = data.get("registry_host") or PROD_REGISTRY_HOST
    # The key goes to helm on stdin. printf is a shell builtin, so the key never
    # becomes a process argument that `ps` can read, and helm does not warn
    # about a password on its command line.
    login_cmd = f"printf '%s' '{key}' | helm registry login {host} -u {username} --password-stdin"
    if console.json_output:
        # Shown exactly once — stdout carries it, chrome goes to stderr.
        console.output_json(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "key": key,
                "username": username,
                "registryHost": host,
                "helmLoginCommand": login_cmd,
            }
        )
        return
    console.success("Registry key minted — it is shown ONCE and cannot be retrieved again.")
    console.print("\nLog your workstation's helm in before pulling the chart:\n")
    console.print(f"  {login_cmd}\n", soft_wrap=True)


def _status(key: dict) -> str:
    """The key's lifecycle state as the API reports it. An API older than the
    `status` field only says whether a key was revoked, so an expired key from
    one reads as active, as it did before."""
    return key.get("status") or ("revoked" if key.get("revoked") else "active")


@registry_keys_cli.command(name="list", help="List registry keys (no key material)")
@synchronizer.create_blocking
@requires_login
async def list_keys(
    show_all: bool = typer.Option(
        False, "--all", help="Include revoked and expired keys, not only active ones"
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    with console.status("[dim]Fetching registry keys...[/dim]", spinner="dots"):
        data, error = await API.registry_keys(org=org)
        if error:
            raise typer.Exit(1)

    keys = (data or {}).get("registry_keys") or []
    # Revoked and expired keys are history: every renewal of a region leaves
    # one behind, and they crowd out the keys that still work.
    shown = keys if show_all else [k for k in keys if _status(k) == "active"]
    hidden = len(keys) - len(shown)
    if console.json_output:
        console.output_json({"keys": shown})
        return
    if not shown:
        if hidden:
            console.print(
                f"[yellow]No active registry keys[/yellow] "
                f"({hidden} revoked or expired; pass --all to show them)"
            )
        else:
            console.print("[yellow]No registry keys[/yellow]")
        return

    # Plain output is read by position (`cut`, `awk`), so the columns keep the
    # places they had: Status takes the old Revoked column's slot and Region is
    # appended. Region names the region holding the key; those keys are its
    # cluster's pull credential, so they cannot be revoked here while the
    # region is live.
    headers = ["ID", "Name", "Prefix", "Created", "Last used", "Status", "Region"]
    rows = [
        (
            str(k.get("id", "")),
            str(k.get("name") or "—"),
            str(k.get("key_prefix") or "—"),
            str(k.get("created_at") or "—"),
            str(k.get("last_used_at") or "—"),
            _status(k),
            str(k.get("region") or "—"),
        )
        for k in shown
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
    if hidden:
        console.print(
            f"[dim]{hidden} revoked or expired key(s) hidden; pass --all to show them[/dim]"
        )


@registry_keys_cli.command(
    name="revoke",
    help="Revoke a registry key. A key held by a live region is refused: "
    "the region's renewal rotates it and deleting the region revokes it.",
)
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
