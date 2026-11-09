#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""The project-level settings: environments, promotion, PR previews, watch paths.

These are the things the API refuses on an environment, because they belong to
the project as a whole. The per-agent commands are in agent.py, on the same
group.
"""

import questionary
import typer

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.environment_utils import (
    describe_promotion_source,
    environment_name_problem,
    environments_of,
    is_preview_name,
    previews_on,
    promotion_source,
)
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.commands.project_group import (
    ORGANIZATION_OPTION,
    find_environment,
    find_project,
    load_services,
    print_environments,
    project_cli,
)
from pipecatcloud.cli.config import config

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

PROJECT_ARGUMENT = typer.Argument(help="Name of the project e.g. 'my-bot'")


@project_env_cli.command(name="list", help="List a project's environments.")
@synchronizer.create_blocking
@requires_login
async def env_list(
    project_name: str = PROJECT_ARGUMENT,
    organization: str = ORGANIZATION_OPTION,
):
    org = organization or config.get("org")

    with console.status(f"[dim]Fetching environments of '{project_name}'[/dim]", spinner="dots"):
        services = await load_services(org, include=["git"])

    project = find_project(services, project_name, org)

    if console.json_output:
        console.output_json({"environments": environments_of(services, project)})
        return

    print_environments(services, project)


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
        "link, secrets and scaling, and follows the project's watch paths.[/dim]\n"
        f"[dim]Address it with [bold]{PIPECAT_CLI_NAME} project status {project_name} "
        f"--env {environment_name}[/bold].[/dim]"
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
        services = await load_services(org, include=["git"])

    project = find_project(services, project_name, org)
    environment = find_environment(services, project, environment_name)
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
        f"Unlinked '{agent_name}' from '{project_name}'. It is a project of its own now, "
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
        services = await load_services(org, include=["git", "activeDeployment"])

    project = find_project(services, project_name, org)
    environment = find_environment(services, project, environment_name)

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
        # `source` is the HTTP payload's shape; the wrapper takes Python names.
        result, error = await API.agent_update_source(
            agent_name=project_name,
            org=org,
            build_id=source.get("buildId"),
            image=source.get("image"),
        )
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(result or {})
        return

    console.success(
        f"Promoted {what} from '{environment_name}' to '{project_name}'.\n"
        f"[dim]Promotion moves code only: '{project_name}' keeps its own secrets, scaling "
        f"and profile. Follow the rollout with [bold]{PIPECAT_CLI_NAME} project status "
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
        services = await load_services(org, include=["git"])

    project = find_project(services, project_name, org)
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
    print_environments(services, project)


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
        services = await load_services(org, include=["git"])

    project = find_project(services, project_name, org)
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
        ...,
        help="Glob patterns a change must match to deploy, relative to the "
        "repository root, e.g. 'bot/**' 'shared/**'. A leading '!' excludes.",
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
