#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import subprocess
from enum import Enum
from typing import Optional

import typer
from rich.panel import Panel

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.deploy_utils import DeployConfigParams, with_deploy_config
from pipecatcloud.cli import PIPECAT_CLI_NAME

docker_cli = typer.Typer(
    name="docker", help="Docker build and push utilities", no_args_is_help=True
)


class RegistryType(str, Enum):
    DOCKERHUB = "dockerhub"
    CUSTOM = "custom"


def _suggest_and_install_binfmt():
    """Suggest and optionally install binfmt_misc support for cross-platform builds."""
    console.print("\n[yellow]⚠️  Cross-platform build support missing[/yellow]")
    console.print(
        "[yellow]   Building ARM64 images on x86_64 requires QEMU and binfmt_misc support.[/yellow]"
    )
    console.print(
        "\n[yellow]   This is a one-time setup that persists across Docker restarts.[/yellow]"
    )
    console.print(
        "\n[dim]   The following command will be run (requires Docker --privileged flag):[/dim]"
    )
    console.print("[dim]   docker run --privileged --rm tonistiigi/binfmt --install all[/dim]")

    if typer.confirm("\nWould you like to install binfmt support now?", default=True):
        console.print("\n[cyan]Installing binfmt support...[/cyan]")
        install_cmd = [
            "docker",
            "run",
            "--privileged",
            "--rm",
            "tonistiigi/binfmt",
            "--install",
            "all",
        ]
        if run_docker_command(
            install_cmd, "Setting up cross-platform build support", stream_output=False
        ):
            console.success("✓ binfmt support installed! You can now retry your build.")
        else:
            console.error("Failed to install binfmt support.")
            console.print("\n[yellow]You can install it manually by running:[/yellow]")
            console.print(
                "[yellow]   docker run --privileged --rm tonistiigi/binfmt --install all[/yellow]"
            )
    else:
        console.print("\n[yellow]Skipping installation. To install manually, run:[/yellow]")
        console.print(
            "[yellow]   docker run --privileged --rm tonistiigi/binfmt --install all[/yellow]"
        )


def _provide_error_hints(stderr: str, stdout: str, command: list[str], registry_info: dict = None):
    """Provide helpful error hints only when Docker's message isn't clear enough."""
    stderr_lower = stderr.lower()
    stdout_lower = stdout.lower()
    combined_output = f"{stderr_lower} {stdout_lower}"

    # Check for exec format error (cross-platform build issue)
    if (
        "exec format error" in combined_output
        or "exec /bin/sh: exec format error" in combined_output
    ):
        _suggest_and_install_binfmt()
        return

    # Only add hints for authentication errors where the solution isn't obvious
    if _is_auth_error(stderr_lower) and "push" in " ".join(command):
        _suggest_docker_login(registry_info)


def _is_auth_error(stderr_lower: str) -> bool:
    """Check if the error is authentication related."""
    auth_keywords = ["unauthorized", "access denied", "denied"]
    return any(keyword in stderr_lower for keyword in auth_keywords)


def _suggest_docker_login(registry_info: dict = None):
    """Suggest the appropriate docker login command."""
    console.print("\n[yellow]💡 You need to authenticate with the registry[/yellow]")

    if registry_info and registry_info.get("type") == "custom" and registry_info.get("url"):
        console.print(f"[yellow]   docker login {registry_info['url']}[/yellow]")
    else:
        console.print("[yellow]   docker login[/yellow]")


def run_docker_command(
    command: list[str], description: str, stream_output: bool = True, registry_info: dict = None
) -> bool:
    """Run a docker command and handle output/errors."""
    try:
        console.print(f"[dim]{description}...[/dim]")

        if stream_output:
            # Stream output in real-time for build commands
            # Collect output for error analysis
            output_lines = []
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            # Stream output line by line
            for line in process.stdout:
                output_lines.append(line)
                console.print(f"[dim]{line.rstrip()}[/dim]")

            process.wait()
            if process.returncode != 0:
                # Create a pseudo-exception with captured output
                error = subprocess.CalledProcessError(process.returncode, command)
                error.output = "".join(output_lines)
                error.stdout = error.output
                error.stderr = ""
                raise error
        else:
            # Capture output for push commands (less verbose)
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            if result.stdout.strip():
                console.print(f"[dim]{result.stdout.strip()}[/dim]")

        return True
    except subprocess.CalledProcessError as e:
        console.error(f"Docker command failed: {' '.join(command)}")
        stdout = getattr(e, "stdout", "") or getattr(e, "output", "") or ""
        stderr = getattr(e, "stderr", "") or ""

        if stdout:
            console.print(f"[red]stdout: {stdout}[/red]")
        if stderr:
            console.print(f"[red]stderr: {stderr}[/red]")

        # Provide helpful hints for common errors
        _provide_error_hints(stderr, stdout, command, registry_info)
        return False
    except FileNotFoundError:
        console.error("Docker not found. Please ensure Docker is installed and in your PATH.")
        return False


def _build_image_name(
    registry_type: RegistryType, username: str, agent_name: str, registry_url: Optional[str] = None
) -> str:
    """Build the full image name based on registry type."""
    if registry_type == RegistryType.DOCKERHUB:
        return f"{username}/{agent_name}"
    elif registry_type == RegistryType.CUSTOM:
        if not registry_url:
            raise ValueError("registry_url is required for custom registries")
        return f"{registry_url}/{username}/{agent_name}"
    else:
        raise ValueError(f"Unsupported registry type: {registry_type}")


@docker_cli.command(name="build-push", help="Build, tag, and push Docker image")
@synchronizer.create_blocking
@with_deploy_config
async def build_push(
    deploy_config=typer.Option(None, hidden=True),
    agent_name: str = typer.Argument(
        None, help="Name of the agent to build image for e.g. 'my-agent'", show_default=False
    ),
    registry: RegistryType = typer.Option(
        None,
        "--registry",
        "-r",
        help="Registry type to push to",
        rich_help_panel="Registry Configuration",
    ),
    registry_username: str = typer.Option(
        None,
        "--username",
        "-u",
        help="Registry username",
        rich_help_panel="Registry Configuration",
    ),
    registry_url: str = typer.Option(
        None,
        "--registry-url",
        help="Custom registry URL (required for custom registries)",
        rich_help_panel="Registry Configuration",
    ),
    version: str = typer.Option(
        None,
        "--version",
        "-v",
        help="Version tag for the image",
        rich_help_panel="Build Configuration",
    ),
    no_push: bool = typer.Option(
        False,
        "--no-push",
        help="Build and tag only, do not push to registry",
        rich_help_panel="Build Configuration",
    ),
    no_latest: bool = typer.Option(
        False,
        "--no-latest",
        help="Do not tag as 'latest'",
        rich_help_panel="Build Configuration",
    ),
):
    """Build, tag, and optionally push a Docker image for your agent."""

    # Load values from deployment config file (if one exists)
    partial_config = deploy_config or DeployConfigParams()
    docker_config = getattr(partial_config, "docker_config", {})

    # Parse image field to extract registry info (if present)
    parsed_registry = None
    parsed_username = None
    parsed_agent_name = None
    parsed_version = None
    parsed_registry_url = None

    if partial_config.image:
        # Parse image like "markatdaily/voice-starter:0.5" or "registry.com/user/app:v1.0"
        image_parts = partial_config.image.split("/")

        if len(image_parts) == 2:
            # Format: "username/repo:tag" (Docker Hub)
            parsed_registry = "dockerhub"
            parsed_username = image_parts[0]
            repo_and_tag = image_parts[1]
        elif len(image_parts) == 3:
            # Format: "registry.com/username/repo:tag" (Custom registry)
            parsed_registry = "custom"
            parsed_registry_url = image_parts[0]
            parsed_username = image_parts[1]
            repo_and_tag = image_parts[2]
        else:
            # Just "repo:tag" - use for agent name only
            repo_and_tag = partial_config.image

        # Extract agent name and version from "repo:tag"
        if ":" in repo_and_tag:
            parsed_agent_name, parsed_version = repo_and_tag.split(":", 1)
        else:
            parsed_agent_name = repo_and_tag
            parsed_version = "latest"

    # Resolve final values with precedence: CLI args > parsed from image > docker config > defaults
    final_agent_name = agent_name or parsed_agent_name or partial_config.agent_name
    final_registry = registry or parsed_registry or docker_config.get("registry")
    final_username = registry_username or parsed_username or docker_config.get("registry_username")
    final_registry_url = registry_url or parsed_registry_url or docker_config.get("registry_url")
    final_version = version or parsed_version or "latest"
    final_no_latest = no_latest or not docker_config.get("auto_latest", True)

    # Validate required parameters
    if not final_agent_name:
        console.error("Agent name is required. Provide via argument or pcc-deploy.toml")
        return typer.Exit(1)

    if not no_push:
        if not final_registry:
            console.error(
                "Registry type is required when pushing. Use --registry or configure in pcc-deploy.toml"
            )
            return typer.Exit(1)

        if not final_username:
            console.error(
                "Registry username is required when pushing. Use --username or configure in pcc-deploy.toml"
            )
            return typer.Exit(1)

        if final_registry == RegistryType.CUSTOM and not final_registry_url:
            console.error(
                "Registry URL is required for custom registries. Use --registry-url or configure in pcc-deploy.toml"
            )
            return typer.Exit(1)

    # Build image name
    if no_push:
        # For local builds, just use agent name
        base_image_name = final_agent_name
    else:
        try:
            base_image_name = _build_image_name(
                RegistryType(final_registry), final_username, final_agent_name, final_registry_url
            )
        except ValueError as e:
            console.error(str(e))
            return typer.Exit(1)

    # Prepare image tags
    version_tag = f"{base_image_name}:{final_version}"
    latest_tag = f"{base_image_name}:latest"

    # Build Docker command
    build_tags = ["-t", version_tag]
    if not final_no_latest:
        build_tags.extend(["-t", latest_tag])

    build_command = [
        "docker",
        "buildx",
        "build",
        "--platform=linux/arm64",
        "--load",
        *build_tags,
        ".",
    ]

    # Display build configuration
    tags_display = [version_tag]
    if not final_no_latest:
        tags_display.append(latest_tag)

    console.print(
        Panel(
            f"[bold white]Agent:[/bold white] [green]{final_agent_name}[/green]\n"
            f"[bold white]Platform:[/bold white] [green]linux/arm64[/green]\n"
            f"[bold white]Tags:[/bold white] [green]{', '.join(tags_display)}[/green]\n"
            f"[bold white]Push:[/bold white] {'[red]No[/red]' if no_push else f'[green]Yes ({final_registry})[/green]'}",
            title="Docker Build Configuration",
            title_align="left",
            border_style="yellow",
        )
    )

    if not typer.confirm("Do you want to proceed with the build?", default=True):
        console.cancel()
        return typer.Exit()

    # Execute build
    console.print("\n[bold cyan]Building Docker image...[/bold cyan]")
    if not run_docker_command(build_command, f"Building {final_agent_name}", stream_output=True):
        return typer.Exit(1)

    console.success(f"Successfully built image(s): {', '.join(tags_display)}")

    # Push if requested
    if not no_push:
        console.print(f"\n[bold cyan]Pushing to {final_registry}...[/bold cyan]")

        # Prepare registry info for better error messages
        registry_info = {
            "type": final_registry,
            "url": final_registry_url if final_registry == "custom" else None,
        }

        # Push version tag
        if not run_docker_command(
            ["docker", "push", version_tag],
            f"Pushing {version_tag}",
            stream_output=False,
            registry_info=registry_info,
        ):
            return typer.Exit(1)

        # Push latest tag if created
        if not final_no_latest:
            if not run_docker_command(
                ["docker", "push", latest_tag],
                f"Pushing {latest_tag}",
                stream_output=False,
                registry_info=registry_info,
            ):
                return typer.Exit(1)

        pushed_tags = [version_tag]
        if not final_no_latest:
            pushed_tags.append(latest_tag)

        console.success(f"Successfully pushed: {', '.join(pushed_tags)}")
    else:
        console.print("\n[yellow]Skipping push (--no-push specified)[/yellow]")

    # Display next steps
    if not no_push:
        console.print("\n[dim]Your image is now available at:[/dim]")
        for tag in tags_display:
            console.print(f"  [bold]{tag}[/bold]")

        console.print("\n[dim]To deploy this image, update your pcc-deploy.toml:[/dim]")
        console.print(f'  [bold]image = "{version_tag}"[/bold]')
        console.print("\n[dim]Then run:[/dim]")
        console.print(f"  [bold]{PIPECAT_CLI_NAME} deploy[/bold]")


def create_docker_command(app: typer.Typer):
    """Add docker command to the main CLI app."""
    app.add_typer(docker_cli, rich_help_panel="Commands")
