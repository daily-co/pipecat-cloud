"""
Unit tests for deployment status interpretation.

Tests the interpret_deployment_status function which maps raw API responses
to structured DeploymentStatus objects used for CLI display during deploy polling.
"""

from datetime import UTC

from pipecatcloud._utils.deploy_utils import (
    DeploymentPhase,
    _format_elapsed,
    _format_revision_line,
    format_health_lines,
    interpret_deployment_status,
)


class TestWaitingForOperator:
    """When reconciledDeploymentId != desiredDeploymentId, operator hasn't processed yet."""

    def test_unreconciled_deployment(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-new",
                "reconciledDeploymentId": "deploy-old",
                "available": False,
                "ready": False,
            },
            desired_deployment_id="deploy-new",
        )
        assert status.phase == DeploymentPhase.WAITING_FOR_OPERATOR
        assert not status.is_available
        assert not status.is_ready

    def test_no_reconciled_id_yet(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-new",
                "reconciledDeploymentId": None,
                "available": False,
                "ready": False,
            },
            desired_deployment_id="deploy-new",
        )
        assert status.phase == DeploymentPhase.WAITING_FOR_OPERATOR


class TestReady:
    """When available=true and ready=true (or activeDeploymentReady=true)."""

    def test_available_and_ready(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": True,
                "ready": True,
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.READY
        assert status.is_available
        assert status.is_ready

    def test_available_and_active_deployment_ready(self):
        """activeDeploymentReady is the legacy field, should still trigger READY."""
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": True,
                "ready": False,
                "activeDeploymentReady": True,
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.READY
        assert status.is_available
        assert status.is_ready

    def test_ready_fallback_to_ready_field(self):
        """When 'available' is missing, falls back to 'ready'."""
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "ready": True,
                "activeDeploymentReady": True,
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.READY


class TestProgressingAvailable:
    """Available but not ready — rolling update with existing service serving traffic."""

    def test_available_not_ready(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-2",
                "reconciledDeploymentId": "deploy-2",
                "available": True,
                "ready": False,
                "conditions": [
                    {"type": "Available", "status": "True"},
                    {"type": "Progressing", "status": "True"},
                ],
            },
            desired_deployment_id="deploy-2",
        )
        assert status.phase == DeploymentPhase.PROGRESSING_AVAILABLE
        assert status.is_available
        assert not status.is_ready
        assert "Progressing" in status.status_message
        assert "Available" in status.status_message

    def test_available_not_ready_with_revision_info(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-2",
                "reconciledDeploymentId": "deploy-2",
                "available": True,
                "ready": False,
                "currentRevision": {
                    "deploymentID": "deploy-2",
                    "phase": "Validating",
                    "readyReplicas": 42,
                },
                "previousRevision": {
                    "deploymentID": "deploy-1",
                    "phase": "Draining",
                    "readyReplicas": 84,
                },
            },
            desired_deployment_id="deploy-2",
        )
        assert status.phase == DeploymentPhase.PROGRESSING_AVAILABLE
        assert status.is_available
        # Multi-line: headline + current + previous
        lines = status.status_message.split("\n")
        assert len(lines) == 3
        assert "42 agents" in lines[1]
        assert "Validating" in lines[1]
        assert "84 agents" in lines[2]
        assert "Draining" in lines[2]
        assert status.current_revision["readyReplicas"] == 42
        assert status.previous_revision["phase"] == "Draining"


class TestProgressingNew:
    """Not available, but progressing — new service coming up for the first time."""

    def test_not_available_progressing(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": False,
                "ready": False,
                "conditions": [
                    {"type": "Progressing", "status": "True"},
                ],
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.PROGRESSING_NEW
        assert not status.is_available
        assert not status.is_ready
        assert "Progressing" in status.status_message

    def test_not_available_progressing_with_agents(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": False,
                "ready": False,
                "conditions": [
                    {"type": "Progressing", "status": "True"},
                ],
                "currentRevision": {
                    "deploymentID": "deploy-1",
                    "phase": "Creating",
                    "readyReplicas": 3,
                },
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.PROGRESSING_NEW
        lines = status.status_message.split("\n")
        assert len(lines) == 2
        assert "3 agents" in lines[1]
        assert "Creating" in lines[1]


class TestDegradedAvailable:
    """Available but degraded — serving traffic but something is wrong."""

    def test_degraded_and_available(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": True,
                "ready": False,
                "conditions": [
                    {"type": "Available", "status": "True"},
                    {
                        "type": "Degraded",
                        "status": "True",
                        "reason": "ImageResolutionFailed",
                        "message": "Failed to resolve image tag",
                    },
                ],
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.DEGRADED_AVAILABLE
        assert status.is_available
        assert not status.is_ready
        assert status.degraded_reason == "Failed to resolve image tag"
        assert "Degraded" in status.status_message
        assert "Available" in status.status_message

    def test_degraded_reason_falls_back_to_reason_field(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": True,
                "ready": False,
                "conditions": [
                    {
                        "type": "Degraded",
                        "status": "True",
                        "reason": "SomeReason",
                    },
                ],
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.DEGRADED_AVAILABLE
        assert status.degraded_reason == "SomeReason"


class TestUnavailable:
    """Not available and not progressing — broken."""

    def test_not_available_not_progressing(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": False,
                "ready": False,
                "conditions": [],
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.UNAVAILABLE
        assert not status.is_available
        assert not status.is_ready
        assert "Unavailable" in status.status_message

    def test_unavailable_with_degraded_reason(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": False,
                "ready": False,
                "conditions": [
                    {
                        "type": "Degraded",
                        "status": "True",
                        "message": "CrashLoopBackOff",
                    },
                ],
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.UNAVAILABLE
        assert "CrashLoopBackOff" in status.status_message


class TestBackwardCompatibility:
    """Tests for API responses that don't include new fields (conditions, revisions)."""

    def test_no_conditions_field(self):
        """Old API without conditions array should still work."""
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": True,
                "ready": True,
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.READY

    def test_no_revision_info(self):
        """API without revision fields should work — single-line message, no replica info."""
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": True,
                "ready": False,
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.PROGRESSING_AVAILABLE
        assert status.current_revision is None
        # No revision info → single line, no "agents"
        assert "\n" not in status.status_message
        assert "agents" not in status.status_message

    def test_legacy_ready_field_only(self):
        """Very old API that only has 'ready' field."""
        status = interpret_deployment_status(
            {
                "activeDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "ready": True,
                "activeDeploymentReady": True,
            },
            desired_deployment_id="deploy-1",
        )
        assert status.phase == DeploymentPhase.READY

    def test_no_desired_deployment_id(self):
        """When desired_deployment_id is None, skip reconciliation check."""
        status = interpret_deployment_status(
            {
                "available": True,
                "ready": True,
            },
        )
        assert status.phase == DeploymentPhase.READY


class TestRevisionLines:
    """Tests for multi-line revision detail rendering."""

    def test_current_revision_line_format(self):
        line = _format_revision_line(
            "Current ",
            {
                "deploymentID": "73071caf-abcd-1234",
                "phase": "Validating",
                "readyReplicas": 42,
            },
        )
        assert "73071caf" in line
        assert "Validating" in line
        assert "42 agents" in line

    def test_previous_revision_line_format(self):
        line = _format_revision_line(
            "Previous",
            {
                "deploymentID": "15122a6b-efgh-5678",
                "phase": "Draining",
                "readyReplicas": 68,
            },
        )
        assert "15122a6b" in line
        assert "Draining" in line
        assert "68 agents" in line

    def test_revision_line_with_elapsed_time(self):
        from datetime import datetime

        recent = (datetime.now(UTC)).isoformat()
        line = _format_revision_line(
            "Current ",
            {
                "deploymentID": "abcdef12",
                "phase": "Validating",
                "readyReplicas": 10,
                "phaseStartedAt": recent,
            },
        )
        assert "Validating" in line
        assert "10 agents" in line
        # Should have a time component (0s or 1s)
        assert "s" in line

    def test_revision_line_without_agents(self):
        """Revision with no readyReplicas field should omit replica count."""
        line = _format_revision_line(
            "Current ",
            {
                "deploymentID": "abcdef12",
                "phase": "Creating",
            },
        )
        assert "Creating" in line
        assert "agents" not in line

    def test_multi_line_message_with_both_revisions(self):
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-2",
                "reconciledDeploymentId": "deploy-2",
                "available": True,
                "ready": False,
                "currentRevision": {
                    "deploymentID": "deploy-2-full-uuid",
                    "phase": "Validating",
                    "readyReplicas": 42,
                },
                "previousRevision": {
                    "deploymentID": "deploy-1-full-uuid",
                    "phase": "Draining",
                    "readyReplicas": 68,
                },
            },
            desired_deployment_id="deploy-2",
        )
        lines = status.status_message.split("\n")
        assert len(lines) == 3
        # Line 0: condition headline
        assert "Progressing" in lines[0]
        assert "Available" in lines[0]
        # Line 1: current revision (deploymentID truncated to 8 chars)
        assert "Current" in lines[1]
        assert "deploy-2" in lines[1]
        assert "42 agents" in lines[1]
        # Line 2: previous revision
        assert "Previous" in lines[2]
        assert "deploy-1" in lines[2]
        assert "68 agents" in lines[2]

    def test_single_line_when_no_revisions(self):
        """Without revision data, message stays single-line."""
        status = interpret_deployment_status(
            {
                "desiredDeploymentId": "deploy-1",
                "reconciledDeploymentId": "deploy-1",
                "available": False,
                "ready": False,
                "conditions": [{"type": "Progressing", "status": "True"}],
            },
            desired_deployment_id="deploy-1",
        )
        assert "\n" not in status.status_message


class TestFormatElapsed:
    """Tests for the elapsed time formatter."""

    def test_none_input(self):
        assert _format_elapsed(None) == ""

    def test_invalid_input(self):
        assert _format_elapsed("not-a-date") == ""

    def test_recent_timestamp(self):
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
        result = _format_elapsed(now)
        assert result in ("0s", "1s")

    def test_minutes_ago(self):
        from datetime import datetime, timedelta

        two_min_ago = (datetime.now(UTC) - timedelta(minutes=2, seconds=30)).isoformat()
        result = _format_elapsed(two_min_ago)
        assert result.startswith("2m")

    def test_z_suffix(self):
        from datetime import datetime, timedelta

        ts = (datetime.now(UTC) - timedelta(seconds=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _format_elapsed(ts)
        assert "s" in result


class TestHealthLines:
    """Tests for format_health_lines rendering."""

    def test_crashloopbackoff_with_oomkilled(self):
        lines = format_health_lines(
            {
                "reason": "CrashLoopBackOff",
                "lastTerminationReason": "OOMKilled",
                "lastExitCode": 137,
                "restartCount": 42,
                "replicasStarted": 3,
            }
        )
        assert len(lines) >= 1
        assert "CrashLoopBackOff" in lines[0]
        assert "OOMKilled" in lines[0]
        assert "exit code 137" in lines[0]
        assert "42 restarts" in lines[0]
        assert "3 replicas" in lines[0]

    def test_error_with_exit_code(self):
        lines = format_health_lines(
            {
                "reason": "Error",
                "lastExitCode": 1,
                "restartCount": 4,
                "replicasStarted": 2,
            }
        )
        assert "Error" in lines[0]
        assert "exit code 1" in lines[0]
        assert "4 restarts" in lines[0]

    def test_message_shows_last_line(self):
        lines = format_health_lines(
            {
                "reason": "Error",
                "lastExitCode": 1,
                "restartCount": 1,
                "replicasStarted": 1,
                "message": "Traceback (most recent call last):\n  File \"bot.py\"\nModuleNotFoundError: No module named 'foo'",
            }
        )
        assert len(lines) == 2
        assert "ModuleNotFoundError" in lines[1]
        # Should NOT include the full traceback, just the last line
        assert "Traceback" not in lines[1]

    def test_image_pull_backoff(self):
        lines = format_health_lines(
            {
                "reason": "ImagePullBackOff",
                "restartCount": 0,
                "replicasStarted": 2,
                "message": 'Back-off pulling image "nonexistent:latest"',
            }
        )
        assert "ImagePullBackOff" in lines[0]
        # No restarts, so no restart text
        assert "restarts" not in lines[0]
        assert "nonexistent:latest" in lines[1]

    def test_no_reason_no_restarts(self):
        """Health with no reason and no restarts produces no lines."""
        lines = format_health_lines(
            {
                "restartCount": 0,
                "replicasStarted": 1,
            }
        )
        assert len(lines) == 0

    def test_same_reason_and_termination_reason(self):
        """When reason == lastTerminationReason, don't duplicate."""
        lines = format_health_lines(
            {
                "reason": "Error",
                "lastTerminationReason": "Error",
                "lastExitCode": 1,
                "restartCount": 3,
                "replicasStarted": 1,
            }
        )
        # Should show "Error (exit code 1)" not "Error (Error, exit code 1)"
        assert "Error (exit code 1)" in lines[0]

    def test_headline_replaces_reason_string(self):
        """When the API supplies a customer-friendly headline, render it instead of
        the raw k8s reason. Exit code is appended as its own segment so it stays
        visible (today's format folded the exit code into the reason string)."""
        lines = format_health_lines(
            {
                "reason": "CrashLoopBackOff",
                "lastTerminationReason": "OOMKilled",
                "lastExitCode": 137,
                "restartCount": 5,
                "replicasStarted": 2,
                "headline": "Out of memory",
            }
        )
        assert "Out of memory" in lines[0]
        # Raw k8s vocabulary should NOT appear in the rendered line when we have a headline.
        assert "CrashLoopBackOff" not in lines[0]
        assert "OOMKilled" not in lines[0]
        # Restart count and exit code are both visible.
        assert "5 restarts across 2 replicas" in lines[0]
        assert "exit code 137" in lines[0]

    def test_headline_with_traceback_message(self):
        """Application Error case: headline plus the last line of the captured traceback."""
        lines = format_health_lines(
            {
                "reason": "CrashLoopBackOff",
                "lastTerminationReason": "Error",
                "lastExitCode": 1,
                "restartCount": 3,
                "replicasStarted": 2,
                "headline": "Application Error",
                "message": (
                    "Traceback (most recent call last):\n"
                    '  File "bot.py"\n'
                    "ModuleNotFoundError: No module named 'nonexistentlibthiswillfail'"
                ),
            }
        )
        assert len(lines) == 2
        assert "Application Error" in lines[0]
        assert "exit code 1" in lines[0]
        assert "ModuleNotFoundError" in lines[1]
        assert "Traceback" not in lines[1]

    def test_headline_without_exit_code(self):
        """ImagePullBackOff and similar pre-start failures have no exit code; the
        exit-code segment must be omitted, not rendered as 'exit None' or similar."""
        lines = format_health_lines(
            {
                "reason": "ImagePullBackOff",
                "restartCount": 0,
                "replicasStarted": 2,
                "headline": "Cannot pull image",
                "message": 'Back-off pulling image "nonexistent:latest"',
            }
        )
        assert "Cannot pull image" in lines[0]
        assert "exit" not in lines[0]
        assert "ImagePullBackOff" not in lines[0]
        assert "nonexistent:latest" in lines[1]

    def test_unmapped_reason_falls_back_to_raw_format(self):
        """An older API response (or a future unmapped reason) has no headline;
        rendering must continue to work via the fallback path."""
        lines = format_health_lines(
            {
                "reason": "CrashLoopBackOff",
                "lastTerminationReason": "OOMKilled",
                "lastExitCode": 137,
                "restartCount": 5,
                "replicasStarted": 2,
            }
        )
        # Existing format preserved exactly: parens, comma, k8s vocabulary.
        assert "CrashLoopBackOff (OOMKilled, exit code 137)" in lines[0]
        assert "5 restarts across 2 replicas" in lines[0]


class TestRevisionLineWithHealth:
    """Tests that _format_revision_line includes health details."""

    def test_revision_with_crash_health(self):
        output = _format_revision_line(
            "Current ",
            {
                "deploymentID": "17baf1ed-1234",
                "phase": "Validating",
                "readyReplicas": 0,
                "health": {
                    "ready": False,
                    "state": "terminated",
                    "reason": "Error",
                    "lastExitCode": 1,
                    "lastTerminationReason": "Error",
                    "restartCount": 4,
                    "replicasStarted": 2,
                    "message": "ModuleNotFoundError: No module named 'foo'",
                },
            },
        )
        lines = output.split("\n")
        assert len(lines) >= 2
        assert "Validating" in lines[0]
        assert "Error" in lines[1]
        assert "ModuleNotFoundError" in lines[2]

    def test_revision_with_healthy_state_no_extra_lines(self):
        output = _format_revision_line(
            "Current ",
            {
                "deploymentID": "abc12345-5678",
                "phase": "Active",
                "readyReplicas": 3,
                "health": {
                    "ready": True,
                    "state": "running",
                    "restartCount": 0,
                    "replicasStarted": 3,
                },
            },
        )
        lines = output.split("\n")
        assert len(lines) == 1  # No health detail lines for healthy containers

    def test_revision_with_infra_issue(self):
        output = _format_revision_line(
            "Current ",
            {
                "deploymentID": "abc12345-5678",
                "phase": "Validating",
                "readyReplicas": 0,
                "health": {
                    "ready": True,
                    "state": "running",
                    "restartCount": 0,
                    "replicasStarted": 2,
                },
                "hasInfrastructureIssue": True,
            },
        )
        assert "Infrastructure issue" in output
        assert "contact support" in output
