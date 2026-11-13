#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Secret values and tokens never reach a log, at any level. The real commands and
client run with a debug sink attached, and only the HTTP session is replaced, so each
test also checks the values really were sent: nothing passes by not being there.
"""

import ast
import os
import subprocess
import sys
import textwrap
from importlib.metadata import version
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from loguru import logger
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.build_utils import upload_to_s3
from pipecatcloud._utils.log_utils import (
    describe_api_error,
    describe_body,
    describe_exception,
    describe_url,
)
from pipecatcloud.api import _API, CAPABILITIES_HEADER, SECRETS_BULK_WRITE_CAPABILITY
from pipecatcloud.cli.api import API as CLI_API
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

SRC = Path(__file__).parent.parent / "src" / "pipecatcloud"
VALUES = {"KEY_A": "sup3r-s3cret-one", "KEY_B": "sup3r-s3cret-two", "KEY_C": "sup3r-s3cret-three"}
TOKEN = "pcc_pat_t0ken-for-the-log-test"
BULK = {CAPABILITIES_HEADER: SECRETS_BULK_WRITE_CAPABILITY}


class FakeSession:
    """Stands in for `aiohttp.ClientSession`, recording what was sent. Each answer
    is (status, body, headers); the last is repeated."""

    def __init__(self, answers: list):
        self.answers = answers
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def request(self, **kwargs):
        self.sent.append(kwargs)
        status, body, headers = self.answers[min(len(self.sent) - 1, len(self.answers) - 1)]
        if isinstance(body, Exception):
            raise body
        response = MagicMock(ok=status < 400, status=status, reason="Reason")
        response.headers = headers
        response.json = AsyncMock(return_value=body)
        response.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(MagicMock(), (), status=status)
        )
        return response


@pytest.fixture
def debug_log():
    lines: list[str] = []
    handler = logger.add(lambda m: lines.append(str(m)), level="DEBUG", format="{message}")
    yield lines
    logger.remove(handler)


def _assert_clean(text: str):
    for value in VALUES.values():
        assert value not in text
    assert TOKEN not in text
    assert "test-token" not in text
    assert "Bearer" not in text


def _set(session: FakeSession, *extra):
    pairs = [f"{k}={v}" for k, v in VALUES.items()]
    with (
        patch.object(aiohttp, "ClientSession", return_value=session),
        patch.object(CLI_API, "token", TOKEN),
    ):
        return runner.invoke(
            entrypoint_cli_typer,
            [
                "--output",
                "plain",
                "secrets",
                "set",
                "my-set",
                *pairs,
                "--skip",
                "-o",
                "acme",
                *extra,
            ],
        )


def _sent_with_values(session: FakeSession) -> list[dict]:
    return [s for s in session.sent if s["method"] == "PUT"]


NOT_FOUND = (404, {}, {})


@pytest.mark.parametrize(
    "headers,puts",
    [(BULK, 1), ({}, 3)],
    ids=["bulk", "one-key-at-a-time"],
)
class TestSecretsSetAtDebug:
    def test_a_write_logs_no_value_and_no_token(self, debug_log, headers, puts):
        session = FakeSession([(404, {}, headers), (200, {"region": "r1"}, {})])

        result = _set(session)

        assert result.exit_code == 0
        # They were sent, with the token, and the log saw the requests.
        sent = _sent_with_values(session)
        assert len(sent) == puts
        assert all(v in str([s["json"] for s in sent]) for v in VALUES.values())
        assert all(s["headers"]["Authorization"] == f"Bearer {TOKEN}" for s in sent)
        log = "".join(debug_log)
        assert log.count("Request: PUT") == puts
        assert "3 keys to write" in log
        _assert_clean(log)
        _assert_clean(result.output)

    def test_a_refused_write_logs_nothing_the_server_echoed(self, debug_log, headers, puts):
        refusal = {"error": f"Refused {VALUES['KEY_A']} with {TOKEN}", "code": "400"}
        session = FakeSession([(404, {}, headers), (400, refusal, headers)])

        result = _set(session)

        assert result.exit_code == 1
        assert len(_sent_with_values(session)) == 1
        assert "Response not ok: 400" in "".join(debug_log)
        _assert_clean("".join(debug_log))

    def test_a_failure_whose_text_quotes_a_value_is_logged_by_type(self, debug_log, headers, puts):
        failure = aiohttp.ClientPayloadError(f"could not send {VALUES['KEY_A']} with {TOKEN}")
        session = FakeSession([(404, {}, headers), (0, failure, {})])

        result = _set(session)

        assert result.exit_code == 1
        assert len(_sent_with_values(session)) == 1
        assert "got no answer" in result.output
        _assert_clean("".join(debug_log))
        _assert_clean(result.output)


class TestEveryOtherPath:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"json": {"data": {"apiKey": VALUES["KEY_A"]}}},
            {"json": [VALUES["KEY_A"]]},
            {"params": {"q": VALUES["KEY_A"]}},
            {"override_token": TOKEN},
        ],
        ids=["body", "list-body", "params", "token"],
    )
    @pytest.mark.asyncio
    async def test_the_client_logs_no_body_params_or_header_on_any_route(self, debug_log, kwargs):
        session = FakeSession([(200, {"ok": True}, {})])
        api = _API(TOKEN)

        with patch.object(aiohttp, "ClientSession", return_value=session):
            await api._base_request("POST", "https://api.test/v1/anything", **kwargs)

        assert "Request: POST https://api.test/v1/anything" in "".join(debug_log)
        _assert_clean("".join(debug_log))

    @pytest.mark.asyncio
    async def test_a_query_string_is_not_logged(self, debug_log):
        session = FakeSession([(200, {"ok": True}, {})])

        with patch.object(aiohttp, "ClientSession", return_value=session):
            await _API(TOKEN)._base_request("GET", f"https://api.test/x?t={VALUES['KEY_A']}")

        assert "https://api.test/x " in "".join(debug_log)
        _assert_clean("".join(debug_log))

    def test_an_agents_status_body_is_not_logged(self, debug_log):
        body = {"name": "bot", "ready": True, "manifest": {"spec": {"env": VALUES["KEY_A"]}}}
        session = FakeSession([(200, {"body": body}, {})])

        with (
            patch.object(aiohttp, "ClientSession", return_value=session),
            patch.object(CLI_API, "token", TOKEN),
        ):
            runner.invoke(
                entrypoint_cli_typer, ["--output", "plain", "agent", "status", "bot", "-o", "acme"]
            )

        assert "Agent status: dict of 3" in "".join(debug_log)
        _assert_clean("".join(debug_log))

    @pytest.mark.asyncio
    async def test_a_failed_upload_logs_its_status_and_code_only(self, debug_log):
        document = (
            "<Error><Code>SignatureDoesNotMatch</Code>"
            f"<AWSAccessKeyId>{TOKEN}</AWSAccessKeyId>"
            f"<StringToSign>{VALUES['KEY_A']}</StringToSign></Error>"
        )
        response = MagicMock(status=403)
        response.text = AsyncMock(return_value=document)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=context)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=session):
            assert await upload_to_s3(b"x", "https://bucket.test/", {"policy": "p"}) is False

        assert "Upload failed: 403 (SignatureDoesNotMatch)" in "".join(debug_log)
        _assert_clean("".join(debug_log))

    @pytest.mark.asyncio
    async def test_an_upload_that_raises_logs_its_type_only(self, debug_log):
        session = MagicMock()
        session.post = MagicMock(side_effect=aiohttp.ClientError(f"sent {VALUES['KEY_A']}"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=session):
            assert await upload_to_s3(b"x", "https://bucket.test/", {"policy": "p"}) is False

        assert "Upload error: ClientError" in "".join(debug_log)
        _assert_clean("".join(debug_log))


def test_an_uncaught_error_inside_a_host_cli_shows_no_local_variables(tmp_path, monkeypatch):
    """A real process, as the except hook needs: the CLI runs as an extension of an
    outer Typer app explicitly configured to print locals in a traceback."""
    script = tmp_path / "host.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            import sys
            from unittest.mock import patch
            import typer
            from pipecatcloud.cli.entry_point import entrypoint_cli_typer

            assert "PCC_TEST_PARENT_AUTH" not in os.environ
            assert typer.__version__ == sys.argv[2]
            host = typer.Typer(pretty_exceptions_show_locals=True)
            host.add_typer(entrypoint_cli_typer, name="cloud")
            with patch(
                "pipecatcloud.cli.commands.secrets.validate_secrets",
                side_effect=RuntimeError("something unexpected"),
            ):
                host(["cloud", "--output", "plain", "secrets", "set", "my-set",
                      sys.argv[1], "--skip", "-o", "acme"])
            """
        )
    )
    # The positive control deliberately prints locals. Forward runtime paths
    # and the conftest's isolated config, never the parent process's auth env.
    runtime_env = (
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "SYSTEMROOT",
        "SystemRoot",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    )
    monkeypatch.setenv("PCC_TEST_PARENT_AUTH", "parent-auth-canary")
    inherited = {key: os.environ[key] for key in runtime_env if key in os.environ}
    # Fail before starting a child if the test config is missing, rather than
    # falling back to a credentials file under the unchanged HOME.
    inherited["PIPECAT_CONFIG_PATH"] = os.environ["PIPECAT_CONFIG_PATH"]

    def run(env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), f"KEY_A={VALUES['KEY_A']}", version("typer")],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    result = run(inherited)
    assert result.returncode != 0
    assert "something unexpected" in result.stderr
    assert VALUES["KEY_A"] not in result.stderr
    assert VALUES["KEY_A"] not in result.stdout

    # What this guards against: the traceback a host's Typer app prints otherwise.
    assert VALUES["KEY_A"] in run({**inherited, "TYPER_STANDARD_TRACEBACK": ""}).stderr


# Everything a log call may read, as a variable, an attribute or a key: a name, a count, a
# status, or a code. A list of what is unsafe always misses the next name for a value, so
# anything else fails here. Log it through `log_utils`, or add it once it is established
# that it can hold no value, credential, body or exception text.
SAFE_TO_LOG = {
    "MAX_CONSECUTIVE_POLL_FAILURES",
    "agent_name",
    "code",
    "consecutive_failures",
    "context_hash",
    "deploy_config_path",
    "exists",
    "is_available",
    "method",
    "name",
    "phase",
    "reason",
    "region",
    "set_name",
    "status",
}
# What takes an exception, a traceback or context along into every line after it.
UNSAFE_LOGGER_METHODS = {"bind", "catch", "contextualize", "exception", "opt", "patch"}
# What sets up the sinks, and writes no line.
LOGGER_SETUP_METHODS = {"add", "remove"}
# An OAuth error code from the callback's own query: a code, never a credential.
KNOWN_SAFE = {("auth.py", "error")}


def _is_log_utils_call(node: ast.AST) -> bool:
    """A call whose answer is safe whatever it is given: it says what, or how many."""
    if not isinstance(node, ast.Call):
        return False
    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
    return name == "len" or name.startswith("describe_")


def _is_plain_chain(node: ast.AST) -> bool:
    """`response` or `self.response`: read only to reach an attribute of it."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _names_within(node: ast.AST):
    """Every variable, attribute and key read under `node`, skipping what `log_utils`
    handles. Of `response.status`, that is `status`: what is logged, not what holds it."""
    if _is_log_utils_call(node):
        return
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Attribute):
        yield node.attr
        if _is_plain_chain(node):
            return
    elif (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        yield node.slice.value
    for child in ast.iter_child_nodes(node):
        yield from _names_within(child)


def unsafe_log_calls(source: str, filename: str = "") -> list[str]:
    """What logs something that can hold a value, a credential or an exception's text."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            continue
        if node.func.attr in LOGGER_SETUP_METHODS:
            continue
        if node.func.attr in UNSAFE_LOGGER_METHODS:
            found.append(f"{filename}:{node.lineno}: logger.{node.func.attr}")
        for arg in [*node.args, *[keyword.value for keyword in node.keywords]]:
            for name in _names_within(arg):
                if name not in SAFE_TO_LOG and (filename, name) not in KNOWN_SAFE:
                    found.append(f"{filename}:{node.lineno}: {name}")
    return found


def test_no_log_call_formats_a_body_a_credential_or_an_exception():
    found = []
    for path in SRC.rglob("*.py"):
        found += unsafe_log_calls(path.read_text(), path.name)
    assert found == []


@pytest.mark.parametrize(
    "line",
    [
        'logger.error(f"failed: {e}")',
        'logger.error(f"failed: {str(e)}")',
        'logger.error(f"failed: {repr(err)}")',
        'logger.error(f"failed: {self.error}")',
        'logger.debug(f"response: {resp.text}")',
        'logger.debug(f"response: {await response.text()}")',
        'logger.debug(f"response: {response.content!r}")',
        'logger.debug(f"status: {response.json()}")',
        'logger.error("failed: %s" % e)',
        'logger.error("failed: {}".format(e))',
        'logger.error("failed: " + str(e))',
        'logger.error("failed: {}", e)',
        'logger.error("failed", detail=e)',
        "logger.debug(secrets_dict)",
        'logger.debug(f"{list(secrets_dict.values())}")',
        "logger.debug(config['token'])",
        'logger.debug(f"{exc.message}")',
        "logger.debug(traceback.format_exc())",
        'logger.exception("failed")',
        'logger.opt(exception=e).error("failed")',
        'logger.opt(exception=True).error("failed")',
        "logger.catch(handler)",
        'logger.debug(f"{fields}")',
        'logger.debug(f"{secret_value}")',
        'logger.debug(f"{api_key}")',
        'logger.debug(f"{response}")',
        'logger.debug(f"{self.response}")',
        'logger.debug(f"{kwargs}")',
        'logger.debug(f"{a_name_nobody_has_thought_of}")',
        'logger.debug(f"{fetch_it()}")',
        'logger.debug(f"{config[key]}")',
        'logger.debug(f"{fetch_it().status}")',
        'logger.bind(x=secret_value).info("wrote")',
        'logger.bind(x=1).info("wrote")',
        'logger.patch(add_detail).info("wrote")',
        "logger.contextualize(x=secret_value)",
    ],
)
def test_the_guard_finds_what_it_is_for(line):
    assert unsafe_log_calls(f"async def f():\n    {line}\n"), line


@pytest.mark.parametrize(
    "line",
    [
        'logger.debug("plain text")',
        'logger.error(f"failed: {describe_exception(e)}")',
        'logger.debug(f"body: {describe_body(payload)}")',
        'logger.debug(f"to {describe_url(url)}")',
        'logger.debug(f"{len(secrets_dict)} keys")',
        'logger.debug(f"status {response.status} {response.reason}")',
        'logger.debug(f"set {set_name} in {region}")',
        'logger.debug(f"{self.agent_name}")',
        "logger.add(sys.stderr, level=os.getenv(name))",
        "logger.remove()",
    ],
)
def test_the_guard_lets_through_what_says_how_many_or_which(line):
    assert unsafe_log_calls(f"def f():\n    {line}\n") == []


class TestDescriptions:
    def test_a_body_is_counted_and_never_quoted(self):
        assert describe_body(None) == "none"
        assert describe_body({"a": "v", "b": "w"}) == "dict of 2"
        assert describe_body(["v"]) == "list of 1"
        assert describe_body("v") == "str"

    def test_a_url_loses_its_query(self):
        assert describe_url("https://x/y?t=v") == "https://x/y"
        assert describe_url("https://x/y") == "https://x/y"

    def test_an_exception_is_its_type_and_status(self):
        assert describe_exception(ValueError("v")) == "ValueError"
        failure = aiohttp.ClientResponseError(MagicMock(), (), status=503, message="v")
        assert describe_exception(failure) == "ClientResponseError (status 503)"

    def test_an_api_error_is_its_code(self):
        assert describe_api_error({"code": "503", "error": "v"}) == "code 503"
        assert describe_api_error(None) == "no code"
