import contextlib
import logging
import socket

from aiohttp.web import Application
from aiohttp.web_runner import AppRunner, SockSite

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def run_temporary_http_server(app: Application):
    sock = socket.socket()
    try:
        sock.bind(("", 0))
        port = sock.getsockname()[1]
        host = f"http://127.0.0.1:{port}"

        runner = AppRunner(app)
        await runner.setup()
        site = SockSite(runner, sock=sock)
        await site.start()
        try:
            yield host
        finally:
            await runner.cleanup()
    except Exception as e:
        logger.error(f"Error setting up temporary HTTP server: {e}")
        raise
    finally:
        sock.close()
