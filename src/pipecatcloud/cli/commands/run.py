#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import typer

from pipecatcloud._utils.async_utils import synchronizer

# ----- Run


def create_run_command(app: typer.Typer):
    @app.command(name="run", help="Run an agent locally")
    @synchronizer.create_blocking
    async def run(
        entrypoint: str,
        host: str = typer.Option(
            "0.0.0.0",
            "--host",
            help="Host to run the agent on",
            rich_help_panel="Run Configuration",
        ),
        port: int = typer.Option(
            8000,
            "--port",
            help="Port to run the agent on",
            rich_help_panel="Run Configuration",
        ),
    ):
        # from pipecatcloud._utils.local_runner import start_server
        # await start_server(entrypoint, host, port)
        print("Not yet implemented")

    return run
