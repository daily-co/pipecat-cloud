"""Tests for `pipecat cloud eval` commands and their API methods."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.api import _API, EVALS_NOT_AVAILABLE_MESSAGE
from pipecatcloud.cli.commands import evals
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

RUN_ID = "11111111-2222-4333-8444-555555555555"
SESSION_ID = "7538a263-3966-48d8-807c-904c33f9afe5"
SECOND_SESSION_ID = "86c1f86e-96b7-4fce-8a65-21303b592be6"

SCENARIO = """\
name: calls
scenarios:
  - name: book
    persona: A caller.
    goal: Book a table.
    success: A table was booked.
"""

TWO_SCENARIOS = (
    SCENARIO
    + """\
  - name: hours
    persona: Another caller.
    goal: Ask the hours.
    success: Hours were given.
"""
)

CRITERIA = """\
name: session_review
scenarios:
  - name: call
    success: The bot answered the caller's question correctly.
    metrics:
      - name: concise
        criterion: The bot asked one question at a time.
        min_score: 0.8
"""


def make_run(status="queued", verdict=None, result=None, **extra):
    run = {
        "id": RUN_ID,
        "kind": "synthetic",
        "status": status,
        "verdict": verdict,
        "scenarioPath": "calls.yaml",
        "scenarioName": "book",
        "params": {},
        "sessionId": None,
        "createdAt": "2026-11-07T10:00:00.000Z",
        "startedAt": None,
        "finishedAt": None,
        "deadlineAt": "2026-11-07T10:30:00.000Z",
        "result": result,
    }
    run.update(extra)
    return run


FAILED_RESULT = {
    "reason": "The caller was never told the closing time.",
    "metrics": [
        {"name": "polite", "score": 0.9, "passed": True, "reason": None},
        {"name": "accurate", "score": 0.2, "passed": False, "reason": "Wrong hours given."},
    ],
    "details": {
        "kind": "simulation",
        "turns": [],
        "failures": [
            {"kind": "judge_no", "reason": "Hours were wrong", "turn": 3, "name": "hours"},
        ],
        "goal": None,
    },
}


@pytest.fixture
def scenario_file(tmp_path):
    path = tmp_path / "calls.yaml"
    path.write_text(SCENARIO, encoding="utf-8")
    return path


@pytest.fixture
def criteria_file(tmp_path):
    path = tmp_path / "criteria.yaml"
    path.write_text(CRITERIA, encoding="utf-8")
    return path


@pytest.fixture
def api():
    """A stand-in for the CLI's API client; each test sets the answers it needs."""
    with patch("pipecatcloud.cli.commands.evals.API") as mock_api:
        mock_api.bubble_error.return_value = mock_api
        mock_api.error_status = None
        mock_api.error_retry_after = None
        mock_api.eval_run_create = AsyncMock(return_value=(make_run(), None))
        mock_api.eval_run_get = AsyncMock(return_value=(make_run(), None))
        mock_api.eval_runs_list = AsyncMock(return_value=({"total_count": 0, "runs": []}, None))
        mock_api.eval_run_cancel = AsyncMock(return_value=(make_run(status="cancelled"), None))
        yield mock_api


@pytest.fixture(autouse=True)
def clock():
    """The wait is timed on the monotonic clock, which a test moves by hand."""
    state = {"now": 0.0}
    with patch("pipecatcloud.cli.commands.evals._monotonic", side_effect=lambda: state["now"]):
        yield state


@pytest.fixture
def sleeps():
    with patch("pipecatcloud.cli.commands.evals.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        yield mock_sleep


def invoke(*args):
    """Run `pipecat cloud eval`; submission tests opt out of the default wait.

    TestWaitIsTheDefault covers the actual command defaults directly.
    """
    argv = [*map(str, args)]
    if argv[:1] in (["run"], ["judge"]) and "--wait" not in argv and "--no-wait" not in argv:
        argv.append("--no-wait")
    return runner.invoke(entrypoint_cli_typer, ["eval", *argv])


# ---- run ----


class TestRun:
    def test_without_wait_it_creates_the_run_and_exits_zero(self, api, scenario_file):
        result = invoke("run", "my-agent", scenario_file)
        assert result.exit_code == 0, result.output
        api.eval_run_create.assert_awaited_once()
        org, agent, body = api.eval_run_create.await_args.args
        assert (org, agent) == ("test-org", "my-agent")
        assert body == {
            "kind": "synthetic",
            "files": {"calls.yaml": SCENARIO},
            "scenarioPath": "calls.yaml",
            "scenarioName": "book",
        }
        assert RUN_ID in result.stdout
        api.eval_run_get.assert_not_awaited()

    def test_organization_option_is_used(self, api, scenario_file):
        invoke("run", "my-agent", scenario_file, "-o", "other-org")
        assert api.eval_run_create.await_args.args[0] == "other-org"

    def test_scenario_option_picks_one_of_several(self, api, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")
        result = invoke("run", "a", path, "--scenario", "hours")
        assert result.exit_code == 0, result.output
        assert api.eval_run_create.await_args.args[2]["scenarioName"] == "hours"

    def test_without_a_choice_every_scenario_in_the_file_runs(self, api, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")
        result = invoke("run", "a", path)
        assert result.exit_code == 0, result.output
        names = [c.args[2]["scenarioName"] for c in api.eval_run_create.await_args_list]
        assert names == ["book", "hours"]

    def test_scenario_can_be_repeated_to_pick_several(self, api, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")
        result = invoke("run", "a", path, "--scenario", "hours", "--scenario", "book")
        assert result.exit_code == 0, result.output
        names = [c.args[2]["scenarioName"] for c in api.eval_run_create.await_args_list]
        assert names == ["hours", "book"]

    def test_unknown_scenario_name_is_refused_before_any_request(self, api, scenario_file):
        result = invoke("run", "a", scenario_file, "--scenario", "nope")
        assert result.exit_code == 3
        assert "nope" in result.output and "book" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_params_and_deadline_are_sent_as_numbers(self, api, scenario_file):
        result = invoke(
            "run",
            "a",
            scenario_file,
            "--param",
            "connectTimeoutSeconds=2.5",
            "--param",
            "defaultTimeoutMs=30000",
            "--deadline",
            "600",
        )
        assert result.exit_code == 0, result.output
        body = api.eval_run_create.await_args.args[2]
        assert body["params"] == {"connectTimeoutSeconds": 2.5, "defaultTimeoutMs": 30000}
        assert isinstance(body["params"]["defaultTimeoutMs"], int)
        assert body["deadlineSeconds"] == 600

    @pytest.mark.parametrize(
        "param",
        ["nope=1", "connectTimeoutSeconds", "connectTimeoutSeconds=", "connectTimeoutSeconds=abc"],
    )
    def test_bad_params_are_refused_before_any_request(self, api, scenario_file, param):
        result = invoke("run", "a", scenario_file, "--param", param)
        assert result.exit_code == 3
        api.eval_run_create.assert_not_awaited()

    def test_root_keys_the_files_relative_to_it(self, api, tmp_path):
        (tmp_path / "scenarios").mkdir()
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / "judge.yaml").write_text("t: 1\n", encoding="utf-8")
        scenario = tmp_path / "scenarios" / "calls.yaml"
        scenario.write_text(
            SCENARIO.replace(
                "name: calls\n", "name: calls\njudge: !include ../shared/judge.yaml\n"
            ),
            encoding="utf-8",
        )
        result = invoke("run", "a", scenario, "--root", tmp_path)
        assert result.exit_code == 0, result.output
        body = api.eval_run_create.await_args.args[2]
        assert sorted(body["files"]) == ["scenarios/calls.yaml", "shared/judge.yaml"]
        assert body["scenarioPath"] == "scenarios/calls.yaml"

    def test_a_bundle_that_cant_be_sent_is_refused_before_any_request(self, api, tmp_path):
        scenario = tmp_path / "calls.yaml"
        scenario.write_text(SCENARIO + "x: !include .env\n", encoding="utf-8")
        (tmp_path / ".env").write_text("T=1\n", encoding="utf-8")
        result = invoke("run", "a", scenario)
        assert result.exit_code == 3
        assert ".env" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_a_non_utf8_include_is_refused_before_any_request(self, api, tmp_path):
        scenario = tmp_path / "calls.yaml"
        scenario.write_text("name: c\nj: !include j.yaml\nscenarios: []\n", encoding="utf-8")
        (tmp_path / "j.yaml").write_bytes(b"\xff\xfe")
        result = invoke("run", "a", scenario)
        assert result.exit_code == 3
        assert "UTF-8" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_json_without_wait_is_the_created_run(self, api, scenario_file):
        result = invoke("run", "a", scenario_file, "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == make_run()

    def test_api_error_exits_three(self, api, scenario_file):
        api.eval_run_create.return_value = (None, {"error": "Agent not found", "code": "404"})
        result = invoke("run", "a", scenario_file)
        assert result.exit_code == 3

    def test_api_error_in_json_is_the_only_thing_on_stdout(self, api, scenario_file):
        error = {"error": EVALS_NOT_AVAILABLE_MESSAGE, "code": "EVALS-NOT-AVAILABLE"}
        api.eval_run_create.return_value = (None, error)
        result = invoke("run", "a", scenario_file, "--json")
        assert result.exit_code == 3
        assert json.loads(result.stdout) == {"error": error}

    def test_a_refusal_in_json_mode_is_an_error_object(self, api, scenario_file):
        result = invoke("run", "a", scenario_file, "--param", "x=1", "--json")
        assert result.exit_code == 3
        assert json.loads(result.stdout)["error"]["code"] == "CLI-USAGE"

    def test_a_usage_error_exits_three_not_clicks_two(self, api):
        result = invoke("run", "only-an-agent")
        assert result.exit_code == 3
        api.eval_run_create.assert_not_awaited()

    def test_not_logged_in_exits_three_not_one(self, api, scenario_file):
        with patch("pipecatcloud._utils.auth_utils.config") as mock_config:
            mock_config.get.return_value = None
            result = invoke("run", "a", scenario_file)
        assert result.exit_code == 3
        api.eval_run_create.assert_not_awaited()


# ---- waiting ----


ENDED_WITHOUT_VERDICT = [
    "cancelled",
    "refused",
    "target_unavailable",
    "timed_out",
    "error",
    "not_started",
    "spend_limited",
    "expired",
    "abandoned",
    "completed",
]


class TestWait:
    def test_a_passed_run_exits_zero(self, api, sleeps, scenario_file):
        api.eval_run_create.return_value = (make_run(), None)
        ended = make_run(
            "completed", "passed", {"reason": "All good.", "metrics": [], "details": None}
        )
        api.eval_run_get.side_effect = [(make_run("running"), None), (ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output
        assert "✓ a calls/book" in result.stdout
        assert "1/1 passed  ·  1/1 done" in result.stdout
        # A pass needs no explaining; the command for its details does.
        assert "All good." not in result.stdout
        assert f"eval status {RUN_ID} --agent a -v" in result.stdout
        assert api.eval_run_get.await_count == 2

    def test_a_failed_run_exits_one_and_shows_why(self, api, sleeps, scenario_file):
        ended = make_run("completed", "failed", FAILED_RESULT)
        api.eval_run_get.side_effect = [(ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 1, result.output
        out = result.stdout
        assert "✗ a calls/book" in out
        assert "0/1 passed" in out
        assert "The caller was never told the closing time." in out
        assert "judge_no" in out and "Hours were wrong" in out
        assert "accurate" in out and "0.2" in out and "Wrong hours given." in out

    @pytest.mark.parametrize("status", ENDED_WITHOUT_VERDICT)
    def test_an_ended_run_without_a_verdict_exits_two(self, api, sleeps, scenario_file, status):
        ended = make_run(status, None, {"reason": "Ended early.", "metrics": [], "details": None})
        api.eval_run_get.side_effect = [(ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 2, result.output
        assert f"no verdict ({status})" in result.stdout
        assert "Ended early." in result.stdout

    def test_a_skipped_run_exits_zero_and_says_skipped(self, api, sleeps, scenario_file):
        ended = make_run("completed", "skipped", {"reason": "Nothing to judge.", "metrics": []})
        api.eval_run_get.side_effect = [(ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output
        assert "⊘ a calls/book" in result.stdout
        # As in `pipecat eval`, a skip is listed but not explained; `status` explains it.
        assert "Failures" not in result.stdout

    def test_starting_is_a_live_status(self, api, sleeps, scenario_file):
        api.eval_run_get.side_effect = [
            (make_run("starting"), None),
            (make_run("running"), None),
            (make_run("completed", "passed"), None),
        ]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output
        assert api.eval_run_get.await_count == 3

    def test_an_already_ended_creation_response_is_not_polled(self, api, sleeps, scenario_file):
        api.eval_run_create.return_value = (make_run("completed", "passed"), None)
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0
        api.eval_run_get.assert_not_awaited()
        sleeps.assert_not_awaited()

    def test_polling_backs_off_to_a_ceiling(self, api, sleeps, scenario_file):
        live = [(make_run("running"), None)] * 7
        api.eval_run_get.side_effect = [*live, (make_run("completed", "passed"), None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output
        delays = [c.args[0] for c in sleeps.await_args_list]
        assert delays == [2.0, 3.0, 4.5, 6.75, 10.0, 10.0, 10.0, 10.0]

    def test_json_wait_prints_the_final_run_alone_on_stdout(self, api, sleeps, scenario_file):
        ended = make_run("completed", "failed", FAILED_RESULT)
        api.eval_run_get.side_effect = [(make_run("running"), None), (ended, None)]
        result = invoke("run", "a", scenario_file, "--wait", "--json")
        assert result.exit_code == 1
        assert json.loads(result.stdout) == ended

    def test_a_transient_poll_failure_is_retried(self, api, sleeps, scenario_file):
        api.error_status = 503
        ended = make_run("completed", "passed")
        api.eval_run_get.side_effect = [
            (None, {"error": "Unavailable", "code": "503"}),
            (ended, None),
        ]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output
        assert api.eval_run_get.await_count == 2

    def test_a_dropped_connection_is_retried(self, api, sleeps, scenario_file):
        # No response at all: no error body and no status.
        ended = make_run("completed", "passed")
        api.eval_run_get.side_effect = [(None, None), (ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output

    def test_polling_gives_up_after_repeated_failures(self, api, sleeps, scenario_file):
        api.error_status = 503
        api.eval_run_get.return_value = (None, {"error": "Unavailable", "code": "503"})
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 3
        assert api.eval_run_get.await_count == evals.POLL_MAX_CONSECUTIVE_ERRORS

    def test_a_failure_streak_is_reset_by_a_good_poll(self, api, sleeps, scenario_file):
        api.error_status = 503
        bad = (None, {"error": "Unavailable", "code": "503"})
        n = evals.POLL_MAX_CONSECUTIVE_ERRORS - 1
        api.eval_run_get.side_effect = [
            *[bad] * n,
            (make_run("running"), None),
            *[bad] * n,
            (make_run("completed", "passed"), None),
        ]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output

    def test_a_permanent_poll_error_ends_the_wait_at_once(self, api, sleeps, scenario_file):
        api.error_status = 404
        api.eval_run_get.return_value = (None, {"error": "Eval run not found", "code": "404"})
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 3
        assert api.eval_run_get.await_count == 1

    def test_a_429_is_retried(self, api, sleeps, scenario_file):
        api.error_status = 429
        api.eval_run_get.side_effect = [
            (None, {"error": "Slow down", "code": "429"}),
            (make_run("completed", "passed"), None),
        ]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output

    def test_poll_failure_in_json_mode_is_an_error_object(self, api, sleeps, scenario_file):
        api.error_status = 404
        error = {"error": "Eval run not found", "code": "404"}
        api.eval_run_get.return_value = (None, error)
        result = invoke("run", "a", scenario_file, "--wait", "--json")
        assert result.exit_code == 3
        assert json.loads(result.stdout) == {"error": error}


class TestCtrlC:
    def test_ctrl_c_stops_waiting_without_cancelling_and_says_how(self, api, sleeps, scenario_file):
        sleeps.side_effect = KeyboardInterrupt
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 130
        api.eval_run_cancel.assert_not_awaited()
        assert "still going" in result.output
        assert f"eval cancel {RUN_ID} --agent a" in result.output
        assert f"eval status {RUN_ID} --agent a --wait" in result.output

    def test_ctrl_c_midway_reports_the_last_state_seen(self, api, sleeps, scenario_file):
        api.eval_run_get.side_effect = [(make_run("running"), None)]
        sleeps.side_effect = [None, KeyboardInterrupt]
        result = invoke("run", "a", scenario_file, "--wait", "--json")
        assert result.exit_code == 130
        assert json.loads(result.stdout)["status"] == "running"
        api.eval_run_cancel.assert_not_awaited()

    def test_ctrl_c_while_waiting_on_status(self, api, sleeps):
        api.eval_run_get.return_value = (make_run("running"), None)
        sleeps.side_effect = KeyboardInterrupt
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == 130
        api.eval_run_cancel.assert_not_awaited()
        assert f"eval cancel {RUN_ID} --agent a" in result.output


# ---- status ----


class TestStatus:
    def test_shows_the_run_and_exits_zero_whatever_the_verdict(self, api):
        api.eval_run_get.return_value = (make_run("completed", "failed", FAILED_RESULT), None)
        result = invoke("status", RUN_ID, "--agent", "my-agent")
        assert result.exit_code == 0, result.output
        assert api.eval_run_get.await_args.args == ("test-org", "my-agent", RUN_ID)
        assert "✗ my-agent calls/book" in result.stdout
        assert "Hours were wrong" in result.stdout

    def test_a_live_run_says_how_to_wait(self, api):
        api.eval_run_get.return_value = (make_run("running"), None)
        result = invoke("status", RUN_ID, "-a", "my-agent")
        assert result.exit_code == 0
        assert "RUNNING" in result.stdout
        assert f"eval status {RUN_ID} --agent my-agent --wait" in result.stdout

    @pytest.mark.parametrize(
        "status,verdict,code",
        [("completed", "passed", 0), ("completed", "failed", 1), ("cancelled", None, 2)],
    )
    def test_wait_on_an_ended_run_exits_with_its_verdict(self, api, sleeps, status, verdict, code):
        api.eval_run_get.return_value = (make_run(status, verdict), None)
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == code
        sleeps.assert_not_awaited()

    def test_wait_follows_a_live_run_to_its_end(self, api, sleeps):
        api.eval_run_get.side_effect = [
            (make_run("queued"), None),
            (make_run("running"), None),
            (make_run("completed", "failed", FAILED_RESULT), None),
        ]
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == 1
        assert api.eval_run_get.await_count == 3

    def test_json_is_the_run(self, api):
        run = make_run("completed", "passed", {"reason": "ok", "metrics": [], "details": None})
        api.eval_run_get.return_value = (run, None)
        result = invoke("status", RUN_ID, "--agent", "a", "--json")
        assert json.loads(result.stdout) == run

    def test_not_found_exits_three(self, api):
        api.eval_run_get.return_value = (None, {"error": "Eval run not found", "code": "404"})
        result = invoke("status", RUN_ID, "--agent", "a")
        assert result.exit_code == 3

    def test_the_agent_is_required(self, api):
        result = invoke("status", RUN_ID)
        assert result.exit_code == 3
        api.eval_run_get.assert_not_awaited()

    def test_brackets_in_a_reason_are_shown_as_written(self, api):
        result_body = {
            "reason": "Expected [bold]hi[/bold] in reply",
            "metrics": [],
            "details": None,
        }
        api.eval_run_get.return_value = (make_run("completed", "failed", result_body), None)
        result = invoke("status", RUN_ID, "--agent", "a")
        assert "[bold]hi[/bold]" in result.stdout

    def test_a_truncated_result_says_so(self, api):
        body = {"reason": "r", "metrics": [], "details": None, "truncated": True}
        api.eval_run_get.return_value = (make_run("completed", "failed", body), None)
        result = invoke("status", RUN_ID, "--agent", "a")
        assert "too large" in result.stdout


# ---- list ----


class TestList:
    def test_lists_runs_with_their_state(self, api):
        runs = [
            make_run("completed", "passed", id="aaaa"),
            make_run("running", None, id="bbbb", scenarioName="hours"),
        ]
        api.eval_runs_list.return_value = ({"total_count": 2, "runs": runs}, None)
        result = invoke("list", "my-agent")
        assert result.exit_code == 0, result.output
        assert api.eval_runs_list.await_args.args[:2] == ("test-org", "my-agent")
        assert api.eval_runs_list.await_args.kwargs == {"limit": 10}
        for text in ("aaaa", "bbbb", "completed", "passed", "running", "hours"):
            assert text in result.stdout

    def test_limit_is_passed_on_and_more_is_signposted(self, api):
        api.eval_runs_list.return_value = (
            {"total_count": 40, "runs": [make_run(id="aaaa")]},
            None,
        )
        result = invoke("list", "a", "--limit", "5")
        assert api.eval_runs_list.await_args.kwargs == {"limit": 5}
        assert "Showing 1 to 1 of 40" in result.stdout
        assert "--offset 1" in result.stdout

    def test_offset_is_passed_on_and_the_hint_moves_on(self, api):
        runs = [make_run(id="aaaa"), make_run(id="bbbb")]
        api.eval_runs_list.return_value = ({"total_count": 40, "runs": runs}, None)
        result = invoke("list", "a", "--limit", "2", "--offset", "10")
        assert api.eval_runs_list.await_args.kwargs == {"limit": 2, "offset": 10}
        assert "Showing 11 to 12 of 40" in result.stdout
        assert "--offset 12" in result.stdout

    def test_no_hint_on_the_last_page(self, api):
        api.eval_runs_list.return_value = (
            {"total_count": 12, "runs": [make_run(id="aaaa"), make_run(id="bbbb")]},
            None,
        )
        result = invoke("list", "a", "--limit", "2", "--offset", "10")
        assert "Showing" not in result.stdout

    @pytest.mark.parametrize("limit", ["0", "201", "x"])
    def test_a_bad_limit_exits_three(self, api, limit):
        result = invoke("list", "a", "--limit", limit)
        assert result.exit_code == 3
        api.eval_runs_list.assert_not_awaited()

    def test_empty(self, api):
        result = invoke("list", "a")
        assert result.exit_code == 0
        assert "No eval runs" in result.stdout

    def test_json_is_the_apis_answer(self, api):
        payload = {"total_count": 1, "runs": [make_run(id="aaaa")]}
        api.eval_runs_list.return_value = (payload, None)
        result = invoke("list", "a", "--json")
        assert json.loads(result.stdout) == payload

    def test_empty_json_is_still_well_formed(self, api):
        result = invoke("list", "a", "--json")
        assert json.loads(result.stdout) == {"total_count": 0, "runs": []}

    def test_error_exits_three(self, api):
        api.eval_runs_list.return_value = (None, {"error": "Agent not found", "code": "404"})
        assert invoke("list", "a").exit_code == 3


# ---- cancel ----


class TestCancel:
    def test_a_queued_run_is_cancelled_at_once(self, api):
        result = invoke("cancel", RUN_ID, "--agent", "my-agent")
        assert result.exit_code == 0, result.output
        assert api.eval_run_cancel.await_args.args == ("test-org", "my-agent", RUN_ID)
        assert "is cancelled" in result.stdout

    def test_a_running_run_is_asked_to_stop(self, api):
        api.eval_run_cancel.return_value = (make_run("running"), None)
        result = invoke("cancel", RUN_ID, "--agent", "a")
        assert result.exit_code == 0
        assert "Cancellation requested" in result.stdout
        assert "is cancelled" not in result.stdout

    def test_a_finished_run_cant_be_cancelled(self, api):
        api.eval_run_cancel.return_value = (
            None,
            {"error": "The run has already finished", "code": "409"},
        )
        assert invoke("cancel", RUN_ID, "--agent", "a").exit_code == 3

    def test_json_is_the_run(self, api):
        result = invoke("cancel", RUN_ID, "--agent", "a", "--json")
        assert json.loads(result.stdout) == make_run("cancelled")


# ---- the API client ----


def _http_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(MagicMock(), (), status=status)


def _session_answering(status: int, body: dict):
    response = MagicMock(ok=status < 400, status=status, reason="x")
    response.headers = {}
    response.json = AsyncMock(return_value=body)
    response.raise_for_status = MagicMock(side_effect=_http_error(status))
    session = MagicMock()
    session.request = AsyncMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestApiClient:
    @pytest.fixture
    def client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_create_posts_the_body(self, client):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = make_run()
            body = {"kind": "synthetic", "files": {"a.yaml": "x"}}
            assert await client._eval_run_create("org", "agent", body) == make_run()
        args, kwargs = request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/v1/organizations/org/services/agent/eval-runs")
        assert kwargs["json"] == body

    @pytest.mark.asyncio
    async def test_get_reads_one_run(self, client):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = make_run()
            await client._eval_run_get("org", "agent", RUN_ID)
        args, _ = request.call_args
        assert args[0] == "GET"
        assert args[1].endswith(f"/services/agent/eval-runs/{RUN_ID}")

    @pytest.mark.asyncio
    async def test_list_sends_only_the_paging_it_is_given(self, client):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {"total_count": 0, "runs": []}
            await client._eval_runs_list("org", "agent", limit=5)
            assert request.call_args.kwargs["params"] == {"limit": 5}
            await client._eval_runs_list("org", "agent")
            assert request.call_args.kwargs["params"] is None

    @pytest.mark.asyncio
    async def test_cancel_posts_to_the_cancel_route_with_no_body(self, client):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = make_run("cancelled")
            await client._eval_run_cancel("org", "agent", RUN_ID)
        args, kwargs = request.call_args
        assert args[0] == "POST"
        assert args[1].endswith(f"/services/agent/eval-runs/{RUN_ID}/cancel")
        assert "json" not in kwargs

    @pytest.mark.asyncio
    async def test_names_are_quoted_into_the_path(self, client):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await client._eval_run_get("org", "a/b", "../x")
        assert "/services/a%2Fb/eval-runs/..%2Fx" in request.call_args.args[1]

    @pytest.mark.asyncio
    async def test_evals_not_available_reads_clearly(self, client):
        session = _session_answering(
            503, {"error": "Evals are not available.", "code": "EVALS-NOT-AVAILABLE"}
        )
        with (
            patch.object(aiohttp, "ClientSession", return_value=session),
            patch("pipecatcloud._utils.console_utils.console"),
        ):
            data, error = await client.eval_runs_list("org", "agent")
        assert data is None
        assert error == {"error": EVALS_NOT_AVAILABLE_MESSAGE, "code": "EVALS-NOT-AVAILABLE"}
        assert client.error_status == 503

    @pytest.mark.asyncio
    async def test_other_errors_keep_the_servers_words(self, client):
        session = _session_answering(409, {"error": "The run has already finished", "code": "409"})
        with (
            patch.object(aiohttp, "ClientSession", return_value=session),
            patch("pipecatcloud._utils.console_utils.console"),
        ):
            data, error = await client.eval_run_cancel("org", "agent", RUN_ID)
        assert error == {"error": "The run has already finished", "code": "409"}


# ---- review follow-ups ----


class TestInterruptionAsTheSynchronizerDeliversIt:
    """Ctrl-C reaches a command run by the synchronizer as a cancellation."""

    @pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
    def test_a_wait_stops_and_says_how_to_check_and_cancel(self, api, sleeps, scenario_file, exc):
        sleeps.side_effect = exc
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 130
        assert "still going" in result.output
        assert f"eval cancel {RUN_ID} --agent a" in result.output
        assert f"eval status {RUN_ID} --agent a --wait" in result.output
        api.eval_run_cancel.assert_not_awaited()

    def test_a_wait_in_json_mode_prints_the_run_seen_last(self, api, sleeps):
        api.eval_run_get.return_value = (make_run("running"), None)
        sleeps.side_effect = asyncio.CancelledError
        result = invoke("status", RUN_ID, "--agent", "a", "--wait", "--json")
        assert result.exit_code == 130
        assert json.loads(result.stdout)["status"] == "running"

    def test_a_cancellation_during_a_poll_request_stops_the_wait_too(self, api, sleeps):
        api.eval_run_get.side_effect = [(make_run("running"), None), asyncio.CancelledError()]
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == 130
        assert "still going" in result.output

    @pytest.mark.parametrize("exc", [KeyboardInterrupt, asyncio.CancelledError])
    def test_during_the_create_request_it_says_a_run_may_exist(self, api, scenario_file, exc):
        api.eval_run_create.side_effect = exc
        result = invoke("run", "my-agent", scenario_file)
        assert result.exit_code == 130
        assert "may exist" in result.output
        assert "eval list my-agent" in result.output

    def test_during_the_create_request_in_json_mode(self, api, scenario_file):
        api.eval_run_create.side_effect = asyncio.CancelledError
        result = invoke("run", "my-agent", scenario_file, "--json")
        assert result.exit_code == 130
        assert json.loads(result.stdout)["error"]["code"] == "CLI-INTERRUPTED"


class _RunningHandler(BaseHTTPRequestHandler):
    hit = threading.Event()

    def do_POST(self):  # noqa: N802
        source = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        setattr(self.server, "eval_source", source)
        body = json.dumps(make_run(kind=source["kind"], sessionId=source.get("sessionId"))).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        source = getattr(self.server, "eval_source", {})
        body = json.dumps(
            make_run(
                "running",
                deadlineAt="2099-01-01T00:00:00.000Z",
                kind=source.get("kind", "synthetic"),
                sessionId=source.get("sessionId"),
            )
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).hit.set()

    def log_message(self, format, *args):  # noqa: A002
        pass


class TestRealSigint:
    @pytest.mark.parametrize("extra", [[], ["--json"]])
    @pytest.mark.parametrize("command", ["status", "judge"])
    def test_sigint_while_waiting_prints_the_lines_and_exits_130(
        self, extra, command, criteria_file
    ):
        _RunningHandler.hit.clear()
        server = HTTPServer(("127.0.0.1", 0), _RunningHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {**os.environ, "PIPECAT_API_HOST": f"http://127.0.0.1:{server.server_port}"}
        code = (
            "from pipecatcloud.cli.entry_point import entrypoint_cli_typer as a;"
            "a(prog_name='pipecat cloud')"
        )
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                "eval",
                *(
                    ["status", RUN_ID, "--agent", "a", "--wait"]
                    if command == "status"
                    else ["judge", "a", SESSION_ID, "--criteria", str(criteria_file)]
                ),
                *extra,
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert _RunningHandler.hit.wait(30), "the command never asked for the run"
            # Now in the first sleep between polls.
            threading.Event().wait(0.7)
            proc.send_signal(signal.SIGINT)
            out, err = proc.communicate(timeout=30)
        finally:
            proc.kill()
            server.shutdown()
        assert proc.returncode == 130, (out, err)
        combined = out + err
        assert f"eval cancel {RUN_ID} --agent a" in combined
        assert "still going" in combined
        if extra:
            assert json.loads(out)["status"] == "running"
            if command == "judge":
                assert json.loads(out)["sessionId"] == SESSION_ID


class TestUnexpectedFailures:
    @pytest.mark.parametrize("flags", [[], ["--json"]])
    def test_an_unexpected_exception_exits_three_and_does_not_quote_itself(
        self, api, scenario_file, flags
    ):
        with patch(
            "pipecatcloud.cli.commands.evals.build_bundle", side_effect=ValueError("tok-123-boom")
        ):
            result = invoke("run", "a", scenario_file, *flags)
        assert result.exit_code == 3
        assert "ValueError" in result.output
        assert "tok-123-boom" not in result.output
        api.eval_run_create.assert_not_awaited()
        if flags:
            assert json.loads(result.stdout)["error"]["code"] == "CLI-INTERNAL"

    def test_a_usage_error_at_the_group_exits_three(self, api):
        result = invoke("bogus")
        assert result.exit_code == 3
        assert "No such command" in result.output


class TestWaitBounds:
    """make_run's run is 1800 s long (created 10:00, deadline 10:30).

    The wait gives it that, the API's 15 minutes to store a result and a 60 s
    sweep: 2760 s on the client's monotonic clock, from first seeing the run.
    """

    LIMIT = 1800 + 15 * 60 + 60

    @staticmethod
    def _advance(sleeps, clock, seconds):
        async def tick(_delay):
            clock["now"] += seconds

        sleeps.side_effect = tick

    def test_the_limit_is_the_runs_length_plus_the_apis_grace_and_sweep(self):
        assert evals._wait_limit(make_run()) == self.LIMIT

    def test_the_wait_outlasts_the_deadline_so_a_stuck_run_can_expire(self, api, sleeps, clock):
        ended = make_run("expired", None, {"reason": "Ran out of time.", "metrics": []})
        api.eval_run_get.side_effect = [
            (make_run("running"), None),
            (make_run("running"), None),
            (make_run("running"), None),
            (ended, None),
        ]
        # Polled at 1000 s, 2000 s (past the 1800 s deadline) and 2700 s, as expired.
        times = iter([1000, 1000, 700])

        async def tick(_delay):
            clock["now"] += next(times)

        sleeps.side_effect = tick
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == 2, result.output
        assert "expired" in result.output

    def test_a_wait_stops_once_it_is_past_the_limit(self, api, sleeps, clock):
        self._advance(sleeps, clock, 1000)
        api.eval_run_get.return_value = (make_run("running"), None)
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == 3
        assert "hasn't ended" in result.output
        assert f"eval status {RUN_ID}" in result.output
        assert sleeps.await_count == 3  # polled at 1000, 2000 and 3000 s, then stopped

    def test_in_json_mode_the_stop_is_an_error_object(self, api, sleeps, clock):
        self._advance(sleeps, clock, 3000)
        api.eval_run_get.return_value = (make_run("running"), None)
        result = invoke("status", RUN_ID, "--agent", "a", "--wait", "--json")
        assert result.exit_code == 3
        assert json.loads(result.stdout)["error"]["code"] == "CLI-WAIT"

    def test_one_second_inside_the_limit_it_keeps_waiting(self, api, sleeps, clock):
        self._advance(sleeps, clock, self.LIMIT - 1)
        api.eval_run_get.side_effect = [
            (make_run("running"), None),
            (make_run("completed", "passed"), None),
        ]
        result = invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert result.exit_code == 0, result.output

    def test_one_second_past_the_limit_it_stops(self, api, sleeps, clock):
        self._advance(sleeps, clock, self.LIMIT + 1)
        api.eval_run_get.return_value = (make_run("running"), None)
        assert invoke("status", RUN_ID, "--agent", "a", "--wait").exit_code == 3

    def test_the_wall_clock_plays_no_part(self, api, sleeps):
        # make_run's deadline is long past by the machine's clock; the wait is
        # timed from when the run was first seen.
        api.eval_run_get.side_effect = [
            (make_run("running"), None),
            (make_run("completed", "passed"), None),
        ]
        assert invoke("status", RUN_ID, "--agent", "a", "--wait").exit_code == 0

    def test_a_run_without_a_deadline_is_waited_for(self, api, sleeps, clock):
        self._advance(sleeps, clock, 10**7)
        api.eval_run_get.side_effect = [
            (make_run("running", deadlineAt=None), None),
            (make_run("running"), None),
            (make_run("completed", "passed"), None),
        ]
        assert invoke("status", RUN_ID, "--agent", "a", "--wait").exit_code == 0


class TestRetryAfter:
    def _fail_then_pass(self, api, retry_after):
        api.error_status = 503
        api.error_retry_after = retry_after
        api.eval_run_get.side_effect = [
            (make_run("running"), None),
            (None, {"error": "Unavailable", "code": "503"}),
            (make_run("completed", "passed"), None),
        ]

    def test_a_polls_retry_after_lengthens_the_next_pause(self, api, sleeps):
        self._fail_then_pass(api, "30")
        assert invoke("status", RUN_ID, "--agent", "a", "--wait").exit_code == 0
        assert [c.args[0] for c in sleeps.await_args_list] == [2.0, 30]

    def test_it_is_capped(self, api, sleeps):
        self._fail_then_pass(api, "3600")
        invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert [c.args[0] for c in sleeps.await_args_list][-1] == evals.POLL_RETRY_AFTER_CAP_SECONDS

    @pytest.mark.parametrize("value", [None, "soon", "-5"])
    def test_an_unusable_one_changes_nothing(self, api, sleeps, value):
        self._fail_then_pass(api, value)
        invoke("status", RUN_ID, "--agent", "a", "--wait")
        assert [c.args[0] for c in sleeps.await_args_list] == [2.0, 3.0]

    def test_creating_a_run_is_never_repeated(self, api, sleeps, scenario_file):
        api.error_status = 503
        api.error_retry_after = "1"
        api.eval_run_create.return_value = (None, {"error": "Unavailable", "code": "503"})
        result = invoke("run", "a", scenario_file)
        assert result.exit_code == 3
        assert api.eval_run_create.await_count == 1
        sleeps.assert_not_awaited()


class TestOutput:
    def test_the_status_hint_is_printed_once(self, api, scenario_file):
        result = invoke("run", "a", scenario_file)
        assert result.stdout.count(f"eval status {RUN_ID}") == 1


# ---- Several runs, and how they read ----

PASSED_RESULT = {
    "reason": "The agent passed the scenario.",
    "metrics": [
        {"name": "concise", "score": 1, "passed": True, "reason": "all 2 turn(s)"},
    ],
    "details": {
        "kind": "simulation",
        "turns": [
            {"turn": 1, "checks": [{"name": "concise", "passed": True}]},
            {"turn": 2, "checks": [{"name": "concise", "passed": True}]},
        ],
        "failures": [],
        "goal": {"criterion": "a table for two was booked", "verdict": "yes", "passed": True},
    },
    "conversation": [
        {"role": "assistant", "content": "Hello, how many in your party?"},
        {"role": "user", "content": "Two, please."},
        {"role": "tool_call", "name": "book_table", "arguments": {"size": 2}, "cancelled": False},
        {"role": "assistant", "content": "Booked a table for two."},
    ],
    "ignored": [],
    "ignoredTotal": 0,
    "truncated": False,
}

METRIC_FAILED_RESULT = {
    **PASSED_RESULT,
    "reason": "The agent fell short on the metric 'concise'.",
    "metrics": [{"name": "concise", "score": 0.5, "passed": False, "reason": "1 of 2 turn(s)"}],
    "details": {
        **PASSED_RESULT["details"],
        "turns": [
            {"turn": 1, "checks": [{"name": "concise", "passed": True}]},
            {"turn": 2, "checks": [{"name": "concise", "passed": False, "reason": "rambles on"}]},
        ],
    },
}

ENDED = {
    "sessionId": SESSION_ID,
    "startedAt": "2026-11-07T10:00:02.000Z",
    "finishedAt": "2026-11-07T10:00:20.500Z",
}

OTHER_ID = "99999999-2222-4333-8444-555555555555"

HEADED = """\
name: calls
persona: A caller named Jamie.
max_turns: 8
scenarios:
  - name: book
    goal: Book a table for two,
      then end the call.
    success: A table was booked.
  - name: hours
    goal: Ask the hours.
    success: Hours were given.
    simulator: {service: ollama, model: gemma4:12b}
  - name: scripted
    turns:
      - user: Hi
"""


def invoke_rich(*args):
    return runner.invoke(entrypoint_cli_typer, ["--output", "rich", "eval", *map(str, args)])


@pytest.fixture
def two_runs(api, tmp_path):
    """A file of two scenarios, whose runs the API tells apart by id."""
    path = tmp_path / "calls.yaml"
    path.write_text(TWO_SCENARIOS, encoding="utf-8")
    created = {
        "book": make_run(id=RUN_ID, scenarioName="book"),
        "hours": make_run(id=OTHER_ID, scenarioName="hours"),
    }

    async def create(org, agent, body):
        return created[body["scenarioName"]], None

    api.eval_run_create.side_effect = create
    ended = {
        RUN_ID: make_run(
            "completed", "passed", PASSED_RESULT, id=RUN_ID, scenarioName="book", **ENDED
        ),
        OTHER_ID: make_run(
            "completed", "failed", METRIC_FAILED_RESULT, id=OTHER_ID, scenarioName="hours", **ENDED
        ),
    }
    polls = {RUN_ID: [make_run("running", id=RUN_ID, scenarioName="book")], OTHER_ID: []}

    async def get(org, agent, run_id):
        queue = polls[run_id]
        return (queue.pop(0) if queue else ended[run_id]), None

    api.eval_run_get.side_effect = get
    return path


class TestSeveralRuns:
    def test_wait_follows_every_run_and_exits_with_the_worst(self, api, sleeps, two_runs):
        result = invoke("run", "a", two_runs, "--wait")
        assert result.exit_code == 1, result.output
        out = result.stdout
        assert "✓ a calls/book (18500ms)" in out
        assert "✗ a calls/hours (18500ms)" in out
        assert "1/2 passed  ·  2/2 done" in out
        assert f"eval list a" in out

    def test_runs_are_polled_in_one_loop_until_each_has_ended(self, api, sleeps, two_runs):
        invoke("run", "a", two_runs, "--wait")
        polled = [c.args[2] for c in api.eval_run_get.await_args_list]
        # The first pass polls both; the second only the one still running.
        assert polled == [RUN_ID, OTHER_ID, RUN_ID]
        assert [c.args[0] for c in sleeps.await_args_list] == [2.0, 3.0]

    def test_only_the_runs_that_did_not_pass_are_explained(self, api, sleeps, two_runs):
        out = invoke("run", "a", two_runs, "--wait").stdout
        assert "rambles on" in out and "turn 2:" in out and "Booked a table for two." in out
        assert "1/2 turns" in out
        assert f"eval status {OTHER_ID} --agent a -v" in out
        assert f"agent logs a --session-id {SESSION_ID}" in out
        # The passing run gets its line and nothing more.
        assert f"eval status {RUN_ID} --agent a -v" not in out

    def test_verbose_explains_every_run_with_its_conversation(self, api, sleeps, two_runs):
        out = invoke("run", "a", two_runs, "--wait", "-v").stdout
        assert out.count("bot: Hello, how many in your party?") == 2
        assert "→ book_table(size=2)" in out
        assert "yes — a table for two was booked" in out

    def test_a_run_that_cannot_start_is_a_row_and_the_others_go_on(self, api, sleeps, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")

        async def create(org, agent, body):
            if body["scenarioName"] == "hours":
                return None, {
                    "error": "modality 'audio' isn't supported on Pipecat Cloud",
                    "code": "400",
                }
            return make_run(scenarioName="book"), None

        api.eval_run_create.side_effect = create
        api.eval_run_get.return_value = (
            make_run("completed", "passed", PASSED_RESULT, **ENDED),
            None,
        )
        result = invoke("run", "a", path, "--wait")
        assert result.exit_code == 3, result.output
        out = result.stdout
        assert "✓ a calls/book" in out
        assert "✗ a calls/hours modality 'audio' isn't supported on Pipecat Cloud" in out
        assert "1/2 passed  ·  2/2 done" in out

    def test_json_is_the_list_of_runs(self, api, sleeps, two_runs):
        result = invoke("run", "a", two_runs, "--wait", "--json")
        assert result.exit_code == 1
        runs = json.loads(result.stdout)
        assert [(r["id"], r["verdict"]) for r in runs] == [(RUN_ID, "passed"), (OTHER_ID, "failed")]

    def test_without_wait_each_run_is_listed_with_its_id(self, api, two_runs):
        result = invoke("run", "a", two_runs)
        assert result.exit_code == 0, result.output
        assert RUN_ID in result.stdout and OTHER_ID in result.stdout
        assert "eval list a" in result.stdout
        api.eval_run_get.assert_not_awaited()

    def test_ctrl_c_leaves_them_all_going_and_says_how_to_follow_them(self, api, sleeps, two_runs):
        sleeps.side_effect = KeyboardInterrupt
        result = invoke("run", "a", two_runs, "--wait")
        assert result.exit_code == 130
        assert "2 runs are still going" in result.output
        assert "eval list a" in result.output
        assert f"eval cancel {RUN_ID} --agent a" in result.output
        assert f"eval cancel {OTHER_ID} --agent a" in result.output
        api.eval_run_cancel.assert_not_awaited()

    def test_rich_mode_draws_the_table_and_its_summary(self, api, sleeps, two_runs):
        result = invoke_rich("run", "a", two_runs, "--wait")
        assert result.exit_code == 1, result.output
        out = result.stdout
        assert "calls/book" in out and "18500ms" in out
        assert "1/2 passed  ·  2/2 done" in out


class TestSubmissionRecovery:
    @pytest.mark.parametrize("wait_flag", ["--wait", "--no-wait"])
    @pytest.mark.parametrize("all_refused", [False, True])
    def test_json_accounts_for_every_selected_scenario(
        self, api, sleeps, tmp_path, wait_flag, all_refused
    ):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")
        refusal = {"error": "The scenario was refused.", "code": "SCENARIO-REFUSED"}

        async def create(org, agent, body):
            if all_refused or body["scenarioName"] == "hours":
                return None, refusal
            return make_run(scenarioName="book"), None

        api.eval_run_create.side_effect = create
        api.eval_run_get.return_value = (make_run("completed", "passed"), None)
        result = invoke("run", "a", path, wait_flag, "--json")
        assert result.exit_code == 3, result.output
        rows = json.loads(result.stdout)
        assert [r["scenarioName"] for r in rows] == ["book", "hours"]
        assert rows[1] == {
            "scenarioPath": "calls.yaml",
            "scenarioName": "hours",
            "submissionStatus": "error",
            "error": refusal,
        }
        if all_refused:
            assert all(r["error"] == refusal for r in rows)
            assert all("id" not in r for r in rows)
            api.eval_run_get.assert_not_awaited()
        else:
            assert rows[0]["id"] == RUN_ID
            assert "submissionStatus" not in rows[0]
        assert api.eval_run_create.await_count == 2
        api.eval_run_cancel.assert_not_awaited()

    def test_waiting_interrupt_keeps_refusals_and_the_latest_accepted_run(
        self, api, sleeps, tmp_path
    ):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")
        refusal = {"error": "The scenario was refused.", "code": "SCENARIO-REFUSED"}
        api.eval_run_create.side_effect = [(make_run(), None), (None, refusal)]
        api.eval_run_get.return_value = (make_run("running"), None)
        sleeps.side_effect = [None, asyncio.CancelledError()]
        result = invoke("run", "a", path, "--wait", "--json")
        assert result.exit_code == 130, result.output
        rows = json.loads(result.stdout)
        assert len(rows) == 2
        assert rows[0]["id"] == RUN_ID and rows[0]["status"] == "running"
        assert rows[1]["scenarioName"] == "hours" and rows[1]["error"] == refusal
        assert f"eval cancel {RUN_ID} --agent a" in result.stderr
        api.eval_run_get.assert_awaited_once()
        api.eval_run_cancel.assert_not_awaited()

    @pytest.mark.parametrize("output", ["plain", "rich", "json"])
    def test_creation_interrupt_distinguishes_accepted_unknown_and_unattempted(
        self, api, tmp_path, output
    ):
        path = tmp_path / "calls.yaml"
        path.write_text(
            TWO_SCENARIOS
            + "  - name: later\n    turns: [{user: hi}]\n"
            + "  - name: last\n    turns: [{user: bye}]\n",
            encoding="utf-8",
        )
        refusal = {"error": "The scenario was refused.", "code": "SCENARIO-REFUSED"}
        accepted = make_run(scenarioName="hours")
        api.eval_run_create.side_effect = [
            (None, refusal),
            (accepted, None),
            asyncio.CancelledError(),
        ]
        result = runner.invoke(
            entrypoint_cli_typer,
            ["--output", output, "eval", "run", "a", str(path)],
        )
        assert result.exit_code == 130, result.output
        human = " ".join((result.stderr if output == "json" else result.stdout).split())
        assert RUN_ID in human
        assert f"eval status {RUN_ID} --agent a --wait" in human
        assert f"eval cancel {RUN_ID} --agent a" in human
        assert "calls/later" in human and "may have reached" in human
        assert "Not submitted: calls/last" in human
        if output == "json":
            rows = json.loads(result.stdout)
            assert [r["scenarioName"] for r in rows] == ["book", "hours", "later", "last"]
            assert rows[0]["submissionStatus"] == "error" and rows[0]["error"] == refusal
            assert rows[1] == accepted
            assert rows[2]["submissionStatus"] == "unknown"
            assert rows[2]["error"]["code"] == "CLI-INTERRUPTED"
            assert rows[3] == {
                "scenarioPath": "calls.yaml",
                "scenarioName": "last",
                "submissionStatus": "not_submitted",
            }
        assert [c.args[2]["scenarioName"] for c in api.eval_run_create.await_args_list] == [
            "book",
            "hours",
            "later",
        ]
        api.eval_run_get.assert_not_awaited()
        api.eval_run_cancel.assert_not_awaited()


class TestScenarioHeader:
    def test_it_lists_each_scenario_as_pipecat_eval_does(self, api, sleeps, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(HEADED, encoding="utf-8")
        api.eval_run_get.return_value = (
            make_run("completed", "passed", PASSED_RESULT, **ENDED),
            None,
        )
        out = invoke("run", "a", path, "--wait").stdout
        assert "Scripted scenarios:\n  calls/scripted:" in out
        assert "Simulated scenarios:\n  calls/book:" in out
        assert "user  -> modality: text | persona: hosted | max_turns: 8" in out
        assert "judge -> modality: text | eval: hosted" in out
        assert "goal  -> Book a table for two, then end the call." in out
        # A scenario's own simulator isn't what runs on Pipecat Cloud.
        assert "gemma4" not in out
        assert out.index("Simulated scenarios:") < out.index("✓ a calls/book")

    def test_audio_names_its_speech_and_transcription(self, api, sleeps, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(
            SCENARIO.replace(
                "    success: A table was booked.\n",
                "    success: A table was booked.\n"
                "    user: {modality: audio, speech: {service: kokoro, voice: af_heart}}\n"
                "    judge: {modality: audio}\n",
            ),
            encoding="utf-8",
        )
        api.eval_run_get.return_value = (
            make_run("completed", "passed", PASSED_RESULT, **ENDED),
            None,
        )
        out = invoke("run", "a", path, "--wait").stdout
        assert "user  -> modality: audio | speech: kokoro/af_heart | persona: hosted" in out
        assert "judge -> modality: audio | transcription: moonshine | eval: hosted" in out

    def test_it_is_left_out_of_json_and_of_a_run_without_wait(self, api, scenario_file):
        assert "scenarios:" not in invoke("run", "a", scenario_file).stdout
        assert "scenarios:" not in invoke("run", "a", scenario_file, "--json").stdout


class TestRows:
    def test_a_running_row_shows_how_long_it_has_run(self, clock):
        row = evals._Row("calls/book", run=make_run("running"), first_running=0.0)
        clock["now"] = 9.44
        # Dim, and padded to the width of `2m 04s`, as `pipecat eval` draws it.
        assert evals._row_cells(row, "a")[3] == "[dim]  9.4s[/dim]"

    @pytest.mark.parametrize("status", ["queued", "starting"])
    def test_a_waiting_row_says_what_it_waits_on(self, status):
        mark, _, _, when = evals._row_cells(evals._Row("calls/book", run=make_run(status)), "a")
        assert "·" in mark and status in when

    def test_elapsed_reads_like_pipecat_eval(self):
        assert evals._fmt_seconds(57.04) == "57.0s"
        assert evals._fmt_seconds(89) == "1m 29s"

    def test_the_status_of_one_ended_run_explains_it_even_when_it_passed(self, api):
        api.eval_run_get.return_value = (
            make_run("completed", "passed", PASSED_RESULT, **ENDED),
            None,
        )
        out = invoke("status", RUN_ID, "--agent", "a").stdout
        assert "✓ a calls/book (18500ms)" in out
        assert "yes — a table for two was booked" in out
        assert "concise: 1.00" in out and "2/2 turns" in out


class TestWaitIsTheDefault:
    def test_run_waits_and_exits_with_the_verdict_unless_told_not_to(
        self, api, sleeps, scenario_file
    ):
        api.eval_run_get.side_effect = [(make_run("completed", "failed", FAILED_RESULT), None)]
        result = runner.invoke(entrypoint_cli_typer, ["eval", "run", "a", str(scenario_file)])
        assert result.exit_code == 1, result.output
        api.eval_run_get.assert_awaited_once()

    def test_no_wait_starts_the_run_and_returns(self, api, scenario_file):
        result = runner.invoke(
            entrypoint_cli_typer, ["eval", "run", "a", str(scenario_file), "--no-wait"]
        )
        assert result.exit_code == 0, result.output
        api.eval_run_get.assert_not_awaited()

    def test_status_still_shows_the_run_as_it_is(self, api):
        api.eval_run_get.return_value = (make_run("running"), None)
        result = runner.invoke(entrypoint_cli_typer, ["eval", "status", RUN_ID, "--agent", "a"])
        assert result.exit_code == 0
        assert api.eval_run_get.await_count == 1


class TestPipecatEvalStyle:
    def test_failures_are_listed_under_their_heading(self, api, sleeps, two_runs):
        out = invoke("run", "a", two_runs, "--wait").stdout
        assert "Failures (1 of 2):" in out
        failures = out[out.index("Failures (1 of 2):") :]
        assert "✗ a calls/hours" in failures and "calls/book" not in failures

    def test_settings_name_the_agent_and_the_runs(self, api, sleeps, two_runs):
        out = invoke("run", "a", two_runs, "--wait").stdout
        assert "Settings:\n  agent       -> a\n  runs        -> 2\n" in out

    def test_config_keywords_take_pipecat_evals_colors(self):
        view = {
            "persona": "x",
            "max_turns": 8,
            "user": {"modality": "audio", "speech": {"service": "kokoro", "voice": "af_heart"}},
            "judge": {"modality": "audio"},
        }
        user, judge = (evals._config_markup(k, b) for k, b in evals._config_lines(view)[:2])
        assert user.startswith("[bold]user [/bold][dim] -> [/dim]")
        assert "[yellow]modality:[/yellow] audio" in user
        assert "[green]speech:[/green] kokoro/af_heart" in user
        assert "[magenta]persona:[/magenta] hosted" in user
        assert "[cyan]max_turns:[/cyan] 8" in user
        assert "[dim] | [/dim]" in user
        assert "[green]transcription:[/green] moonshine" in judge
        assert "[magenta]eval:[/magenta] hosted" in judge

    def test_durations_read_like_pipecat_eval_and_never_say_60s(self):
        assert evals._fmt_seconds(59.96) == "1m 00s"
        assert evals._fmt_seconds(12.34) == "12.3s"


# ---- Finished-session judging ----


def judgment(session_id=SESSION_ID, run_id=RUN_ID, **extra):
    return make_run(
        **{
            "id": run_id,
            "kind": "session",
            "sessionId": session_id,
            "scenarioPath": "criteria.yaml",
            "scenarioName": "call",
            **extra,
        }
    )


class TestJudge:
    def test_submits_one_finished_session_with_unmodified_criteria(self, api, criteria_file):
        accepted = judgment()
        api.eval_run_create.return_value = accepted, None
        result = invoke("judge", "a", SESSION_ID, "--criteria", criteria_file, "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == accepted
        api.eval_run_create.assert_awaited_once_with(
            "test-org",
            "a",
            {
                "kind": "session",
                "sessionId": SESSION_ID,
                "files": {"criteria.yaml": CRITERIA},
                "scenarioPath": "criteria.yaml",
                "scenarioName": "call",
            },
        )
        api.eval_run_get.assert_not_awaited()

    def test_sessions_are_submitted_in_input_order(self, api, criteria_file):
        accepted = [judgment(), judgment(SECOND_SESSION_ID, OTHER_ID)]
        api.eval_run_create.side_effect = [(run, None) for run in accepted]
        result = invoke(
            "judge", "a", SESSION_ID, SECOND_SESSION_ID, "--criteria", criteria_file, "--json"
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == accepted
        assert [c.args[2]["sessionId"] for c in api.eval_run_create.await_args_list] == [
            SESSION_ID,
            SECOND_SESSION_ID,
        ]

    def test_deadline_and_organization_apply_to_each_judgment(self, api, criteria_file):
        api.eval_run_create.return_value = judgment(), None
        result = invoke(
            "judge",
            "a",
            SESSION_ID,
            "--criteria",
            criteria_file,
            "--deadline",
            "300",
            "-o",
            "other-org",
        )
        assert result.exit_code == 0, result.output
        org, agent, body = api.eval_run_create.await_args.args
        assert (org, agent, body["deadlineSeconds"]) == ("other-org", "a", 300)
        assert "params" not in body

    @pytest.mark.parametrize("sessions", [["invalid"], [SESSION_ID, "invalid"]])
    def test_all_session_ids_are_validated_before_any_submission(
        self, api, criteria_file, sessions
    ):
        result = invoke("judge", "a", *sessions, "--criteria", criteria_file)
        assert result.exit_code == 3, result.output
        api.eval_run_create.assert_not_awaited()

    def test_canonical_duplicates_are_refused_before_any_submission(self, api, criteria_file):
        result = invoke(
            "judge", "a", SESSION_ID, SESSION_ID.upper(), "--criteria", criteria_file, "--json"
        )
        assert result.exit_code == 3, result.output
        assert json.loads(result.stdout)["error"]["code"] == "CLI-USAGE"
        assert "more than once" in result.stdout
        api.eval_run_create.assert_not_awaited()

    @pytest.mark.parametrize("missing", ["sessions", "criteria"])
    def test_sessions_and_criteria_are_required(self, api, criteria_file, missing):
        args = ["--criteria", criteria_file] if missing == "sessions" else [SESSION_ID]
        result = invoke("judge", "a", *args)
        assert result.exit_code == 3, result.output
        api.eval_run_create.assert_not_awaited()

    @pytest.mark.parametrize(
        "flag,value", [("--scenario", "call"), ("--param", "defaultTimeoutMs=1")]
    )
    def test_synthetic_selection_and_runtime_settings_are_not_judge_options(
        self, api, criteria_file, flag, value
    ):
        result = invoke("judge", "a", SESSION_ID, "--criteria", criteria_file, flag, value)
        assert result.exit_code == 3, result.output
        api.eval_run_create.assert_not_awaited()

    def test_run_no_longer_accepts_the_unreleased_session_option(self, api, scenario_file):
        result = invoke("run", "a", scenario_file, "--session", SESSION_ID)
        assert result.exit_code == 3, result.output
        api.eval_run_create.assert_not_awaited()

    @pytest.mark.parametrize("entries", ["[]", "[{name: a}, {name: b}]", "[{name: ''}]", "[{}]"])
    def test_criteria_requires_one_named_entry(self, api, tmp_path, entries):
        path = tmp_path / "criteria.yaml"
        path.write_text(f"scenarios: {entries}\n", encoding="utf-8")
        result = invoke("judge", "a", SESSION_ID, "--criteria", path)
        assert result.exit_code == 3, result.output
        assert "exactly one named entry" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_criteria_include_closure_uses_the_existing_safe_root(self, api, tmp_path):
        (tmp_path / "policies").mkdir()
        (tmp_path / "shared").mkdir()
        path = tmp_path / "policies" / "criteria.yaml"
        path.write_text(
            "name: session_review\nscenarios:\n  - name: call\n"
            "    success: !include ../shared/success.yaml\n",
            encoding="utf-8",
        )
        (tmp_path / "shared" / "success.yaml").write_text("A correct answer.\n", encoding="utf-8")
        api.eval_run_create.return_value = judgment(), None
        result = invoke("judge", "a", SESSION_ID, "--criteria", path, "--root", tmp_path)
        assert result.exit_code == 0, result.output
        body = api.eval_run_create.await_args.args[2]
        assert sorted(body["files"]) == ["policies/criteria.yaml", "shared/success.yaml"]
        assert body["scenarioPath"] == "policies/criteria.yaml"
        assert "!include ../shared/success.yaml" in body["files"]["policies/criteria.yaml"]

    def test_hidden_includes_are_refused_before_submission(self, api, criteria_file):
        criteria_file.write_text(CRITERIA + "private: !include .env\n", encoding="utf-8")
        (criteria_file.parent / ".env").write_text("TOKEN=private\n", encoding="utf-8")
        result = invoke("judge", "a", SESSION_ID, "--criteria", criteria_file)
        assert result.exit_code == 3, result.output
        assert "hidden" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_header_names_the_session_and_actual_criteria_without_fake_caller(
        self, api, sleeps, tmp_path
    ):
        path = tmp_path / "criteria.yaml"
        path.write_text(
            CRITERIA.replace(
                "    success:",
                "    persona: Fake caller canary.\n    goal: Fake goal canary.\n    success:",
            ),
            encoding="utf-8",
        )
        accepted = judgment(status="completed", verdict="passed", result=PASSED_RESULT)
        api.eval_run_create.return_value = accepted, None
        out = invoke("judge", "a", SESSION_ID, "--criteria", path, "--wait").stdout
        assert "Criteria:" in out and "session_review/call" in out
        assert "Sessions:" in out and SESSION_ID in out
        assert "judge -> modality: text | eval: hosted" in out
        assert "success -> The bot answered" in out
        assert "Fake caller canary" not in out and "Fake goal canary" not in out
        assert "user  ->" not in out and "goal  ->" not in out
        assert "Simulated scenarios:" not in out

    def test_session_status_keeps_source_identity(self, api):
        api.eval_run_get.return_value = judgment(status="running"), None
        out = invoke("status", RUN_ID, "--agent", "a").stdout
        assert "Session" in out and SESSION_ID in out
        api.eval_run_get.return_value = judgment(status="completed", verdict="passed"), None
        out = invoke("status", RUN_ID, "--agent", "a").stdout
        assert f"criteria/call ({SESSION_ID})" in out

    @pytest.mark.parametrize("wait_flag", ["--wait", "--no-wait"])
    @pytest.mark.parametrize("single", [True, False])
    def test_refused_judgments_preserve_session_identity(
        self, api, criteria_file, wait_flag, single
    ):
        refusal = {"error": "Conversation not published", "code": "EVAL-CONVERSATION-MISSING"}
        api.eval_run_create.return_value = None, refusal
        sessions = [SESSION_ID] if single else [SESSION_ID, SECOND_SESSION_ID]
        result = invoke("judge", "a", *sessions, "--criteria", criteria_file, wait_flag, "--json")
        assert result.exit_code == 3, result.output
        rows = json.loads(result.stdout)
        rows = [rows] if single else rows
        assert [row["sessionId"] for row in rows] == sessions
        assert all(row["submissionStatus"] == "error" and row["error"] == refusal for row in rows)
        assert all("id" not in row for row in rows)
        assert api.eval_run_create.await_count == len(sessions)
        api.eval_run_get.assert_not_awaited()
        api.eval_run_cancel.assert_not_awaited()

    @pytest.mark.parametrize("output", ["plain", "rich", "json"])
    def test_partial_submission_interrupt_keeps_accepted_unknown_and_unattempted_ids(
        self, api, criteria_file, output
    ):
        third = "e3b7eaef-058f-4219-bd3e-4dc303a3d656"
        accepted = judgment()
        api.eval_run_create.side_effect = [(accepted, None), asyncio.CancelledError()]
        result = runner.invoke(
            entrypoint_cli_typer,
            [
                "--output",
                output,
                "eval",
                "judge",
                "a",
                SESSION_ID,
                SECOND_SESSION_ID,
                third,
                "--criteria",
                str(criteria_file),
                "--no-wait",
            ],
        )
        assert result.exit_code == 130, result.output
        human = result.stderr if output == "json" else result.stdout
        assert RUN_ID in human and SECOND_SESSION_ID in human and third in human
        if output == "json":
            rows = json.loads(result.stdout)
            assert rows[0] == accepted
            assert rows[1]["sessionId"] == SECOND_SESSION_ID
            assert rows[1]["submissionStatus"] == "unknown"
            assert rows[2]["sessionId"] == third and rows[2]["submissionStatus"] == "not_submitted"
        assert api.eval_run_create.await_count == 2
        api.eval_run_cancel.assert_not_awaited()

    def test_single_interrupted_submission_preserves_unknown_session_identity(
        self, api, criteria_file
    ):
        api.eval_run_create.side_effect = asyncio.CancelledError()
        result = invoke("judge", "a", SESSION_ID, "--criteria", criteria_file, "--json")
        assert result.exit_code == 130, result.output
        value = json.loads(result.stdout)
        assert value["sessionId"] == SESSION_ID and value["submissionStatus"] == "unknown"
        assert value["error"]["code"] == "CLI-INTERRUPTED"
        assert "id" not in value
        api.eval_run_create.assert_awaited_once()
        api.eval_run_cancel.assert_not_awaited()

    def test_waiting_interrupt_keeps_failed_siblings_and_latest_accepted_session(
        self, api, sleeps, criteria_file
    ):
        refusal = {"error": "Missing conversation", "code": "CONVERSATION-MISSING"}
        api.eval_run_create.side_effect = [(judgment(), None), (None, refusal)]
        running = judgment(status="running")
        api.eval_run_get.return_value = running, None
        sleeps.side_effect = [None, asyncio.CancelledError()]
        result = invoke(
            "judge",
            "a",
            SESSION_ID,
            SECOND_SESSION_ID,
            "--criteria",
            criteria_file,
            "--wait",
            "--json",
        )
        assert result.exit_code == 130, result.output
        rows = json.loads(result.stdout)
        assert rows[0] == running
        assert rows[1]["sessionId"] == SECOND_SESSION_ID and rows[1]["error"] == refusal
        api.eval_run_cancel.assert_not_awaited()

    def test_judge_waits_by_default_and_uses_the_shared_exit_priority(
        self, api, sleeps, criteria_file
    ):
        api.eval_run_create.side_effect = [
            (judgment(), None),
            (judgment(SECOND_SESSION_ID, OTHER_ID), None),
        ]
        ended = {
            RUN_ID: judgment(status="completed", verdict="passed"),
            OTHER_ID: judgment(SECOND_SESSION_ID, OTHER_ID, status="completed", verdict="failed"),
        }

        async def get(org, agent, run_id):
            return ended[run_id], None

        api.eval_run_get.side_effect = get
        result = runner.invoke(
            entrypoint_cli_typer,
            [
                "eval",
                "judge",
                "a",
                SESSION_ID,
                SECOND_SESSION_ID,
                "--criteria",
                str(criteria_file),
                "--json",
            ],
        )
        assert result.exit_code == 1, result.output
        assert json.loads(result.stdout) == [ended[RUN_ID], ended[OTHER_ID]]
        assert api.eval_run_get.await_count == 2
