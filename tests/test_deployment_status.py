"""
Unit tests for deployment status interpretation.

Tests the interpret_deployment_status function which maps raw API responses
to structured DeploymentStatus objects used for CLI display during deploy polling.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import (
    DeploymentPhase,
    _format_elapsed,
    _format_revision_line,
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
        assert "42 replicas" in lines[1]
        assert "Validating" in lines[1]
        assert "84 replicas" in lines[2]
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

    def test_not_available_progressing_with_replicas(self):
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
        assert "3 replicas" in lines[1]
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
        # No revision info → single line, no "replicas"
        assert "\n" not in status.status_message
        assert "replicas" not in status.status_message

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
        assert "42 replicas" in line

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
        assert "68 replicas" in line

    def test_revision_line_with_elapsed_time(self):
        from datetime import datetime, timezone

        recent = (datetime.now(timezone.utc)).isoformat()
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
        assert "10 replicas" in line
        # Should have a time component (0s or 1s)
        assert "s" in line

    def test_revision_line_without_replicas(self):
        """Revision with no readyReplicas field should omit replica count."""
        line = _format_revision_line(
            "Current ",
            {
                "deploymentID": "abcdef12",
                "phase": "Creating",
            },
        )
        assert "Creating" in line
        assert "replicas" not in line

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
        assert "42 replicas" in lines[1]
        # Line 2: previous revision
        assert "Previous" in lines[2]
        assert "deploy-1" in lines[2]
        assert "68 replicas" in lines[2]

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
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        result = _format_elapsed(now)
        assert result in ("0s", "1s")

    def test_minutes_ago(self):
        from datetime import datetime, timedelta, timezone

        two_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=2, seconds=30)).isoformat()
        result = _format_elapsed(two_min_ago)
        assert result.startswith("2m")

    def test_z_suffix(self):
        from datetime import datetime, timedelta, timezone

        ts = (datetime.now(timezone.utc) - timedelta(seconds=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = _format_elapsed(ts)
        assert "s" in result
