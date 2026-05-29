"""
Unit tests for deploy status-poll failure handling (PCC-872).

The deploy poll loop must not crash when a status poll fails: `API.agent`
returns `(None, error)` on any non-2xx, so the loop has to classify the failure
before touching the (None) status payload. `_poll_failure_is_transient` is that
classifier — transient blips get retried, real problems abort.
"""

from pipecatcloud.cli.commands.deploy import _poll_failure_is_transient


class TestTransientFailures:
    """Failures that should be retried rather than failing the deploy."""

    def test_no_error_payload_is_transient(self):
        # Network-level failures (timeout, connection reset) and 200-without-body
        # responses surface as (None, None) — no structured error at all.
        assert _poll_failure_is_transient(None) is True

    def test_502_bad_gateway_is_transient(self):
        # The exact case from the ticket: API pod cycled by its liveness probe.
        assert _poll_failure_is_transient({"error": "Bad Gateway", "code": "502"}) is True

    def test_500_and_503_are_transient(self):
        assert _poll_failure_is_transient({"code": "500"}) is True
        assert _poll_failure_is_transient({"code": "503"}) is True


class TestFatalFailures:
    """Failures that won't clear on their own and should abort the deploy."""

    def test_4xx_is_fatal(self):
        assert _poll_failure_is_transient({"error": "Unauthorized", "code": "401"}) is False
        assert _poll_failure_is_transient({"error": "Not Found", "code": "404"}) is False

    def test_non_numeric_application_code_is_fatal(self):
        # Structured PCC errors carry codes like this rather than HTTP statuses.
        assert _poll_failure_is_transient({"code": "AGENT_CONFIG_INVALID"}) is False

    def test_missing_code_with_error_present_is_fatal(self):
        # An error dict with no code we can classify: don't assume retryable.
        assert _poll_failure_is_transient({"error": "something broke"}) is False
