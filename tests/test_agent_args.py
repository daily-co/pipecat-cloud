"""The agent *SessionArguments types are thin subclasses of pipecat-ai's runner
argument types.

They are Pipecat Cloud's stable session-argument API (not deprecated): each
subclasses the matching ``pipecat.runner.types.*RunnerArguments``, so it carries
``session_id`` and interoperates with pipecat's runner machinery.
"""

import dataclasses
import importlib.util
import inspect
import os
import sys
import types
import warnings
from dataclasses import dataclass
from importlib.metadata import version

import pytest
from packaging.version import Version
from pipecat.runner import types as runner_types
from pipecat.runner.types import (
    DailyRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)

import pipecatcloud
from pipecatcloud import agent
from pipecatcloud.agent import (
    DailySessionArguments,
    PipecatSessionArguments,
    SessionArguments,
    SmallWebRTCSessionArguments,
    WebSocketSessionArguments,
)

# MOQSessionArguments needs pipecat-ai 1.12.0, the release that gave
# MOQRunnerArguments its relay_url. The expectation comes from the release
# number, not from the field check agent.py makes, so these tests hold the
# module to the documented requirement on whichever pipecat-ai they run with.
# The base version, so a development or pre-release build of 1.12.0 counts as 1.12.0.
MOQ_SUPPORTED = Version(Version(version("pipecat-ai")).base_version) >= Version("1.12.0")
needs_moq = pytest.mark.skipif(not MOQ_SUPPORTED, reason="needs pipecat-ai 1.12.0 or newer")
without_moq = pytest.mark.skipif(MOQ_SUPPORTED, reason="needs pipecat-ai older than 1.12.0")

RELAY_URL = "https://internal.example.moq.test:4443/?jwt=relay-token"

# Set by CI's agent-types job to the pipecat-ai it layers over the lock.
EXPECTED_PIPECAT_AI = os.environ.get("PCC_EXPECTED_PIPECAT_AI")


@pytest.mark.skipif(not EXPECTED_PIPECAT_AI, reason="set by CI's agent-types job")
def test_runs_on_the_expected_pipecat_ai():
    """Checked in the test process itself: a separate `python -c` check can pass
    while pytest resolves to another environment."""
    assert version("pipecat-ai") == EXPECTED_PIPECAT_AI


def test_construction_does_not_warn():
    """The types are supported, not deprecated — constructing must not warn."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning becomes an error
        PipecatSessionArguments(session_id="s")
        DailySessionArguments(room_url="https://x.daily.co/r", token="t", session_id="s")
        WebSocketSessionArguments(websocket=None, session_id="s")
        SmallWebRTCSessionArguments(webrtc_connection=None, session_id="s")


def test_session_arguments_construct_and_expose_fields():
    d = DailySessionArguments(room_url="https://x.daily.co/r", token="t", session_id="s1")
    assert d.session_id == "s1"
    assert d.room_url == "https://x.daily.co/r"
    # The inherited RunnerArguments __post_init__ runs (sets handle_sigint default).
    assert d.handle_sigint is False


def test_session_id_is_optional_via_runnerarguments_override():
    """Guard for the MRO override: when pipecat-ai provides session_id on the base
    (v1.2.0+), it overrides the SessionArguments mixin's required field, making
    session_id optional. If someone reorders the bases, this breaks loudly here."""
    d = DailySessionArguments(room_url="https://x.daily.co/r")
    assert d.session_id is None


def test_subclasses_are_runner_argument_types():
    """Subclass relationship → interop with create_transport / isinstance checks."""
    daily = DailySessionArguments(room_url="https://x.daily.co/r", session_id="s")
    assert isinstance(daily, DailyRunnerArguments)
    assert isinstance(daily, RunnerArguments)
    assert isinstance(daily, SessionArguments)

    assert isinstance(PipecatSessionArguments(session_id="s"), RunnerArguments)
    assert isinstance(WebSocketSessionArguments(websocket=None), WebSocketRunnerArguments)
    assert isinstance(
        SmallWebRTCSessionArguments(webrtc_connection=None), SmallWebRTCRunnerArguments
    )


def _moq_session_arguments(**overrides):
    """The arguments as pipecat-base builds them for a MoQ session."""
    values = {
        "session_id": "s1",
        "relay_url": RELAY_URL,
        "namespace": "pcc/s1",
        "participant_id": "response",
        "peer_id": "request",
        "body": {"k": 1},
    }
    values.update(overrides)
    return agent.MOQSessionArguments(**values)


@needs_moq
def test_moq_session_arguments_construct_and_expose_fields():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        args = _moq_session_arguments()
    assert args.session_id == "s1"
    assert args.relay_url == RELAY_URL
    assert args.namespace == "pcc/s1"
    assert args.participant_id == "response"
    assert args.peer_id == "request"
    assert args.body == {"k": 1}
    assert args.serve is False
    assert args.verify_ssl is True


@needs_moq
def test_moq_session_arguments_are_moq_runner_arguments():
    """create_transport dispatches on pipecat's MOQRunnerArguments."""
    args = _moq_session_arguments()
    assert isinstance(args, runner_types.MOQRunnerArguments)
    assert isinstance(args, RunnerArguments)
    assert isinstance(args, SessionArguments)


@needs_moq
def test_relay_url_token_stays_out_of_repr():
    """The relay URL carries the session's token; printing the arguments must not."""
    args = _moq_session_arguments()
    assert "relay-token" not in repr(args)
    assert "relay_url" not in repr(args)
    assert args.relay_url == RELAY_URL


def _field_spec(f: dataclasses.Field) -> tuple:
    """Every attribute of a dataclass field except its name, type and repr."""
    return (
        f.default,
        f.default_factory,
        f.init,
        f.hash,
        f.compare,
        dict(f.metadata),
        f.kw_only,
        getattr(f, "doc", None),  # Python 3.14+
    )


@needs_moq
def test_relay_url_redeclaration_changes_only_repr():
    """Against the same class without the redeclared field: same fields, same
    order, same signature; relay_url differs in repr alone.

    A redeclared field replaces the base's whole Field, so every attribute that
    field() can set is compared, not only those that show in the signature."""

    @dataclass
    class Undeclared(runner_types.MOQRunnerArguments, SessionArguments):
        pass

    ours = dataclasses.fields(agent.MOQSessionArguments)
    plain = dataclasses.fields(Undeclared)
    assert [f.name for f in ours] == [f.name for f in plain]
    assert inspect.signature(agent.MOQSessionArguments) == inspect.signature(Undeclared)
    relay, plain_relay = (next(f for f in fs if f.name == "relay_url") for fs in (ours, plain))
    assert _field_spec(relay) == _field_spec(plain_relay)
    assert (relay.repr, plain_relay.repr) == (False, True)


@needs_moq
def test_moq_session_id_is_optional_via_runnerarguments_override():
    """The same MRO guard as for the other types: pipecat's base comes first."""
    assert agent.MOQSessionArguments(relay_url=RELAY_URL).session_id is None


@needs_moq
def test_moq_session_arguments_are_exported_from_the_package():
    from pipecatcloud import MOQSessionArguments

    assert MOQSessionArguments is agent.MOQSessionArguments


def test_moq_session_arguments_stay_out_of_all():
    """Listing it would make `from pipecatcloud import *` raise on pipecat-ai < 1.12."""
    assert "MOQSessionArguments" not in pipecatcloud.__all__
    assert "MOQSessionArguments" in dir(pipecatcloud)


@needs_moq
@pytest.mark.asyncio
async def test_create_transport_dials_the_relay_url(monkeypatch):
    """The session's relay URL, namespace and ids reach the MoQ transport.

    The transport module needs the moq extra (moq-rs), which this repo does not
    install, so it is replaced by one that records what create_transport builds.
    """
    from pipecat.runner.utils import create_transport

    built = {}

    class FakeMOQParams:
        pass

    class FakeMOQTransport:
        def __init__(self, params, **kwargs):
            built["params"] = params
            built["kwargs"] = kwargs

    # setattr: type checkers reject new attributes on a plain ModuleType.
    fake_module = types.ModuleType("pipecat.transports.moq.transport")
    setattr(fake_module, "MOQParams", FakeMOQParams)
    setattr(fake_module, "MOQTransport", FakeMOQTransport)
    monkeypatch.setitem(sys.modules, "pipecat.transports.moq.transport", fake_module)

    transport = await create_transport(_moq_session_arguments(), {"moq": FakeMOQParams})

    assert isinstance(transport, FakeMOQTransport)
    params = built["params"]
    assert params.relay_url == RELAY_URL
    assert params.namespace == "pcc/s1"
    assert params.participant_id == "response"
    assert params.peer_id == "request"
    assert params.verify_ssl is True
    assert params.serve is False
    # With a relay URL, host and port are left to the transport's defaults.
    assert built["kwargs"] == {"path": "/moq"}


@without_moq
def test_moq_session_arguments_refused_on_older_pipecat():
    """On the pipecat-ai this suite runs with, if it is older than 1.12.0."""
    with pytest.raises(ImportError, match=r"pipecat-ai\[moq\]>=1\.12\.0"):
        from pipecatcloud.agent import MOQSessionArguments


@without_moq
def test_package_import_refused_on_older_pipecat():
    with pytest.raises(ImportError, match=r"pipecat-ai\[moq\]>=1\.12\.0"):
        from pipecatcloud import MOQSessionArguments


@without_moq
def test_star_import_still_works_on_older_pipecat():
    namespace: dict = {}
    exec("from pipecatcloud import *", namespace)
    assert "DailySessionArguments" in namespace
    assert "MOQSessionArguments" not in namespace


def _load_agent_copy(monkeypatch, moq_runner_arguments):
    """Import a fresh copy of pipecatcloud.agent against a stand-in pipecat-ai.

    ``moq_runner_arguments`` replaces pipecat's MOQRunnerArguments, or removes it
    when None. The copy is a separate module, so the real one is left alone.
    """
    if moq_runner_arguments is None:
        monkeypatch.delattr(runner_types, "MOQRunnerArguments", raising=False)
    else:
        # raising=False: pipecat-ai older than 1.6.0 has no attribute to replace.
        monkeypatch.setattr(runner_types, "MOQRunnerArguments", moq_runner_arguments, raising=False)
    spec = importlib.util.spec_from_file_location("_pipecatcloud_agent_copy", agent.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Registered so that `from _pipecatcloud_agent_copy import ...` finds it.
    monkeypatch.setitem(sys.modules, spec.name, module)
    return module


@dataclass
class _MOQRunnerArgumentsWithoutRelayURL(RunnerArguments):
    """MOQRunnerArguments as pipecat-ai 1.6 to 1.11 shipped it: no relay_url."""

    host: str | None = None
    port: int | None = None
    namespace: str = "pipecat"


class _MOQRunnerArgumentsNotADataclass:
    """A MOQRunnerArguments that is not a dataclass: dataclasses.fields() raises on it."""

    relay_url: str | None = None


@pytest.mark.parametrize(
    "moq_runner_arguments",
    [None, _MOQRunnerArgumentsWithoutRelayURL, _MOQRunnerArgumentsNotADataclass],
    ids=[
        "pipecat-ai before 1.6 (no MOQRunnerArguments)",
        "pipecat-ai 1.6 to 1.11 (no relay_url)",
        "MOQRunnerArguments not a dataclass",
    ],
)
def test_older_pipecat_keeps_the_module_importable(monkeypatch, moq_runner_arguments):
    module = _load_agent_copy(monkeypatch, moq_runner_arguments)

    # Every other session type is still there.
    module.DailySessionArguments(room_url="https://x.daily.co/r", session_id="s")
    module.PipecatSessionArguments(session_id="s")

    # Reaching for MOQSessionArguments says what to install, and which
    # pipecat-ai the environment has, by attribute and by `from ... import`.
    with pytest.raises(ImportError) as refused:
        module.MOQSessionArguments
    message = str(refused.value)
    assert "pipecat-ai[moq]>=1.12.0" in message
    assert f"pipecat-ai {version('pipecat-ai')}" in message
    with pytest.raises(ImportError, match=r"pipecat-ai\[moq\]>=1\.12\.0"):
        exec("from _pipecatcloud_agent_copy import MOQSessionArguments", {})


def test_unknown_module_attribute_is_still_an_attribute_error():
    with pytest.raises(AttributeError):
        getattr(agent, "NoSuchSessionArguments")
