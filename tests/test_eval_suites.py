"""Tests for suite runs (`eval run` as one, `eval suite ...`) and bundles (`eval bundle ...`)."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.eval_bundle import (
    MAX_BUNDLE_BYTES,
    BundleError,
    collect_directory,
)
from pipecatcloud.api import _API
from pipecatcloud.cli.commands import eval_suites
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

SUITE_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
REVISION_ID = "99999999-8888-4777-8666-555555555555"
BUNDLE_ID = "12121212-3434-4565-8787-909090909090"
RUN_A = "11111111-2222-4333-8444-555555555555"
RUN_B = "11111111-2222-4333-8444-666666666666"
FINGERPRINT = "jfp1:" + "a" * 64
OTHER_FINGERPRINT = "jfp1:" + "b" * 64

SCENARIOS = """\
name: calls
scenarios:
  - name: book
    persona: A caller.
    goal: Book a table.
    success: A table was booked.
  - name: hours
    persona: Another caller.
    goal: Ask the hours.
    success: Hours were given.
"""


def counts(planned=2, pending=0, live=0, passed=0, failed=0, skipped=0, without=0):
    terminal = passed + failed + skipped + without
    return {
        "planned": planned,
        "pending": pending,
        "live": live,
        "terminal": terminal,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "withoutVerdict": without,
    }


def make_suite(status="queued", verdict=None, measurement=False, **extra):
    suite = {
        "id": SUITE_ID,
        "serviceName": "agent",
        "region": "us-west",
        "bundleRevisionId": REVISION_ID,
        "status": status,
        "target": {"scenarioPath": "calls.yaml"},
        "filters": None,
        "repeat": {"requested": None, "suite": None, "effective": None},
        "measurement": measurement,
        "concurrency": {"requested": None, "suite": None, "effective": 4},
        "deadlineSeconds": 43200,
        "entries": [{"index": 0, "label": "", "concurrency": None, "body": None}],
        "counts": counts(pending=2) if status == "queued" else counts(),
        "verdict": verdict,
        "passRates": None,
        "cancelRequestedAt": None,
        "error": None,
        "fingerprint": None,
        "mixed": False,
        "deadlineAt": "2026-11-13T22:00:00.000Z",
        "createdAt": "2026-11-13T10:00:00.000Z",
        "startedAt": None,
        "finishedAt": None,
    }
    suite.update(extra)
    return suite


def ended(verdict: str | None = "passed", status="completed", **extra):
    kwargs = {"passed": 2} if verdict == "passed" else {}
    if verdict == "failed":
        kwargs = {"passed": 1, "failed": 1}
    elif verdict == "skipped":
        kwargs = {"skipped": 2}
    elif verdict is None:
        kwargs = {"passed": 1, "without": 1}
    extra.setdefault("counts", counts(**kwargs))
    return make_suite(
        status=status, verdict=verdict, finishedAt="2026-11-13T10:05:00.000Z", **extra
    )


def make_run(run_id=RUN_A, verdict="passed", status="completed", name="book", **extra):
    run = {
        "id": run_id,
        "kind": "synthetic",
        "status": status,
        "verdict": verdict,
        "scenarioPath": "calls.yaml",
        "scenarioName": name,
        "suiteRunId": SUITE_ID,
        "sessionId": None,
        "createdAt": "2026-11-13T10:00:00.000Z",
        "startedAt": "2026-11-13T10:00:01.000Z",
        "finishedAt": "2026-11-13T10:01:00.000Z",
        "deadlineAt": "2026-11-13T10:30:00.000Z",
        "result": None,
    }
    run.update(extra)
    return run


def make_items(*runs, label="", attempts=None):
    return {
        "total": len(runs),
        "limit": 500,
        "offset": 0,
        "items": [
            {
                "ordinal": i,
                "entry": 0,
                "label": label,
                "scenarioPath": r["scenarioPath"],
                "scenarioName": r["scenarioName"],
                "kind": "simulation",
                "attempt": (attempts or [1] * len(runs))[i],
                "evalRunId": r["id"],
            }
            for i, r in enumerate(runs)
        ],
    }


def make_bundle(name="calls", number=2, bundle_id=BUNDLE_ID, revision_id=REVISION_ID):
    return {
        "id": bundle_id,
        "name": name,
        "createdAt": "2026-11-13T09:00:00.000Z",
        "updatedAt": "2026-11-13T09:30:00.000Z",
        "deletedAt": None,
        "latestRevision": {
            "id": revision_id,
            "bundleId": bundle_id,
            "number": number,
            "digest": "sha256+jcs-v1:" + "0" * 64,
            "coverage": "structural",
            "createdAt": "2026-11-13T09:30:00.000Z",
        },
    }


def make_revision(number=1, revision_id=REVISION_ID, files=None):
    return {
        "id": revision_id,
        "bundleId": BUNDLE_ID,
        "number": number,
        "digest": "sha256+jcs-v1:" + "0" * 64,
        "coverage": "structural",
        "createdAt": "2026-11-13T09:30:00.000Z",
        "bundleDeletedAt": None,
        "catalog": {
            "suites": [
                {
                    "path": "smoke.yaml",
                    "concurrency": 2,
                    "entries": [{"label": "a"}, {"label": "b"}],
                }
            ],
            "scenarioFiles": {"calls.yaml": [{"name": "book", "kind": "simulation", "runs": 3}]},
            "fragments": ["turns/greeting.yaml"],
            "bodies": {"bodies/fast.json": {"model": "fast"}},
        },
        "checker": {
            "region": "us-west",
            "harnessVersion": "1.16.0",
            "ignored": [{"file": "calls.yaml", "scenario": "book", "key": "user.voice"}],
            "ignoredTotal": 3,
        },
        "files": files if files is not None else {"calls.yaml": SCENARIOS},
    }


@pytest.fixture
def api():
    """A stand-in for the CLI's API client; each test sets the answers it needs."""
    with patch("pipecatcloud.cli.commands.evals.API") as mock_api:
        mock_api.bubble_error.return_value = mock_api
        mock_api.error_status = None
        mock_api.error_retry_after = None
        mock_api.no_response.return_value = False
        mock_api.eval_capabilities = AsyncMock(
            return_value=({"enabled": True, "bundles": True, "suites": True}, None)
        )
        mock_api.eval_suite_run_create = AsyncMock(return_value=(make_suite(), None))
        mock_api.eval_suite_run_get = AsyncMock(return_value=(ended(), None))
        mock_api.eval_suite_runs_list = AsyncMock(
            return_value=({"total": 0, "limit": 10, "offset": 0, "suiteRuns": []}, None)
        )
        mock_api.eval_suite_run_items = AsyncMock(return_value=(make_items(), None))
        mock_api.eval_suite_run_cancel = AsyncMock(
            return_value=(ended(verdict=None, status="cancelled"), None)
        )
        mock_api.eval_runs_list = AsyncMock(
            return_value=({"total_count": 2, "runs": [make_run(), make_run(RUN_B)]}, None)
        )
        mock_api.eval_run_get = AsyncMock(return_value=(make_run(), None))
        mock_api.eval_suite_member_runs = AsyncMock(
            return_value=({"total_count": 2, "runs": [make_run(), make_run(RUN_B)]}, None)
        )
        mock_api.eval_org_run_get = AsyncMock(return_value=(make_run(), None))
        mock_api.eval_run_create = AsyncMock(return_value=(make_run(status="queued"), None))
        mock_api.eval_bundles_list = AsyncMock(
            return_value=({"total_count": 1, "bundles": [make_bundle()]}, None)
        )
        mock_api.eval_bundle_revisions_list = AsyncMock(
            return_value=({"total_count": 0, "revisions": []}, None)
        )
        mock_api.eval_bundle_revision_get = AsyncMock(return_value=(make_revision(2), None))
        mock_api.eval_bundle_create = AsyncMock(
            return_value=({"bundle": make_bundle(number=1), "revision": make_revision(1)}, None)
        )
        mock_api.eval_bundle_revision_append = AsyncMock(return_value=(make_revision(3), None))
        yield mock_api


@pytest.fixture(autouse=True)
def clock():
    state = {"now": 0.0}
    with patch("pipecatcloud.cli.commands.evals._monotonic", side_effect=lambda: state["now"]):
        yield state


@pytest.fixture
def sleeps():
    with patch("pipecatcloud.cli.commands.evals.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        yield mock_sleep


@pytest.fixture
def scenario_file(tmp_path):
    path = tmp_path / "calls.yaml"
    path.write_text(SCENARIOS, encoding="utf-8")
    return path


def invoke(*args):
    return runner.invoke(entrypoint_cli_typer, ["eval", *map(str, args)])


def created_body(api) -> dict:
    return api.eval_suite_run_create.call_args.args[2]


# ---- `eval run` as a suite run ----


class TestRunAsSuite:
    def test_with_suites_the_file_runs_as_one_suite_run(self, api, sleeps, scenario_file):
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 0, result.output
        api.eval_run_create.assert_not_called()
        body = created_body(api)
        assert body["files"] == {"calls.yaml": SCENARIOS}
        assert body["scenarioPath"] == "calls.yaml"
        # Every scenario: the server reads the file.
        assert "scenarioNames" not in body
        UUID(body["requestId"])
        assert "Verdict: passed" in result.output

    def test_scenario_options_become_its_scenario_names(self, api, sleeps, scenario_file):
        invoke("run", "agent", scenario_file, "--scenario", "hours", "--scenario", "book")
        assert created_body(api)["scenarioNames"] == ["hours", "book"]

    def test_repeat_concurrency_and_deadline_are_the_suite_runs(self, api, sleeps, scenario_file):
        invoke(
            "run",
            "agent",
            scenario_file,
            "--repeat",
            "3",
            "--concurrency",
            "2",
            "--deadline",
            "600",
        )
        body = created_body(api)
        assert (body["repeat"], body["concurrency"], body["deadlineSeconds"]) == (3, 2, 600)

    def test_param_runs_the_scenarios_side_by_side(self, api, scenario_file):
        result = invoke(
            "run", "agent", scenario_file, "--param", "connectTimeoutSeconds=30", "--no-wait"
        )
        assert result.exit_code == 0, result.output
        api.eval_suite_run_create.assert_not_called()
        assert api.eval_run_create.await_count == 2

    def test_param_with_repeat_is_refused(self, api, scenario_file):
        result = invoke(
            "run", "agent", scenario_file, "--param", "connectTimeoutSeconds=30", "--repeat", "2"
        )
        assert result.exit_code == 3
        api.eval_suite_run_create.assert_not_called()
        api.eval_run_create.assert_not_called()

    def test_suites_without_bundles_runs_side_by_side(self, api, scenario_file):
        # A suite run of a file's scenarios has them checked as a bundle's are.
        api.eval_capabilities.return_value = (
            {"enabled": True, "suites": True, "bundles": False},
            None,
        )
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        api.eval_suite_run_create.assert_not_called()
        assert api.eval_run_create.await_count == 2

    def test_the_header_leaves_out_criteria_only_entries(self, api, sleeps, tmp_path):
        path = tmp_path / "calls.yaml"
        path.write_text(SCENARIOS + "  - name: rubric\n    success: Polite.\n", encoding="utf-8")
        result = invoke("run", "agent", path)
        assert "calls/book" in result.output
        assert "calls/rubric" not in result.output

    def test_criteria_only_entries_are_left_out_side_by_side_too(self, api, tmp_path):
        api.eval_capabilities.return_value = ({"enabled": True, "suites": False}, None)
        path = tmp_path / "calls.yaml"
        path.write_text(SCENARIOS + "  - name: rubric\n    success: Polite.\n", encoding="utf-8")
        result = invoke("run", "agent", path, "--no-wait")
        assert result.exit_code == 0, result.output
        sent = [c.args[2]["scenarioName"] for c in api.eval_run_create.call_args_list]
        assert sent == ["book", "hours"]

    def test_a_file_of_criteria_alone_has_nothing_to_run(self, api, tmp_path):
        path = tmp_path / "criteria.yaml"
        path.write_text("name: c\nscenarios:\n  - name: rubric\n    success: Polite.\n")
        result = invoke("run", "agent", path)
        assert result.exit_code == 3
        assert "eval judge" in result.output
        api.eval_suite_run_create.assert_not_called()

    def test_without_suites_it_runs_side_by_side(self, api, scenario_file):
        api.eval_capabilities.return_value = ({"enabled": True, "suites": False}, None)
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        api.eval_suite_run_create.assert_not_called()
        assert api.eval_run_create.await_count == 2

    def test_an_api_without_the_capabilities_route_runs_side_by_side(self, api, scenario_file):
        api.eval_capabilities.return_value = (None, {"error": "Not found", "code": "404"})
        api.error_status = 404
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        assert api.eval_run_create.await_count == 2

    def test_capabilities_that_cant_be_read_exit_three(self, api, scenario_file):
        api.eval_capabilities.return_value = (None, {"error": "boom", "code": "500"})
        api.error_status = 500
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        api.eval_suite_run_create.assert_not_called()
        api.eval_run_create.assert_not_called()

    def test_repeat_without_suites_is_refused(self, api, scenario_file):
        api.eval_capabilities.return_value = ({"enabled": True, "suites": False}, None)
        result = invoke("run", "agent", scenario_file, "--repeat", "2")
        assert result.exit_code == 3
        assert "doesn't have yet" in result.output
        api.eval_run_create.assert_not_called()

    def test_no_wait_shows_it_accepted_and_exits_zero(self, api, sleeps, scenario_file):
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        assert SUITE_ID in result.output
        assert f"eval suite status {SUITE_ID} --wait" in result.output
        api.eval_suite_run_get.assert_not_called()

    def test_json_without_wait_is_the_suite_run(self, api, scenario_file):
        result = invoke("run", "agent", scenario_file, "--no-wait", "--json")
        assert json.loads(result.stdout)["id"] == SUITE_ID


# ---- Creating ----


class TestCreate:
    def test_no_answer_is_sent_again_with_the_same_request_id(self, api, sleeps, scenario_file):
        answers = [(None, {"error": "dropped", "code": "CLI"}), (make_suite(), None)]
        api.eval_suite_run_create.side_effect = answers
        api.no_response.side_effect = [True]
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        first, second = (c.args[2] for c in api.eval_suite_run_create.call_args_list)
        assert first == second

    def test_a_503_with_retry_after_is_sent_again(self, api, sleeps, scenario_file):
        api.eval_suite_run_create.side_effect = [
            (None, {"error": "busy", "code": "503"}),
            (make_suite(), None),
        ]
        api.error_status = 503
        api.error_retry_after = "5"
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        sleeps.assert_any_await(5)

    def test_a_refusal_is_not_sent_again(self, api, scenario_file):
        api.eval_suite_run_create.return_value = (None, {"error": "bad", "code": "EVAL-X"})
        api.error_status = 409
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert api.eval_suite_run_create.await_count == 1

    def test_a_503_without_retry_after_is_not_sent_again(self, api, scenario_file):
        api.eval_suite_run_create.return_value = (None, {"error": "down", "code": "503"})
        api.error_status = 503
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert api.eval_suite_run_create.await_count == 1

    @pytest.mark.parametrize("status", [502, 504])
    def test_a_gateway_failure_is_sent_again(self, api, sleeps, scenario_file, status):
        api.eval_suite_run_create.side_effect = [
            (None, {"error": "gateway", "code": str(status)}),
            (make_suite(), None),
        ]
        api.error_status = status
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        assert api.eval_suite_run_create.await_count == 2

    @pytest.mark.parametrize("code", ["EVAL-SUITES-NOT-AVAILABLE", "EVAL-BUNDLES-NOT-AVAILABLE"])
    def test_an_agent_that_cant_take_a_suite_run_runs_side_by_side(self, api, scenario_file, code):
        # The organization has suite runs, but not the agent's region, say.
        api.eval_suite_run_create.return_value = (None, {"error": "Not here.", "code": code})
        api.error_status = 503
        result = invoke("run", "agent", scenario_file, "--no-wait")
        assert result.exit_code == 0, result.output
        assert api.eval_suite_run_create.await_count == 1
        assert api.eval_run_create.await_count == 2
        assert "This agent can't take a suite run here" in result.output
        assert "this organization" not in result.output

    def test_with_suite_only_options_that_refusal_is_an_error(self, api, scenario_file):
        api.eval_suite_run_create.return_value = (
            None,
            {"error": "Not here.", "code": "EVAL-SUITES-NOT-AVAILABLE"},
        )
        api.error_status = 503
        result = invoke("run", "agent", scenario_file, "--repeat", "2")
        assert result.exit_code == 3
        api.eval_run_create.assert_not_called()

    def test_scenarios_refused_list_their_problems(self, api, scenario_file):
        problems = [{"file": "calls.yaml", "scenario": "book", "code": "x", "message": "no voice"}]
        api.eval_suite_run_create.return_value = (
            None,
            {"error": "refused", "code": "EVAL-SUITE-SCENARIOS-REFUSED", "problems": problems},
        )
        api.error_status = 400
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert "its scenarios have problems" in result.output
        assert "calls.yaml book no voice" in result.output

    def test_a_plan_refusal_says_what_and_why(self, api, scenario_file):
        api.eval_suite_run_create.return_value = (
            None,
            {
                "error": "The suite run can't be planned.",
                "code": "EVAL-SUITE-PLAN-INVALID",
                "refusal": {"kind": "repeat_too_large", "repeat": 99, "max": 50},
            },
        )
        api.error_status = 400
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert "can't be planned" in result.output
        assert "repeat_too_large" in result.output

    def test_refusals_in_json_are_the_error_object(self, api, scenario_file):
        refusal = {
            "error": "x",
            "code": "EVAL-SUITE-PLAN-INVALID",
            "refusal": {"kind": "empty_plan"},
        }
        api.eval_suite_run_create.return_value = (None, refusal)
        api.error_status = 400
        result = invoke("run", "agent", scenario_file, "--json")
        assert result.exit_code == 3
        assert json.loads(result.stdout) == {"error": refusal}

    def test_no_answer_at_the_last_try_says_one_may_exist(self, api, sleeps, scenario_file):
        api.eval_suite_run_create.return_value = (None, {"error": "dropped", "code": "CLI"})
        api.no_response.return_value = True
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert "may have started" in result.output
        assert "eval suite list agent" in result.output

    def test_it_gives_up_after_three_tries(self, api, sleeps, scenario_file):
        api.eval_suite_run_create.return_value = (None, {"error": "dropped", "code": "CLI"})
        api.no_response.return_value = True
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert api.eval_suite_run_create.await_count == eval_suites.CREATE_ATTEMPTS


# ---- Waiting, and the exit codes ----


class TestWaitForSuite:
    def test_it_polls_until_the_suite_run_ends(self, api, sleeps, scenario_file):
        api.eval_suite_run_get.side_effect = [
            (make_suite(status="running", counts=counts(pending=1, live=1)), None),
            (ended(), None),
        ]
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 0, result.output
        assert api.eval_suite_run_get.await_count == 2

    @pytest.mark.parametrize(
        ("suite", "code"),
        [
            (ended("passed"), 0),
            (ended("skipped"), 0),
            (ended("failed"), 1),
            (ended(None), 2),
            (ended(None, measurement=True, counts=counts(failed=2)), 0),
            (ended(None, status="cancelled", counts=counts(without=2)), 2),
            (ended(None, status="expired", counts=counts(without=2)), 2),
            (ended(None, status="error", counts=counts(without=2)), 2),
            (ended(None, status="cancelled", measurement=True, counts=counts(without=2)), 2),
        ],
    )
    def test_the_exit_code_is_the_contracts(self, api, sleeps, scenario_file, suite, code):
        api.eval_suite_run_get.return_value = (suite, None)
        api.eval_suite_member_runs.return_value = ({"total_count": 0, "runs": []}, None)
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == code, result.output

    def test_suite_exit_code_directly(self):
        assert eval_suites.suite_exit_code(ended("passed")) == 0
        assert eval_suites.suite_exit_code(ended("failed")) == 1
        assert eval_suites.suite_exit_code(ended(None, measurement=True)) == 0
        assert eval_suites.suite_exit_code(ended("passed", status="cancelled")) == 2

    def test_json_wait_prints_the_final_suite_run_alone(self, api, sleeps, scenario_file):
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        result = invoke("run", "agent", scenario_file, "--json")
        assert result.exit_code == 1
        assert json.loads(result.stdout)["verdict"] == "failed"

    def test_a_transient_poll_failure_is_retried(self, api, sleeps, scenario_file):
        api.eval_suite_run_get.side_effect = [
            (None, {"error": "x", "code": "502"}),
            (ended(), None),
        ]
        api.error_status = 502
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 0, result.output

    def test_a_permanent_poll_failure_exits_three(self, api, sleeps, scenario_file):
        api.eval_suite_run_get.return_value = (None, {"error": "gone", "code": "404"})
        api.error_status = 404
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3

    def test_a_part_of_evals_switched_off_ends_the_wait_at_once(self, api, sleeps, scenario_file):
        api.eval_suite_run_get.return_value = (
            None,
            {"error": "off", "code": "EVAL-SUITES-NOT-AVAILABLE"},
        )
        api.error_status = 503
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert api.eval_suite_run_get.await_count == 1

    def test_it_stops_waiting_well_past_the_deadline(self, api, sleeps, clock, scenario_file):
        live = make_suite(status="running", counts=counts(live=2))

        def still_live(*_):
            clock["now"] += 60 * 60
            return live, None

        api.eval_suite_run_create.return_value = (live, None)
        api.eval_suite_run_get.side_effect = still_live
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 3
        assert "well past its deadline" in result.output

    def test_ctrl_c_leaves_it_going_and_exits_130(self, api, sleeps, scenario_file):
        sleeps.side_effect = KeyboardInterrupt
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 130
        assert f"eval suite cancel {SUITE_ID}" in result.output
        api.eval_suite_run_cancel.assert_not_called()

    def test_ctrl_c_while_starting_says_one_may_exist(self, api, scenario_file):
        api.eval_suite_run_create.side_effect = KeyboardInterrupt
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 130
        assert "eval suite list agent" in result.output


# ---- What's shown once it ends ----


class TestReport:
    def test_failures_are_explained_with_their_entry_and_attempt(self, api, sleeps, scenario_file):
        failed = make_run(RUN_B, verdict="failed", name="hours", result={"reason": "No hours."})
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        api.eval_suite_member_runs.return_value = (
            {"total_count": 2, "runs": [make_run(), failed]},
            None,
        )
        api.eval_suite_run_items.return_value = (
            make_items(make_run(), failed, label="fast", attempts=[1, 2]),
            None,
        )
        api.eval_org_run_get.return_value = (failed, None)
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 1
        assert "Failures (1 of 2)" in result.output
        assert "fast: calls/hours #2" in result.output
        assert "No hours." in result.output
        api.eval_org_run_get.assert_awaited_once()
        assert api.eval_org_run_get.call_args.args[1] == RUN_B

    def test_only_so_many_failures_are_explained(self, api, sleeps, scenario_file):
        runs = [make_run(f"run-{i}", verdict="failed") for i in range(25)]
        api.eval_suite_run_get.return_value = (
            ended("failed", counts=counts(planned=25, failed=25)),
            None,
        )
        api.eval_suite_member_runs.return_value = ({"total_count": 25, "runs": runs}, None)
        api.eval_org_run_get.return_value = (runs[0], None)
        result = invoke("run", "agent", scenario_file)
        assert api.eval_org_run_get.await_count == eval_suites.MAX_EXPLAINED_MEMBERS
        assert "And 5 more." in result.output

    def test_member_runs_are_read_page_by_page(self, api, sleeps, scenario_file):
        api.eval_suite_member_runs.side_effect = [
            ({"total_count": 3, "runs": [make_run(), make_run(RUN_B)]}, None),
            ({"total_count": 3, "runs": [make_run("run-3")]}, None),
        ]
        invoke("run", "agent", scenario_file)
        offsets = [c.kwargs["offset"] for c in api.eval_suite_member_runs.call_args_list]
        assert offsets == [0, 2]
        assert all(c.args[1] == SUITE_ID for c in api.eval_suite_member_runs.call_args_list)

    def test_a_measurement_shows_pass_rates_not_failures(self, api, sleeps, scenario_file):
        rates = [
            {
                "entry": 0,
                "label": "",
                "scenarioPath": "calls.yaml",
                "scenarioName": "book",
                "fingerprint": FINGERPRINT,
                "passed": 3,
                "failed": 1,
                "skipped": 0,
                "withoutVerdict": 0,
                "passRate": 0.75,
            }
        ]
        api.eval_suite_run_get.return_value = (
            ended(None, measurement=True, passRates=rates, counts=counts(4, passed=3, failed=1)),
            None,
        )
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 0
        assert "75%" in result.output
        assert "Failures" not in result.output
        api.eval_org_run_get.assert_not_called()

    def test_mixed_is_a_warning_and_pass_rates_name_their_version(self, api, sleeps, scenario_file):
        rates = [
            {
                "entry": 0,
                "label": "",
                "scenarioPath": "calls.yaml",
                "scenarioName": "book",
                "fingerprint": fp,
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "withoutVerdict": 0,
                "passRate": 1,
            }
            for fp in (FINGERPRINT, OTHER_FINGERPRINT)
        ]
        api.eval_suite_run_get.return_value = (
            ended(None, measurement=True, mixed=True, passRates=rates),
            None,
        )
        result = invoke("run", "agent", scenario_file)
        assert "Mixed judging versions" in result.output
        assert "pass rates are split by version" in result.output
        assert "Judging version" in result.output
        assert OTHER_FINGERPRINT in result.output

    def test_an_error_says_what_ended_it(self, api, sleeps, scenario_file):
        error = {
            "code": "deployment_changed",
            "message": "The agent's version changed during the suite run.",
            "expectedDeploymentId": "d1",
            "deploymentId": "d2",
            "memberOrdinal": None,
        }
        api.eval_suite_run_get.return_value = (
            ended(None, status="error", error=error, counts=counts(without=2)),
            None,
        )
        api.eval_suite_member_runs.return_value = ({"total_count": 0, "runs": []}, None)
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 2
        assert "The agent's version changed" in result.output
        assert "deployment_changed" in result.output
        assert "2 of the members never started" in result.output

    def test_members_that_cant_be_read_keep_the_verdicts_exit_code(
        self, api, sleeps, scenario_file
    ):
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        api.eval_suite_member_runs.return_value = (None, {"error": "Bad gateway", "code": "502"})
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 1
        assert "Couldn't read the suite run's members: Bad gateway" in result.output

    def test_a_result_that_cant_be_read_is_said_and_the_exit_code_kept(
        self, api, sleeps, scenario_file
    ):
        failed = make_run(RUN_B, verdict="failed")
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        api.eval_suite_member_runs.return_value = ({"total_count": 1, "runs": [failed]}, None)
        api.eval_suite_run_items.return_value = (None, {"error": "x", "code": "502"})
        api.eval_org_run_get.return_value = (None, {"error": "Gone", "code": "404"})
        result = invoke("run", "agent", scenario_file)
        assert result.exit_code == 1
        assert "Couldn't read the run's result: Gone" in result.output

    def test_members_are_read_through_the_organization(self, api, sleeps, scenario_file):
        # Not by the agent's name: the agent may be gone, or its name reused.
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        api.eval_suite_member_runs.return_value = (
            {"total_count": 1, "runs": [make_run(verdict="failed")]},
            None,
        )
        invoke("run", "agent", scenario_file)
        api.eval_suite_member_runs.assert_awaited()
        api.eval_org_run_get.assert_awaited_once()
        api.eval_runs_list.assert_not_called()
        api.eval_run_get.assert_not_called()

    def test_verbose_explains_every_member(self, api, sleeps, scenario_file):
        invoke("run", "agent", scenario_file, "-v")
        assert api.eval_org_run_get.await_count == 2


# ---- `eval suite run` ----


class TestSuiteRun:
    def test_bundle_runs_its_latest_revision(self, api, sleeps):
        result = invoke("suite", "run", "agent", "--bundle", "calls", "--suite", "smoke.yaml")
        assert result.exit_code == 0, result.output
        body = created_body(api)
        assert body["bundleRevisionId"] == REVISION_ID
        assert body["suitePath"] == "smoke.yaml"
        assert "files" not in body
        api.eval_bundle_revisions_list.assert_not_called()

    def test_bundle_at_n_finds_that_revision(self, api, sleeps):
        older = dict(make_bundle()["latestRevision"], id="rev-1", number=1)
        api.eval_bundle_revisions_list.side_effect = [
            ({"total_count": 2, "revisions": [make_bundle()["latestRevision"]]}, None),
            ({"total_count": 2, "revisions": [older]}, None),
        ]
        invoke("suite", "run", "agent", "--bundle", "calls@1", "--suite", "smoke.yaml")
        assert created_body(api)["bundleRevisionId"] == "rev-1"

    def test_a_revision_that_isnt_there_is_refused(self, api):
        api.eval_bundle_revisions_list.return_value = (
            {"total_count": 1, "revisions": [make_bundle()["latestRevision"]]},
            None,
        )
        result = invoke("suite", "run", "agent", "--bundle", "calls@7", "--suite", "s.yaml")
        assert result.exit_code == 3
        assert "has no revision 7" in result.output
        api.eval_suite_run_create.assert_not_called()

    def test_a_bundle_that_isnt_there_is_refused(self, api):
        result = invoke("suite", "run", "agent", "--bundle", "other", "--suite", "s.yaml")
        assert result.exit_code == 3
        assert "no bundle named 'other'" in result.output

    def test_bundles_are_found_page_by_page(self, api, sleeps):
        api.eval_bundles_list.side_effect = [
            ({"total_count": 2, "bundles": [make_bundle("first", bundle_id="b1")]}, None),
            ({"total_count": 2, "bundles": [make_bundle()]}, None),
        ]
        result = invoke("suite", "run", "agent", "--bundle", "calls", "--suite", "s.yaml")
        assert result.exit_code == 0, result.output
        assert [c.kwargs["offset"] for c in api.eval_bundles_list.call_args_list] == [0, 1]

    def test_revision_names_one_by_its_id(self, api, sleeps):
        invoke("suite", "run", "agent", "--revision", "rev-x", "--suite", "s.yaml")
        assert created_body(api)["bundleRevisionId"] == "rev-x"
        api.eval_bundles_list.assert_not_called()

    @pytest.mark.parametrize(
        "args",
        [
            ["--suite", "s.yaml"],
            ["--suite", "s.yaml", "--bundle", "calls", "--revision", "r"],
        ],
    )
    def test_one_of_bundle_and_revision_is_required(self, api, args):
        result = invoke("suite", "run", "agent", *args)
        assert result.exit_code == 3
        api.eval_suite_run_create.assert_not_called()

    @pytest.mark.parametrize("ref", ["Calls", "calls@0", "calls@x", "-calls", "calls@"])
    def test_a_bad_bundle_reference_is_refused_before_any_request(self, api, ref):
        result = invoke("suite", "run", "agent", "--bundle", ref, "--suite", "s.yaml")
        assert result.exit_code == 3
        api.eval_bundles_list.assert_not_called()

    def test_options_reach_the_request(self, api, sleeps):
        invoke(
            "suite", "run", "agent", "--revision", "r", "--suite", "s.yaml",
            "--repeat", "5", "--concurrency", "3", "--deadline", "900",
        )  # fmt: skip
        body = created_body(api)
        assert (body["repeat"], body["concurrency"], body["deadlineSeconds"]) == (5, 3, 900)


# ---- `eval suite status`, `list` and `cancel` ----


class TestSuiteStatus:
    def test_a_live_suite_run_shows_its_counts_and_how_to_wait(self, api):
        api.eval_suite_run_get.return_value = (
            make_suite(status="running", counts=counts(pending=1, live=1)),
            None,
        )
        result = invoke("suite", "status", SUITE_ID)
        assert result.exit_code == 0
        assert "1 live" in result.output
        assert f"eval suite status {SUITE_ID} --wait" in result.output
        api.eval_suite_member_runs.assert_not_called()

    def test_it_shows_the_judging_version(self, api):
        api.eval_suite_run_get.return_value = (ended(fingerprint=FINGERPRINT), None)
        result = invoke("suite", "status", SUITE_ID)
        assert "Judging version" in result.output
        assert FINGERPRINT in result.output

    def test_without_wait_an_ended_failure_still_exits_zero(self, api):
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        result = invoke("suite", "status", SUITE_ID)
        assert result.exit_code == 0

    def test_wait_exits_with_the_verdict(self, api, sleeps):
        api.eval_suite_run_get.side_effect = [
            (make_suite(status="running"), None),
            (ended("failed"), None),
        ]
        result = invoke("suite", "status", SUITE_ID, "--wait")
        assert result.exit_code == 1

    def test_wait_on_an_ended_suite_run_shows_its_counts_once(self, api):
        api.eval_suite_run_get.return_value = (ended("failed"), None)
        result = invoke("suite", "status", SUITE_ID, "--wait")
        assert result.exit_code == 1
        assert result.output.count("2/2 done") == 1
        api.eval_suite_run_get.assert_awaited_once()

    def test_json(self, api):
        result = invoke("suite", "status", SUITE_ID, "--json")
        assert json.loads(result.stdout)["id"] == SUITE_ID

    def test_suites_off_says_so_and_exits_three(self, api):
        api.eval_suite_run_get.return_value = (
            None,
            {"error": "Suite runs aren't available for this organization yet.", "code": "x"},
        )
        result = invoke("suite", "status", SUITE_ID, "--json")
        assert result.exit_code == 3
        assert "aren't available" in result.stdout


class TestSuiteList:
    def test_one_agents_suite_runs_as_a_table(self, api):
        api.eval_suite_runs_list.return_value = (
            {"total": 3, "limit": 1, "offset": 0, "suiteRuns": [ended("failed")]},
            None,
        )
        result = invoke("suite", "list", "agent", "-n", "1")
        assert result.exit_code == 0
        args, kwargs = api.eval_suite_runs_list.call_args
        assert args[1] == "agent"
        assert kwargs == {"limit": 1}
        assert SUITE_ID in result.output
        assert "--offset 1" in result.output

    def test_the_organizations_without_an_agent(self, api):
        invoke("suite", "list")
        assert api.eval_suite_runs_list.call_args.args[1] is None

    def test_none(self, api):
        result = invoke("suite", "list")
        assert "No suite runs" in result.output


class TestSuiteCancel:
    def test_a_cancelled_suite_run(self, api):
        result = invoke("suite", "cancel", SUITE_ID)
        assert result.exit_code == 0
        assert "is cancelled" in result.output

    def test_a_cancel_still_being_carried_out(self, api):
        api.eval_suite_run_cancel.return_value = (
            make_suite(status="running", cancelRequestedAt="2026-11-13T10:01:00.000Z"),
            None,
        )
        result = invoke("suite", "cancel", SUITE_ID)
        assert result.exit_code == 0
        assert "Cancellation requested" in result.output

    def test_one_that_already_ended_says_how_and_exits_three(self, api):
        api.eval_suite_run_cancel.return_value = (None, {"error": "ended", "code": "409"})
        api.error_status = 409
        api.eval_suite_run_get.return_value = (ended("passed"), None)
        result = invoke("suite", "cancel", SUITE_ID)
        assert result.exit_code == 3
        assert "already ended: completed, verdict passed" in result.output

    def test_one_that_already_ended_in_json(self, api):
        api.eval_suite_run_cancel.return_value = (None, {"error": "ended", "code": "409"})
        api.error_status = 409
        api.eval_suite_run_get.return_value = (ended("passed"), None)
        result = invoke("suite", "cancel", SUITE_ID, "--json")
        assert result.exit_code == 3
        out = json.loads(result.stdout)
        assert out["error"]["code"] == "409"
        assert out["suiteRun"]["status"] == "completed"

    def test_another_error_exits_three(self, api):
        api.eval_suite_run_cancel.return_value = (None, {"error": "nope", "code": "404"})
        api.error_status = 404
        result = invoke("suite", "cancel", SUITE_ID)
        assert result.exit_code == 3
        api.eval_suite_run_get.assert_not_called()


class TestListBySuite:
    def test_suite_filters_the_runs(self, api):
        invoke("list", "agent", "--suite", SUITE_ID)
        assert api.eval_runs_list.call_args.kwargs["suite_run_id"] == SUITE_ID

    def test_without_it_there_is_no_filter(self, api):
        invoke("list", "agent")
        assert "suite_run_id" not in api.eval_runs_list.call_args.kwargs


# ---- Bundles: collecting a directory ----


class TestCollectDirectory:
    def test_yaml_and_json_files_keyed_by_their_path(self, tmp_path):
        (tmp_path / "suites").mkdir()
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / "suites" / "smoke.yml").write_text("kind: suite\n")
        (tmp_path / "body.JSON").write_text("{}")
        (tmp_path / "README.md").write_text("hi")
        found = collect_directory(tmp_path)
        assert sorted(found.files) == ["body.JSON", "calls.yaml", "suites/smoke.yml"]
        assert found.other_files == 1

    def test_hidden_files_and_folders_are_left_out_and_listed(self, tmp_path):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "x.yaml").write_text("x: 1\n")
        (tmp_path / ".hidden.yaml").write_text("x: 1\n")
        found = collect_directory(tmp_path)
        assert list(found.files) == ["calls.yaml"]
        assert (".git/", "is a hidden folder") in found.skipped
        assert any(key == ".hidden.yaml" for key, _ in found.skipped)

    def test_a_file_named_like_a_secret_is_left_out(self, tmp_path):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / "id_rsa.yaml").write_text("x: 1\n")
        found = collect_directory(tmp_path)
        assert list(found.files) == ["calls.yaml"]
        assert found.skipped == [("id_rsa.yaml", "looks like it holds secrets")]

    @pytest.mark.parametrize(
        "name",
        [
            "credentials.json",
            "client_secret_123.apps.googleusercontent.com.json",
            "db-secret.yaml",
            "my-service-account.json",
            "kubeconfig.yaml",
            "token.json",
            "values.sops.yaml",
        ],
    )
    def test_names_credentials_are_kept_under_are_left_out(self, tmp_path, name):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / name).write_text("x: 1\n")
        found = collect_directory(tmp_path)
        assert list(found.files) == ["calls.yaml"]
        assert found.skipped == [(f"sub/{name}", "is named like a credential")]

    @pytest.mark.parametrize(
        ("text", "why"),
        [
            ('{"private_key": "-----BEGIN PRIVATE KEY-----\\nabc"}', "holds a private key"),
            ("apiVersion: v1\nkind: Secret\ndata:\n  a: b\n", "is a Kubernetes Secret"),
        ],
    )
    def test_content_that_looks_like_a_credential_is_left_out(self, tmp_path, text, why):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / "other.json").write_text(text)
        found = collect_directory(tmp_path)
        assert list(found.files) == ["calls.yaml"]
        assert found.skipped == [("other.json", why)]

    @pytest.mark.parametrize("folder", ["secrets", "credentials", "service-account-keys"])
    def test_folders_named_for_credentials_are_left_out(self, tmp_path, folder):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "prod.yaml").write_text("password: hunter2\n")
        found = collect_directory(tmp_path)
        assert list(found.files) == ["calls.yaml"]
        assert found.skipped == [(f"{folder}/", "is named like it holds credentials")]

    @pytest.mark.parametrize(
        "text",
        [
            'apiVersion: v1\nkind: "Secret"\n',
            "apiVersion: v1\nkind: 'Secret'\n",
            '{"apiVersion":"v1","kind":"Secret","data":{}}',
            "apiVersion: v1\nkind: Secret  # db\n",
            "apiVersion: v1\nkind: List\nitems:\n  - kind: Secret\n    data: {}\n",
        ],
    )
    def test_a_quoted_kubernetes_secret_is_left_out(self, tmp_path, text):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        (tmp_path / "db.json").write_text(text)
        found = collect_directory(tmp_path)
        assert ("db.json", "is a Kubernetes Secret") in found.skipped

    def test_nothing_left_to_send_is_refused(self, tmp_path):
        (tmp_path / "key.json").write_text("-----BEGIN RSA PRIVATE KEY-----")
        with pytest.raises(BundleError, match="nothing to send"):
            collect_directory(tmp_path)

    def test_a_folder_that_cant_be_read_is_listed(self, tmp_path):
        (tmp_path / "calls.yaml").write_text("name: c\n")
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "x.yaml").write_text("x: 1\n")
        locked.chmod(0)
        try:
            found = collect_directory(tmp_path)
        finally:
            locked.chmod(0o755)
        assert list(found.files) == ["calls.yaml"]
        assert ("locked/", "can't be read") in found.skipped

    def test_a_symlink_leaving_the_directory_is_refused(self, tmp_path):
        outside = tmp_path / "outside.yaml"
        outside.write_text("x: 1\n")
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "calls.yaml").symlink_to(outside)
        with pytest.raises(BundleError, match="leaves the root"):
            collect_directory(root)

    def test_a_linked_folder_is_not_followed(self, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        (target / "x.yaml").write_text("x: 1\n")
        root = tmp_path / "bundle"
        root.mkdir()
        (root / "calls.yaml").write_text("name: c\n")
        os.symlink(target, root / "linked")
        found = collect_directory(root)
        assert list(found.files) == ["calls.yaml"]
        assert any(key == "linked/" for key, _ in found.skipped)

    def test_a_file_that_isnt_utf8_is_refused(self, tmp_path):
        (tmp_path / "calls.yaml").write_bytes(b"\xff\xfe")
        with pytest.raises(BundleError, match="UTF-8"):
            collect_directory(tmp_path)

    def test_files_past_the_size_limit_are_refused(self, tmp_path):
        half = "x" * (MAX_BUNDLE_BYTES // 2 + 1)
        (tmp_path / "a.yaml").write_text(half)
        (tmp_path / "b.yaml").write_text(half)
        with pytest.raises(BundleError, match="most a bundle can hold"):
            collect_directory(tmp_path)

    def test_a_directory_without_files_to_send_is_refused(self, tmp_path):
        (tmp_path / "README.md").write_text("hi")
        with pytest.raises(BundleError, match="no .yaml"):
            collect_directory(tmp_path)

    def test_a_missing_directory_is_refused(self, tmp_path):
        with pytest.raises(BundleError, match="isn't a directory"):
            collect_directory(tmp_path / "nope")


# ---- `eval bundle push` ----


@pytest.fixture
def bundle_dir(tmp_path):
    (tmp_path / "calls.yaml").write_text(SCENARIOS, encoding="utf-8")
    return tmp_path


class TestBundlePush:
    def test_a_new_name_creates_the_bundle(self, api, bundle_dir):
        api.eval_bundles_list.return_value = ({"total_count": 0, "bundles": []}, None)
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert result.exit_code == 0, result.output
        body = api.eval_bundle_create.call_args.args[1]
        assert body["name"] == "calls"
        assert body["files"] == {"calls.yaml": SCENARIOS}
        UUID(body["requestId"])
        api.eval_bundle_revision_append.assert_not_called()
        assert "Published calls@1" in result.output
        assert "as a new bundle 'calls'" in result.output

    def test_an_existing_name_appends_its_next_revision(self, api, bundle_dir):
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert result.exit_code == 0, result.output
        assert api.eval_bundle_revision_append.call_args.args[1] == BUNDLE_ID
        assert "as revision 3 of 'calls'" in result.output
        assert "Published calls@3" in result.output
        api.eval_bundle_create.assert_not_called()

    def test_a_name_taken_meanwhile_appends_to_that_bundle(self, api, bundle_dir):
        api.eval_bundles_list.side_effect = [
            ({"total_count": 0, "bundles": []}, None),
            ({"total_count": 1, "bundles": [make_bundle()]}, None),
        ]
        api.eval_bundle_create.return_value = (
            None,
            {"error": "taken", "code": "EVAL-BUNDLE-NAME-TAKEN"},
        )
        api.error_status = 409
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert result.exit_code == 0, result.output
        assert api.eval_bundle_revision_append.call_args.args[1] == BUNDLE_ID

    def test_refused_files_list_their_problems_and_exit_three(self, api, bundle_dir):
        problems = [
            {"file": "list.yaml", "code": "orphan_fragment", "message": "list.yaml: an orphan"},
            {"file": "calls.yaml", "scenario": "book", "code": "bad", "message": "no goal"},
        ]
        api.eval_bundle_revision_append.return_value = (
            None,
            {"error": "refused", "code": "EVAL-BUNDLE-REFUSED", "problems": problems, "total": 5},
        )
        api.error_status = 400
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert result.exit_code == 3
        assert "list.yaml: an orphan (orphan_fragment)" in result.output
        assert "list.yaml list.yaml" not in result.output
        assert "calls.yaml book no goal" in result.output
        assert "And 3 more." in result.output
        assert api.eval_bundle_revision_append.await_count == 1

    def test_refused_files_in_json_are_the_error_object(self, api, bundle_dir):
        refusal = {"error": "refused", "code": "EVAL-BUNDLE-REFUSED", "problems": [], "total": 0}
        api.eval_bundle_revision_append.return_value = (None, refusal)
        api.error_status = 400
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes", "--json")
        assert result.exit_code == 3
        assert json.loads(result.stdout) == {"error": refusal}

    def test_no_answer_is_sent_again_with_the_same_request_id(self, api, sleeps, bundle_dir):
        api.eval_bundle_revision_append.side_effect = [
            (None, {"error": "dropped", "code": "CLI"}),
            (make_revision(3), None),
        ]
        api.no_response.side_effect = [True]
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert result.exit_code == 0, result.output
        first, second = (c.args[2] for c in api.eval_bundle_revision_append.call_args_list)
        assert first == second

    def test_without_yes_off_a_terminal_nothing_is_sent(self, api, bundle_dir):
        result = invoke("bundle", "push", "calls", bundle_dir)
        assert result.exit_code == 3
        assert "--yes" in result.output
        api.eval_bundle_revision_append.assert_not_called()

    def test_it_asks_on_a_terminal(self, api, bundle_dir):
        with patch.object(eval_suites, "stdin_is_interactive", return_value=True):
            declined = runner.invoke(
                entrypoint_cli_typer,
                ["eval", "bundle", "push", "calls", str(bundle_dir)],
                input="n\n",
            )
            assert declined.exit_code == 0
            assert "Nothing was published" in declined.output
            api.eval_bundle_revision_append.assert_not_called()
            accepted = runner.invoke(
                entrypoint_cli_typer,
                ["eval", "bundle", "push", "calls", str(bundle_dir)],
                input="y\n",
            )
        assert accepted.exit_code == 0, accepted.output
        api.eval_bundle_revision_append.assert_awaited_once()

    def test_ctrl_c_at_the_prompt_publishes_nothing_and_exits_130(self, api, bundle_dir):
        with (
            patch.object(eval_suites, "stdin_is_interactive", return_value=True),
            patch.object(eval_suites.typer, "confirm", side_effect=KeyboardInterrupt),
        ):
            result = invoke("bundle", "push", "calls", bundle_dir)
        assert result.exit_code == 130
        assert "Nothing was published" in result.output
        api.eval_bundle_revision_append.assert_not_called()

    def test_ctrl_c_while_publishing_says_a_revision_may_exist(self, api, bundle_dir):
        api.eval_bundle_revision_append.side_effect = KeyboardInterrupt
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert result.exit_code == 130
        assert "may have been published" in result.output
        assert "eval bundle show calls" in result.output

    @pytest.mark.parametrize("name", ["Calls", "calls@2", "-x", "a" * 129])
    def test_a_bad_name_is_refused_before_any_request(self, api, bundle_dir, name):
        result = invoke("bundle", "push", name, bundle_dir, "--yes")
        assert result.exit_code == 3
        api.eval_bundles_list.assert_not_called()

    def test_a_directory_that_cant_be_sent_is_refused_before_any_request(self, api, tmp_path):
        result = invoke("bundle", "push", "calls", tmp_path, "--yes")
        assert result.exit_code == 3
        api.eval_bundles_list.assert_not_called()

    def test_the_preview_lists_what_is_left_out(self, api, bundle_dir):
        (bundle_dir / ".env.yaml").write_text("x: 1\n")
        (bundle_dir / "notes.txt").write_text("hi")
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert "Left out: .env.yaml" in result.output
        assert "1 other file is not YAML or JSON" in result.output

    def test_it_shows_the_catalog_and_how_to_run_a_suite(self, api, bundle_dir):
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes")
        assert "smoke.yaml" in result.output
        assert "book (simulation, runs 3)" in result.output
        assert "user.voice" in result.output
        assert "--bundle calls@3 --suite smoke.yaml" in result.output

    def test_json_is_the_revision(self, api, bundle_dir):
        result = invoke("bundle", "push", "calls", bundle_dir, "--yes", "--json")
        assert result.exit_code == 0
        assert json.loads(result.stdout)["number"] == 3


class TestBundleListAndShow:
    def test_list_is_a_table_of_names_and_latest_revisions(self, api):
        result = invoke("bundle", "list")
        assert result.exit_code == 0
        assert "calls" in result.output
        assert BUNDLE_ID in result.output

    def test_show_reads_the_latest_revision_whole(self, api):
        result = invoke("bundle", "show", "calls")
        assert result.exit_code == 0, result.output
        assert api.eval_bundle_revision_get.call_args.args[1] == REVISION_ID
        assert "turns/greeting.yaml" in result.output
        assert "bodies/fast.json" in result.output
        # The files are listed, not printed.
        assert "Book a table." not in result.output

    def test_show_files_prints_them(self, api):
        result = invoke("bundle", "show", "calls", "--files")
        assert "==> calls.yaml <==" in result.output
        assert "Book a table." in result.output

    def test_show_files_prints_them_as_stored(self, api):
        text = "name: x  # :thumbs_up: [bold]not markup[/bold]\n"
        api.eval_bundle_revision_get.return_value = (make_revision(2, files={"x.yaml": text}), None)
        result = invoke("bundle", "show", "calls", "--files")
        assert ":thumbs_up: [bold]not markup[/bold]" in result.output

    def test_show_files_keeps_tabs(self, api):
        api.eval_bundle_revision_get.return_value = (
            make_revision(2, files={"x.yaml": "a:\tb\n"}),
            None,
        )
        result = invoke("bundle", "show", "calls", "--files")
        assert "a:\tb\n" in result.output

    def test_show_json_is_the_revision(self, api):
        result = invoke("bundle", "show", "calls@2", "--json")
        assert json.loads(result.stdout)["files"] == {"calls.yaml": SCENARIOS}


# ---- The API client ----


class TestApiClient:
    @pytest.fixture
    def client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("call", "args", "method", "path", "params"),
        [
            ("_eval_capabilities", ("org",), "GET", "/organizations/org/eval-capabilities", None),
            (
                "_eval_suite_run_get",
                ("org", SUITE_ID),
                "GET",
                f"/organizations/org/eval-suite-runs/{SUITE_ID}",
                None,
            ),
            (
                "_eval_suite_run_cancel",
                ("org", SUITE_ID),
                "POST",
                f"/organizations/org/eval-suite-runs/{SUITE_ID}/cancel",
                None,
            ),
            (
                "_eval_suite_run_items",
                ("org", SUITE_ID, 500, 0),
                "GET",
                f"/organizations/org/eval-suite-runs/{SUITE_ID}/items",
                {"limit": 500, "offset": 0},
            ),
            (
                "_eval_suite_runs_list",
                ("org", "my agent", 5),
                "GET",
                "/organizations/org/eval-suite-runs",
                {"serviceName": "my agent", "limit": 5},
            ),
            ("_eval_bundles_list", ("org",), "GET", "/organizations/org/eval-bundles", None),
            (
                "_eval_bundle_revisions_list",
                ("org", BUNDLE_ID, 200, 0),
                "GET",
                f"/organizations/org/eval-bundles/{BUNDLE_ID}/revisions",
                {"limit": 200, "offset": 0},
            ),
            (
                "_eval_bundle_revision_get",
                ("org", REVISION_ID),
                "GET",
                f"/organizations/org/eval-bundle-revisions/{REVISION_ID}",
                None,
            ),
        ],
    )
    async def test_reads(self, client, call, args, method, path, params):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await getattr(client, call)(*args)
        a, kwargs = request.call_args
        assert a[0] == method
        assert a[1].endswith("/v1" + path)
        assert kwargs.get("params") == params

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("call", "args", "path"),
        [
            (
                "_eval_suite_run_create",
                ("org", "agent"),
                "/organizations/org/services/agent/eval-suite-runs",
            ),
            ("_eval_bundle_create", ("org",), "/organizations/org/eval-bundles"),
            (
                "_eval_bundle_revision_append",
                ("org", BUNDLE_ID),
                f"/organizations/org/eval-bundles/{BUNDLE_ID}/revisions",
            ),
        ],
    )
    async def test_writes_post_the_body(self, client, call, args, path):
        body = {"requestId": "r", "files": {"a.yaml": "x"}}
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {"id": "x"}
            await getattr(client, call)(*args, body)
        a, kwargs = request.call_args
        assert a[0] == "POST"
        assert a[1].endswith("/v1" + path)
        assert kwargs["json"] == body

    @pytest.mark.asyncio
    async def test_eval_runs_list_filters_by_suite_run(self, client):
        with patch.object(client, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await client._eval_runs_list("org", "agent", limit=200, suite_run_id=SUITE_ID)
        assert request.call_args.kwargs["params"] == {"limit": 200, "suiteRunId": SUITE_ID}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "says"),
        [
            ("EVAL-SUITES-NOT-AVAILABLE", "Suite runs aren't available"),
            ("EVAL-BUNDLES-NOT-AVAILABLE", "Eval bundles aren't available"),
        ],
    )
    async def test_suites_and_bundles_off_are_said_plainly(self, client, code, says):
        import aiohttp

        async def refuse(*_args, **_kwargs):
            client.error = {"error": "terse", "code": code}
            raise aiohttp.ClientResponseError(None, (), status=503)  # type: ignore[arg-type]

        with patch.object(client, "_base_request", side_effect=refuse):
            with pytest.raises(aiohttp.ClientResponseError):
                await client._eval_request("GET", "http://x")
        assert says in client.error["error"]
