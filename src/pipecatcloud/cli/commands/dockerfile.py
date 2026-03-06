#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Dockerfile generation command for Pipecat Cloud CLI.
"""

from pathlib import Path
from typing import Optional

import questionary
import typer
from rich.panel import Panel
from rich.syntax import Syntax

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.dockerfile_gen import (
    ProjectType,
    detect_entrypoint,
    detect_project_type,
    generate_dockerfile,
)


def create_dockerfile_command(app: typer.Typer):
    """Add dockerfile command to the main CLI app."""

    @app.command(
        name="dockerfile",
        help="Generate a Dockerfile for your Pipecat agent",
        rich_help_panel="Commands",
    )
    @synchronizer.create_blocking
    async def dockerfile(
        output: str = typer.Option(
            "Dockerfile",
            "--output",
            "-o",
            help="Output file path",
        ),
        project_type: Optional[str] = typer.Option(
            None,
            "--type",
            "-t",
            help="Project type (uv, pip, poetry). Auto-detected if not specified.",
        ),
        entrypoint: Optional[str] = typer.Option(
            None,
            "--entrypoint",
            "-e",
            help="Python entrypoint file (e.g., bot.py). Auto-detected if not specified.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            "-f",
            help="Overwrite existing Dockerfile without prompting",
        ),
        build_dir: str = typer.Option(
            ".",
            "--build-dir",
            "-d",
            help="Directory to analyze for project detection",
        ),
    ):
        """Generate a Dockerfile optimized for Pipecat Cloud deployment."""

        # Detect project type if not specified
        if project_type:
            try:
                detected_type = ProjectType(project_type.lower())
            except ValueError:
                console.error(
                    f"Unknown project type: '{project_type}'\n\n"
                    f"Supported types: uv, pip, poetry"
                )
                return typer.Exit(1)
        else:
            detected_type = detect_project_type(build_dir)

        if detected_type == ProjectType.UNKNOWN:
            console.print(
                Panel(
                    "Could not automatically detect project type.\n\n"
                    "Please ensure your project has one of:\n"
                    "  • [bold]uv.lock[/bold] (uv project)\n"
                    "  • [bold]requirements.txt[/bold] (pip project)\n"
                    "  • [bold]poetry.lock[/bold] (poetry project)\n\n"
                    "Or specify the type with [bold]--type[/bold]",
                    title="[yellow]Detection Failed[/yellow]",
                    title_align="left",
                    border_style="yellow",
                )
            )

            # Offer to select manually
            type_choice = await questionary.select(
                "Select project type:",
                choices=[
                    questionary.Choice("uv (recommended)", value="uv"),
                    questionary.Choice("pip", value="pip"),
                    questionary.Choice("poetry", value="poetry"),
                    questionary.Choice("Cancel", value=None),
                ],
            ).ask_async()

            if not type_choice:
                console.cancel()
                return typer.Exit()

            detected_type = ProjectType(type_choice)

        # Detect entrypoint if not specified
        detected_entrypoint = entrypoint or detect_entrypoint(build_dir)

        if not detected_entrypoint:
            console.print("[yellow]Could not detect entrypoint file.[/yellow]")

            detected_entrypoint = await questionary.text(
                "Enter the Python file to run:",
                default="bot.py",
            ).ask_async()

            if not detected_entrypoint:
                console.cancel()
                return typer.Exit()

        # Check if entrypoint exists (warning only)
        entrypoint_path = Path(build_dir) / detected_entrypoint
        if not entrypoint_path.exists():
            console.print(
                f"[yellow]Warning: Entrypoint '{detected_entrypoint}' "
                f"not found in {build_dir}[/yellow]"
            )
            if not force:
                if not typer.confirm("Continue anyway?", default=False):
                    console.cancel()
                    return typer.Exit()

        # Generate Dockerfile
        try:
            dockerfile_content = generate_dockerfile(detected_type, detected_entrypoint)
        except ValueError as e:
            console.error(str(e))
            return typer.Exit(1)

        # Check if output file exists
        output_path = Path(output)
        if output_path.exists() and not force:
            # Show preview
            console.print(
                Panel(
                    Syntax(dockerfile_content, "dockerfile", theme="monokai", line_numbers=True),
                    title="Generated Dockerfile",
                    title_align="left",
                )
            )

            if not typer.confirm(f"\n'{output}' already exists. Overwrite?", default=False):
                console.cancel()
                return typer.Exit()

        # Write Dockerfile
        output_path.write_text(dockerfile_content)

        console.success(
            f"[bold white]Project type:[/bold white] [green]{detected_type.value}[/green]\n"
            f"[bold white]Entrypoint:[/bold white] [green]{detected_entrypoint}[/green]\n"
            f"[bold white]Output:[/bold white] [green]{output}[/green]\n\n"
            f"[dim]You can now deploy with:[/dim] [bold]pcc deploy <agent-name>[/bold]",
            title="Dockerfile Generated",
        )
