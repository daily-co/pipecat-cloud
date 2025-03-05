#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import zipfile
from typing import Set

import aiohttp
import questionary
import typer

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.console_utils import console
from pipecatcloud.config import config

# ----- Init

FILES_TO_EXTRACT = {
    "pipecat-cloud-starter-main/bot.py",
    "pipecat-cloud-starter-main/Dockerfile",
    "pipecat-cloud-starter-main/requirements.txt",
    "pipecat-cloud-starter-main/pcc-deploy.toml",
    "pipecat-cloud-starter-main/README.md",
    "pipecat-cloud-starter-main/.gitignore",
    "pipecat-cloud-starter-main/env.example",
    "pipecat-cloud-starter-main/local_runner.py",
}


def check_existing_files() -> Set[str]:
    """Check which target files already exist in the current directory."""
    return {
        os.path.basename(file)
        for file in FILES_TO_EXTRACT
        if os.path.exists(os.path.basename(file))
    }


def create_init_command(app: typer.Typer):
    @app.command(name="init", help="Initialize project directory with template files")
    @synchronizer.create_blocking
    async def init():
        if not await questionary.confirm(
            "This will download the latest starter project to the current directory. Continue?"
        ).ask_async():
            console.print("[bold]Aborting init request[/bold]")
            return typer.Exit(1)

        # Check existing files more efficiently
        existing_files = check_existing_files()
        if existing_files:
            files_list = "\n".join(f"- {f}" for f in existing_files)
            if not await questionary.confirm(
                f"The following files already exist and will be overwritten:\n{files_list}\nDo you want to continue?"
            ).ask_async():
                console.print("[bold]Aborting init request[/bold]")
                return typer.Exit(1)

        zip_path = "init.zip"
        try:
            with console.status("[dim]Downloading starter project...[/dim]", spinner="dots"):
                zip_url = config.get("init_zip_url")
                if not zip_url:
                    raise ValueError("No starter project URL found in configuration")

                async with aiohttp.ClientSession() as session:
                    async with session.get(zip_url) as response:
                        if response.status != 200:
                            raise aiohttp.ClientResponseError(
                                response.request_info,
                                response.history,
                                status=response.status,
                                message=f"Failed to download starter project: {response.reason}",
                            )

                        # Stream the download to a file
                        with open(zip_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)

                # Extract files
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    for file in FILES_TO_EXTRACT:
                        try:
                            source = zip_ref.read(file)
                            target = os.path.basename(file)
                            with open(target, "wb") as f:
                                f.write(source)
                        except KeyError:
                            console.print(
                                f"[yellow]Warning:[/yellow] File {file} not found in starter project"
                            )
                        except Exception as e:
                            raise Exception(f"Failed to extract {file}: {str(e)}")

            console.success(
                "You can now start building your agent in [bold]bot.py[/bold]"
                "\n\n"
                "Follow the steps in the [bold]README.md[/bold] file to get started.",
                title="Project files downloaded successfully",
            )

        except aiohttp.ClientError as e:
            console.error(f"Network error while downloading starter project: {str(e)}")
            return typer.Exit(1)
        except zipfile.BadZipFile:
            console.error("Downloaded file is not a valid zip archive")
            return typer.Exit(1)
        except Exception as e:
            console.error(f"Error during initialization: {str(e)}")
            return typer.Exit(1)
        finally:
            # Cleanup the temporary zip file
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception as e:
                    console.print(
                        f"[yellow]Warning:[/yellow] Failed to cleanup temporary file {zip_path}: {str(e)}"
                    )

    return init
