#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import fastapi
import uvicorn
from loguru import logger


async def start_server(entrypoint: str, host: str = "0.0.0.0", port: int = 8000):
    """Start a FastAPI server with the specified routes.

    Args:
        entrypoint (str): The entry point identifier
    """
    logger.debug(f"Starting local bot runner for {entrypoint}")
    app = fastapi.FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    # @TODO dynamically load entrypoint

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)

    await server.serve()
