import asyncio
from typing import Optional


class SmallWebRTCSessionManager:
    """
    Manages the waiting logic between PipecatSession and SmallWebRTCSession.

    This class provides:
      - A mechanism to wait for a WebRTC connection within a timeout period.
      - Automatic cancellation if no connection is established in time.
      - A way to complete the waiting future once the session has successfully started.
    """

    def __init__(self, timeout_seconds: int = 120):
        self._pending_future: Optional[asyncio.Future] = None
        self._timeout_task: Optional[asyncio.Task] = None
        self._timeout_seconds = timeout_seconds

    async def wait_for_webrtc(self) -> None:
        """
        Creates and waits for a future to be completed when WebRTC connection arrives.

        Raises:
            TimeoutError: If WebRTC connection is not received within the timeout period
            RuntimeError: If another waiting operation is already in progress
        """
        if self._pending_future is not None:
            raise RuntimeError("Already waiting for WebRTC connection")

        self._pending_future = asyncio.Future()

        async def timeout_handler():
            await asyncio.sleep(self._timeout_seconds)
            if self._pending_future and not self._pending_future.done():
                self._pending_future.set_exception(
                    TimeoutError(f"WebRTC connection not received within {self._timeout_seconds} seconds")
                )

        # Create and store the timeout task
        self._timeout_task = asyncio.create_task(timeout_handler())

        try:
            await self._pending_future
        finally:
            self._cleanup()

    def complete_session(self) -> bool:
        """
        Completes the waiting future.

        Returns:
            bool: True if the future was found and completed, False otherwise
        """
        if self._pending_future and not self._pending_future.done():
            # Cancel the timeout task first
            self.cancel_timeout()
            # Complete the future
            self._pending_future.set_result(True)
            return True
        return False

    def cancel_timeout(self) -> bool:
        """
        Cancels the timeout handler without completing the future.

        Returns:
            bool: True if the timeout task was found and cancelled, False otherwise
        """
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
            return True
        return False

    def _cleanup(self) -> None:
        """Cleans up all resources."""
        self._pending_future = None
        self._timeout_task = None

    def is_waiting(self) -> bool:
        """
        Checks if currently waiting for WebRTC connection.

        Returns:
            bool: True if waiting for connection, False otherwise
        """
        return self._pending_future is not None and not self._pending_future.done()
