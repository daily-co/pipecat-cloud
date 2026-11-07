"""Tests for `pipecat cloud eval run | status | list | cancel` and their API methods."""

import json
import sys
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
def api():
    """A stand-in for the CLI's API client; each test sets the answers it needs."""
    with patch("pipecatcloud.cli.commands.evals.API") as mock_api:
        mock_api.bubble_error.return_value = mock_api
        mock_api.error_status = None
        mock_api.eval_run_create = AsyncMock(return_value=(make_run(), None))
        mock_api.eval_run_get = AsyncMock(return_value=(make_run(), None))
        mock_api.eval_runs_list = AsyncMock(return_value=({"total_count": 0, "runs": []}, None))
        mock_api.eval_run_cancel = AsyncMock(return_value=(make_run(status="cancelled"), None))
        yield mock_api


@pytest.fixture
def sleeps():
    with patch("pipecatcloud.cli.commands.evals.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        yield mock_sleep


def invoke(*args):
    return runner.invoke(entrypoint_cli_typer, ["eval", *map(str, args)])


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

    def test_several_scenarios_without_a_choice_is_refused_before_any_request(self, api, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(TWO_SCENARIOS, encoding="utf-8")
        result = invoke("run", "a", path)
        assert result.exit_code == 3
        assert "book" in result.output and "hours" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_unknown_scenario_name_is_refused_before_any_request(self, api, scenario_file):
        result = invoke("run", "a", scenario_file, "--scenario", "nope")
        assert result.exit_code == 3
        assert "nope" in result.output and "book" in result.output
        api.eval_run_create.assert_not_awaited()

    def test_session_run_sends_the_session_and_its_kind(self, api, scenario_file):
        result = invoke("run", "a", scenario_file, "--session", SESSION_ID)
        assert result.exit_code == 0, result.output
        body = api.eval_run_create.await_args.args[2]
        assert body["kind"] == "session"
        assert body["sessionId"] == SESSION_ID
        assert "params" not in body

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

    def test_params_with_a_session_are_refused(self, api, scenario_file):
        result = invoke(
            "run", "a", scenario_file, "--session", SESSION_ID, "--param", "defaultTimeoutMs=1"
        )
        assert result.exit_code == 3
        assert "synthetic" in result.output
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
        result = invoke(
            "run", "a", scenario_file, "--session", SESSION_ID, "--param", "x=1", "--json"
        )
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
        assert "PASSED" in result.stdout
        assert "All good." in result.stdout
        assert api.eval_run_get.await_count == 2

    def test_a_failed_run_exits_one_and_shows_why(self, api, sleeps, scenario_file):
        ended = make_run("completed", "failed", FAILED_RESULT)
        api.eval_run_get.side_effect = [(ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 1, result.output
        out = result.stdout
        assert "FAILED" in out
        assert "The caller was never told the closing time." in out
        assert "judge_no" in out and "Hours were wrong" in out
        assert "accurate" in out and "0.2" in out and "Wrong hours given." in out

    @pytest.mark.parametrize("status", ENDED_WITHOUT_VERDICT)
    def test_an_ended_run_without_a_verdict_exits_two(self, api, sleeps, scenario_file, status):
        ended = make_run(status, None, {"reason": "Ended early.", "metrics": [], "details": None})
        api.eval_run_get.side_effect = [(ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 2, result.output
        assert "NO VERDICT" in result.stdout
        assert status in result.stdout
        assert "Ended early." in result.stdout

    def test_a_skipped_run_exits_zero_and_says_skipped(self, api, sleeps, scenario_file):
        ended = make_run("completed", "skipped", {"reason": "Nothing to judge.", "metrics": []})
        api.eval_run_get.side_effect = [(ended, None)]
        result = invoke("run", "a", scenario_file, "--wait")
        assert result.exit_code == 0, result.output
        assert "SKIPPED" in result.stdout

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
        assert "FAILED" in result.stdout
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
        assert "Showing 1 of 40" in result.stdout

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
