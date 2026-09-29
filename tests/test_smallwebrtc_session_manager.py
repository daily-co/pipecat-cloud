"""Unit tests for SmallWebRTCSessionManager waiting and timeout handling."""

import asyncio
import sys
from pathlib import Path

import pytest

# Import from source, not installed package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.smallwebrtc.session_manager import SmallWebRTCSessionManager


async def _wait_until_waiting(manager: SmallWebRTCSessionManager) -> None:
    while not manager.is_waiting():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_complete_session_releases_the_waiter():
    manager = SmallWebRTCSessionManager(timeout_seconds=5)
    waiter = asyncio.create_task(manager.wait_for_webrtc())
    await _wait_until_waiting(manager)

    assert manager.complete_session() is True
    await waiter
    assert manager.is_waiting() is False


@pytest.mark.asyncio
async def test_wait_times_out():
    manager = SmallWebRTCSessionManager(timeout_seconds=0.05)

    with pytest.raises(TimeoutError):
        await manager.wait_for_webrtc()

    assert manager.is_waiting() is False


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_time_out_the_next_wait():
    manager = SmallWebRTCSessionManager(timeout_seconds=0.2)

    first = asyncio.create_task(manager.wait_for_webrtc())
    await _wait_until_waiting(manager)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    # A second wait starts after the first wait's timeout is half spent.
    await asyncio.sleep(0.1)
    second = asyncio.create_task(manager.wait_for_webrtc())
    await _wait_until_waiting(manager)

    # The first wait's timeout expires here. The second wait must still be open.
    await asyncio.sleep(0.15)
    assert manager.is_waiting() is True

    assert manager.complete_session() is True
    await second
