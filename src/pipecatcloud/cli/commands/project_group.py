#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The `project` command group, and the pieces its commands share.

A project is an agent with environments, and each environment is an agent of
its own. One group covers both: a command names the project, and `--env`
reaches one of its environments. The group is also registered as `agent`,
which is deprecated and prints a notice.

The group lives here rather than in either command module so that `agent.py`
(the per-agent commands) and `project.py` (the project-level settings) can
both add to it without importing each other.
"""

import sys

import typer
from rich import box
from rich.table import Table

from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.environment_utils import (
    PRODUCTION_ENVIRONMENT_NAME,
    environments_of,
    environments_with_production,
    is_environment,
)
from pipecatcloud._utils.github_utils import short_sha
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API

#: The deprecated name the group is also registered under.
AGENT_ALIAS_NAME = "agent"

project_cli = typer.Typer(
    name="project",
    help="Manage projects, their environments, and what they share",
    no_args_is_help=True,
)

# Which name the group was invoked under, for the few places that differ.
# Assigned on every invocation, so it never carries over.
_invoked_as = "project"


def invoked_as_agent() -> bool:
    """Whether this invocation came through the deprecated `agent` name."""
    return _invoked_as == AGENT_ALIAS_NAME


@project_cli.callback()
def project_group(ctx: typer.Context):
    global _invoked_as
    _invoked_as = ctx.info_name or "project"
    if invoked_as_agent():
        # stderr whichever output mode is in force: a line on stdout would
        # break a plain-mode `cut` pipeline as surely as it would break a
        # JSON parse.
        sys.stderr.write(
            f"warning: '{PIPECAT_CLI_NAME} agent' is deprecated. Use "
            f"'{PIPECAT_CLI_NAME} project' instead; an environment is addressed with "
            "--env.\n"
        )


ORGANIZATION_OPTION = typer.Option(
    None,
    "--organization",
    "-o",
    help="Organization to act in. Defaults to the one in your configuration.",
)

ENVIRONMENT_OPTION = typer.Option(
    None,
    "--env",
    help="Act on this environment of the project rather than on the project itself",
)

PROJECT_ARGUMENT_HELP = "Name of the project, or of an agent, e.g. 'my-bot'"


async def load_services(org: str | None, include: list[str] | None = None) -> list[dict]:
    """Every agent in the organization, which is what the project tree is built
    from: a service payload names its parent by id, never by name."""
    data, error = await API.agents(org=org, include=include)
    if error:
        raise typer.Exit(1)
    return data or []


def find_project(services: list[dict], project_name: str, org: str | None) -> dict:
    """The named agent, refusing one that is an environment of another agent."""
    project = next((s for s in services if s.get("name") == project_name), None)
    if not project:
        console.error(f"Agent '{project_name}' not found in organization '{org}'")
        raise typer.Exit(1)
    if is_environment(project):
        names = {s.get("id"): s.get("name") for s in services}
        parent = names.get(project.get("parentServiceId"), "another agent")
        console.error(
            f"Agent '{project_name}' is the '{project.get('environmentName')}' environment of "
            f"'{parent}', so it has no environments of its own.\n"
            f"[dim]Run this against '{parent}' instead.[/dim]"
        )
        raise typer.Exit(1)
    return project


def find_environment(services: list[dict], project: dict, environment_name: str) -> dict:
    """A project's environment by its environment name.

    `production` is the project itself rather than one of the agents linked to
    it, so it is not among these and is refused saying so: `env promote` and
    `env remove` both name an environment to act on. `--env production` means
    the project and is answered in `resolve_agent` before it reaches here.
    """
    environments = environments_of(services, project)
    found = next((e for e in environments if e.get("environmentName") == environment_name), None)
    if found:
        return found
    have = ", ".join(e.get("environmentName") or "?" for e in environments) or "none"
    if environment_name == PRODUCTION_ENVIRONMENT_NAME:
        console.error(
            f"'{PRODUCTION_ENVIRONMENT_NAME}' is the project itself, not one of its "
            f"environments.\n[dim]Environments: {have}[/dim]"
        )
        raise typer.Exit(1)
    console.error(
        f"Project '{project.get('name')}' has no environment '{environment_name}'.\n"
        f"[dim]Environments: {have}[/dim]"
    )
    raise typer.Exit(1)


async def resolve_agent(project_name: str, environment: str | None, org: str | None) -> str:
    """The agent to act on: the project itself, or the named environment of it.

    Without `--env`, and with `--env production`, this is the name as given, so
    nothing is looked up and a command costs exactly what it did before. With
    any other environment, the project's environments are read to find the
    agent behind the name.
    """
    # Direct (non-Typer) calls leave the OptionInfo default in place, so only
    # a real string means --env was given.
    if not environment or not isinstance(environment, str):
        return project_name
    # The project is its own production environment, so this is the name as
    # given, and asking for it costs no lookup either.
    if environment == PRODUCTION_ENVIRONMENT_NAME:
        return project_name
    services = await load_services(org)
    project = find_project(services, project_name, org)
    return find_environment(services, project, environment).get("name") or project_name


ENVIRONMENT_COLUMNS = ["Environment", "Agent", "Branch", "Running Commit", "Pull Request"]


def environment_rows(services: list[dict], project: dict) -> list[tuple]:
    """One row per environment: name, agent, branch, running commit, PR.

    `production` is the first row, which is the project's own agent: it is an
    environment of the project like any other, and the only one a project is
    certain to have.
    """
    rows = []
    for environment in environments_with_production(services, project):
        git = environment.get("git") or {}
        deployed = environment.get("deployedCommit") or {}
        pull_request = git.get("pullRequestNumber")
        rows.append(
            (
                environment.get("environmentName") or "?",
                environment.get("name") or "?",
                git.get("branch") or "—",
                short_sha(deployed["sha"]) if deployed.get("sha") else "—",
                f"#{pull_request}" if pull_request else "—",
            )
        )
    return rows


def print_environments(services: list[dict], project: dict) -> None:
    """The project's environments as a table, `production` included.

    That row is always there, so the table always prints, and the pointer to
    `env add` follows it while the project has nothing else linked to it.
    """
    rows = environment_rows(services, project)
    if not console.rich_output:
        console.print_records(ENVIRONMENT_COLUMNS, rows)
    else:
        table = Table(show_header=True, header_style="bold", box=box.SIMPLE, border_style="dim")
        for column in ENVIRONMENT_COLUMNS:
            table.add_column(column)
        for row in rows:
            table.add_row(*row)
        console.print(table)
    if not environments_of(services, project):
        project_name = project.get("name")
        console.print(
            f"[dim]'{project_name}' has no environments of its own yet. Add one with "
            f"[bold]{PIPECAT_CLI_NAME} project env add {project_name} <name> "
            "--agent <agent>[/bold].[/dim]"
        )
