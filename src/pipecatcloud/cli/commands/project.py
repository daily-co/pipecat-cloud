#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Project-level commands: environments, promotion, PR previews, watch paths.

A project is an agent with environments. Its environments are agents of their
own, so everything that acts on a single agent — status, logs, sessions,
scaling, deploys, the GitHub repository link — stays on `agent`, and only the
settings that belong to the project as a whole live here.
"""

import questionary
import typer
from rich import box
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.environment_utils import (
    describe_promotion_source,
    environment_name_problem,
    environments_of,
    is_environment,
    is_preview_name,
    previews_on,
    projects_in,
    promotion_source,
)
from pipecatcloud._utils.github_utils import (
    DEFAULT_DOCKERFILE_PATH,
    binding_summary,
    short_sha,
)
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

project_cli = typer.Typer(
    name="project",
    help="Project management: environments, PR previews, and watch paths",
    no_args_is_help=True,
)

project_env_cli = typer.Typer(
    name="env",
    help="Manage a project's environments",
    no_args_is_help=True,
)
project_cli.add_typer(project_env_cli)

project_previews_cli = typer.Typer(
    name="previews",
    help="Manage a project's PR previews",
    no_args_is_help=True,
)
project_cli.add_typer(project_previews_cli)

project_watch_paths_cli = typer.Typer(
    name="watch-paths",
    help="Manage a project's watch paths",
    no_args_is_help=True,
)
project_cli.add_typer(project_watch_paths_cli)

ORGANIZATION_OPTION = typer.Option(None, "--organization", "-o", help="Organization to act in")

PROJECT_ARGUMENT = typer.Argument(help="Name of the project e.g. 'my-agent'")


async def _load_services(org: str | None, include: list[str]) -> list[dict]:
    """Every agent in the organization, which is what the project tree is built
    from: a service payload names its parent by id, never by name."""
    data, error = await API.agents(org=org, include=include)
    if error:
        raise typer.Exit(1)
    return data or []


def _find_project(services: list[dict], project_name: str, org: str | None) -> dict:
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


def _find_environment(services: list[dict], project: dict, environment_name: str) -> dict:
    """A project's environment by its environment name."""
    environments = environments_of(services, project)
    found = next((e for e in environments if e.get("environmentName") == environment_name), None)
    if found:
        return found
    have = ", ".join(e.get("environmentName") or "?" for e in environments) or "none"
    console.error(
        f"Project '{project.get('name')}' has no environment '{environment_name}'.\n"
        f"[dim]Environments: {have}[/dim]"
    )
    raise typer.Exit(1)


def _environment_rows(services: list[dict], project: dict) -> list[tuple]:
    """One row per environment: name, agent, branch, running commit, PR."""
    rows = []
    for environment in environments_of(services, project):
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


ENVIRONMENT_COLUMNS = ["Environment", "Agent", "Branch", "Running Commit", "Pull Request"]


def _print_environments(services: list[dict], project: dict) -> None:
    rows = _environment_rows(services, project)
    project_name = project.get("name")
    if not rows:
        console.print(
            f"[yellow]Project '{project_name}' has no environments.[/yellow]\n"
            f"[dim]Add one with [bold]{PIPECAT_CLI_NAME} project env add {project_name} "
            "<name> --agent <agent>[/bold].[/dim]"
        )
        return
    if not console.rich_output:
        console.print_records(ENVIRONMENT_COLUMNS, rows)
        return
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE, border_style="dim")
    for column in ENVIRONMENT_COLUMNS:
        table.add_column(column)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _project_settings_rows(project: dict) -> list[tuple[str, str]]:
    """The project's own settings: its binding, watch paths, and previews."""
    git = project.get("git") or {}
    rows = [("Region", str(project.get("region") or "—"))]
    if git:
        rows += [
            ("GitHub Repository", str(git.get("repoFullName") or "—")),
            ("GitHub Branch", str(git.get("branch") or "—")),
            ("Dockerfile Path", str(git.get("dockerfilePath") or DEFAULT_DOCKERFILE_PATH)),
            ("Auto-deploy On Push", "yes" if git.get("autoDeploy", True) else "no"),
            ("Watch Paths", ", ".join(git.get("watchPaths") or []) or "all files"),
        ]
        secret_set = git.get("previewSecretSet")
        rows.append(("PR Previews", f"on (secrets: {secret_set})" if secret_set else "off"))
        if secret_set:
            rows.append(
                (
                    "Preview Bot Pull Requests",
                    "yes" if git.get("previewBotPullRequests") else "no",
                )
            )
    else:
        rows.append(("GitHub Repository", "— (not connected)"))
    return rows


@project_cli.command(name="list", help="List projects in an organization.")
@synchronizer.create_blocking
@requires_login
async def list_projects(organization: str = ORGANIZATION_OPTION):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching projects for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        services = await _load_services(org, include=["git"])

    projects = projects_in(services)
    if not projects:
        console.error(
            f"No projects found for organization '{org}'\n\n"
            f"[dim]Deploy an agent first with[/dim] [bold cyan]{PIPECAT_CLI_NAME} deploy[/bold cyan]"
        )
        raise typer.Exit(1)

    if console.json_output:
        console.output_json({"projects": projects})
        return

    columns = ["Name", "Region", "Environments", "PR Previews", "GitHub"]
    rows = []
    for project in projects:
        environments = environments_of(services, project)
        named = [e for e in environments if not is_preview_name(e.get("environmentName"))]
        open_previews = len(environments) - len(named)
        rows.append(
            (
                project.get("name"),
                project.get("region"),
                str(len(named)),
                (f"on ({open_previews} open)" if previews_on(project) else "off"),
                binding_summary(project.get("git")),
            )
        )

    if not console.rich_output:
        console.print_records(
            columns, rows, title=f"Projects for organization: {org} ({len(rows)} results)"
        )
        return

    table = Table(show_header=True, show_lines=True, border_style="dim", box=box.SIMPLE)
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(f"[bold]{row[0]}[/bold]", *row[1:])
    console.success(
        table, title=f"Projects for organization: {org}", title_extra=f"{len(rows)} results"
    )


@project_cli.command(name="status", help="Show a project's settings and its environments.")
@synchronizer.create_blocking
@requires_login
async def project_status(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(f"[dim]Looking up project '{project_name}'[/dim]", spinner="dots"):
        services = await _load_services(org, include=["git"])

    project = _find_project(services, project_name, org)

    if console.json_output:
        console.output_json(
            {"project": project, "environments": environments_of(services, project)}
        )
        return

    if not console.rich_output:
        console.print(f"Project: {project_name}")
        for label, value in _project_settings_rows(project):
            console.print(f"{label}: {value}")
        _print_environments(services, project)
        return

    settings = Table(show_header=False, box=box.SIMPLE)
    settings.add_column("Key")
    settings.add_column("Value")
    for label, value in _project_settings_rows(project):
        settings.add_row(f"[bold]{label}:[/bold]", value)
    console.success(settings, title=f"Project: {project_name}")
    _print_environments(services, project)


@project_env_cli.command(name="list", help="List a project's environments.")
@synchronizer.create_blocking
@requires_login
async def env_list(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(f"[dim]Fetching environments of '{project_name}'[/dim]", spinner="dots"):
        services = await _load_services(org, include=["git"])

    project = _find_project(services, project_name, org)

    if console.json_output:
        console.output_json({"environments": environments_of(services, project)})
        return

    _print_environments(services, project)


@project_env_cli.command(
    name="add",
    help="Link an existing agent as an environment of a project.",
)
@synchronizer.create_blocking
@requires_login
async def env_add(
    project_name: str = PROJECT_ARGUMENT,
    environment_name: str = typer.Argument(
        help="What to call it among the project's environments e.g. 'staging'"
    ),
    agent_name: str = typer.Option(
        ..., "--agent", help="Name of the existing agent to link as this environment"
    ),
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    # The same rules the API applies, so a typo fails before a round trip.
    if problem := environment_name_problem(environment_name):
        console.error(problem)
        raise typer.Exit(1)

    with console.status(
        f"[dim]Linking agent [bold]'{agent_name}'[/bold] as [bold]{project_name} > "
        f"{environment_name}[/bold][/dim]",
        spinner="dots",
    ):
        link, error = await API.environment_link(
            agent_name=agent_name,
            org=org,
            parent=project_name,
            environment_name=environment_name,
        )
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(link or {})
        return

    console.success(
        f"Agent '{agent_name}' is now the '{environment_name}' environment of "
        f"'{project_name}'.\n"
        "[dim]Linking changes nothing about what it runs. It keeps its own repository "
        "link, secrets and scaling, and follows the project's watch paths.[/dim]"
    )


@project_env_cli.command(
    name="remove",
    help="Unlink an environment from its project. The agent keeps running.",
)
@synchronizer.create_blocking
@requires_login
async def env_remove(
    project_name: str = PROJECT_ARGUMENT,
    environment_name: str = typer.Argument(help="Environment to unlink e.g. 'staging'"),
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(f"[dim]Looking up environment '{environment_name}'[/dim]", spinner="dots"):
        services = await _load_services(org, include=["git"])

    project = _find_project(services, project_name, org)
    environment = _find_environment(services, project, environment_name)
    agent_name = environment.get("name") or ""

    with console.status(
        f"[dim]Unlinking [bold]'{agent_name}'[/bold] from [bold]'{project_name}'[/bold][/dim]",
        spinner="dots",
    ):
        result, error = await API.environment_unlink(agent_name=agent_name, org=org)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(result or {})
        return

    console.success(
        f"Unlinked '{agent_name}' from '{project_name}'. It is an ordinary agent now, "
        "running exactly as it was.\n"
        "[dim]The project's watch paths it followed are its own from here.[/dim]"
    )


@project_env_cli.command(
    name="promote",
    help="Deploy an environment's running code onto the project.",
)
@synchronizer.create_blocking
@requires_login
async def env_promote(
    project_name: str = PROJECT_ARGUMENT,
    environment_name: str = typer.Argument(help="Environment to promote e.g. 'staging'"),
    organization: str = ORGANIZATION_OPTION,
    force: bool = typer.Option(False, "--force", "-f", help="Bypass prompt for confirmation"),
):
    org = organization or config.get("org")

    with console.status(f"[dim]Looking up environment '{environment_name}'[/dim]", spinner="dots"):
        services = await _load_services(org, include=["git", "activeDeployment"])

    project = _find_project(services, project_name, org)
    environment = _find_environment(services, project, environment_name)

    source = promotion_source(environment)
    if not source:
        console.error(f"Environment '{environment_name}' has nothing deployed to promote.")
        raise typer.Exit(1)
    what = describe_promotion_source(source)

    if not force:
        console.require_interactive("--force")
        if not await questionary.confirm(
            f"Deploy {what} from '{environment_name}' onto '{project_name}'? "
            "Active sessions are not interrupted."
        ).ask_async():
            console.print("[bold]Aborting promotion[/bold]")
            raise typer.Exit(1)

    with console.status(
        f"[dim]Promoting [bold]{environment_name}[/bold] to [bold]{project_name}[/bold][/dim]",
        spinner="dots",
    ):
        result, error = await API.agent_update_source(agent_name=project_name, org=org, **source)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(result or {})
        return

    console.success(
        f"Promoted {what} from '{environment_name}' to '{project_name}'.\n"
        f"[dim]Promotion moves code only: '{project_name}' keeps its own secrets, scaling "
        f"and profile. Follow the rollout with [bold]{PIPECAT_CLI_NAME} agent status "
        f"{project_name}[/bold].[/dim]"
    )


@project_previews_cli.command(name="status", help="Show whether a project's PR previews are on.")
@synchronizer.create_blocking
@requires_login
async def previews_status(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(f"[dim]Looking up project '{project_name}'[/dim]", spinner="dots"):
        services = await _load_services(org, include=["git"])

    project = _find_project(services, project_name, org)
    git = project.get("git") or {}
    previews = [
        e for e in environments_of(services, project) if is_preview_name(e.get("environmentName"))
    ]

    if console.json_output:
        console.output_json(
            {
                "previews": {
                    "enabled": previews_on(project),
                    "secretSet": git.get("previewSecretSet"),
                    "botPullRequests": bool(git.get("previewBotPullRequests")),
                    "watchPaths": git.get("watchPaths") or [],
                },
                "openPreviews": previews,
            }
        )
        return

    if not previews_on(project):
        console.print(
            f"PR previews are off for '{project_name}'.\n"
            f"[dim]Turn them on with [bold]{PIPECAT_CLI_NAME} project previews enable "
            f"{project_name} --secret-set <set>[/bold].[/dim]"
        )
        return

    console.print(f"PR previews are on for '{project_name}'.")
    console.print(f"Secret Set: {git.get('previewSecretSet')}")
    console.print(f"Bot Pull Requests: {'yes' if git.get('previewBotPullRequests') else 'no'}")
    console.print(f"Watch Paths: {', '.join(git.get('watchPaths') or []) or 'all files'}")
    _print_environments(services, project)


@project_previews_cli.command(
    name="enable",
    help="Turn a project's PR previews on, with the secret set they mount.",
)
@synchronizer.create_blocking
@requires_login
async def previews_enable(
    project_name: str = PROJECT_ARGUMENT,
    secret_set: str = typer.Option(
        ..., "--secret-set", help="Secret set every preview mounts in place of the project's"
    ),
    bot_pull_requests: bool = typer.Option(
        None,
        "--bot-pull-requests/--no-bot-pull-requests",
        help="Give pull requests opened by bots previews too (default: disabled)",
    ),
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Turning PR previews on for [bold]'{project_name}'[/bold][/dim]", spinner="dots"
    ):
        result, error = await API.previews_update(
            agent_name=project_name,
            org=org,
            secret_set=secret_set,
            bot_pull_requests=bot_pull_requests,
        )
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(result or {})
        return

    console.success(
        f"PR previews are on for '{project_name}', mounting secret set '{secret_set}'.\n"
        "[dim]Every same-repo pull request into the branch the project deploys from gets an "
        "environment of its own, scaled to at most one instance. A pull request that touches "
        "none of the project's watch paths gets none.[/dim]"
    )


@project_previews_cli.command(
    name="disable", help="Turn a project's PR previews off and delete the ones it has."
)
@synchronizer.create_blocking
@requires_login
async def previews_disable(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Turning PR previews off for [bold]'{project_name}'[/bold][/dim]", spinner="dots"
    ):
        result, error = await API.previews_update(
            agent_name=project_name, org=org, secret_set=None, bot_pull_requests=None
        )
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(result or {})
        return

    console.success(
        f"PR previews are off for '{project_name}'. The previews it has are deleted "
        "within a few minutes."
    )


@project_watch_paths_cli.command(name="show", help="Show a project's watch paths.")
@synchronizer.create_blocking
@requires_login
async def watch_paths_show(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(f"[dim]Looking up project '{project_name}'[/dim]", spinner="dots"):
        services = await _load_services(org, include=["git"])

    project = _find_project(services, project_name, org)
    git = project.get("git") or {}
    watch_paths = git.get("watchPaths") or []

    if console.json_output:
        console.output_json({"watchPaths": watch_paths})
        return

    if not git:
        console.error(
            f"Project '{project_name}' has no GitHub repository connected, so it has no "
            "watch paths."
        )
        raise typer.Exit(1)

    if not watch_paths:
        console.print(
            f"'{project_name}' has no watch paths, so every change deploys.\n"
            f"[dim]Set them with [bold]{PIPECAT_CLI_NAME} project watch-paths set "
            f"{project_name} <path>...[/bold].[/dim]"
        )
        return

    console.print(f"Watch paths for '{project_name}':")
    for path in watch_paths:
        console.print(path)


@project_watch_paths_cli.command(
    name="set",
    help="Set a project's watch paths, replacing the ones it has.",
)
@synchronizer.create_blocking
@requires_login
async def watch_paths_set(
    project_name: str = PROJECT_ARGUMENT,
    paths: list[str] = typer.Argument(
        ..., help="Repository paths a change must touch to deploy e.g. 'bot/' 'shared/'"
    ),
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Saving watch paths for [bold]'{project_name}'[/bold][/dim]", spinner="dots"
    ):
        result, error = await API.watch_paths_update(
            agent_name=project_name, org=org, watch_paths=list(paths)
        )
        if error:
            raise typer.Exit(1)

    saved = (result or {}).get("watchPaths") or list(paths)

    if console.json_output:
        console.output_json({"watchPaths": saved})
        return

    console.success(
        f"Watch paths for '{project_name}': {', '.join(saved)}\n"
        "[dim]A push that touches none of them does not deploy, and a pull request that "
        "touches none of them gets no preview. The Dockerfile and .dockerignore always "
        "count. The project's environments and previews follow this list.[/dim]"
    )


@project_watch_paths_cli.command(
    name="clear", help="Clear a project's watch paths, so every change deploys."
)
@synchronizer.create_blocking
@requires_login
async def watch_paths_clear(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Clearing watch paths for [bold]'{project_name}'[/bold][/dim]", spinner="dots"
    ):
        result, error = await API.watch_paths_update(
            agent_name=project_name, org=org, watch_paths=[]
        )
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(result or {"watchPaths": []})
        return

    console.success(
        f"Cleared the watch paths for '{project_name}'. Every change to the connected "
        "branch deploys again."
    )
