"""Tests for the GitHub integration commands (PCC-933).

Pins the load-bearing properties: the upsert payload only carries what the
caller passed, local validation refuses a malformed repo/branch before any
round-trip, a git source and an image are never sent together, the create-only
git binding is refused on an existing agent rather than silently dropped, and
the wait loop only reports the deploy it actually queued.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import (
    DeployConfigParams,
    GitDeployResult,
    GitDeployWait,
    GitSourceConfig,
    ResourcesConfig,
    report_git_deploy_result,
    validate_git_source_combination,
)
from pipecatcloud._utils.github_utils import (
    binding_summary,
    describe_deploy,
    is_deploy_in_flight,
    is_running_linked_binding,
    is_valid_branch_name,
    is_valid_repo_full_name,
    ref_to_branch,
)
from pipecatcloud.api import _API
from pipecatcloud.cli.commands.agent import _git_status_rows, agent_deploy, link, unlink
from pipecatcloud.cli.commands.build import _build_source
from pipecatcloud.cli.commands.github import branches, connect, disconnect
from pipecatcloud.cli.commands.github import status as github_status


@pytest.fixture
def github_mocks():
    with (
        patch("pipecatcloud.cli.commands.github.API") as mock_api,
        patch("pipecatcloud.cli.commands.github.console") as mock_console,
    ):
        mock_console.is_terminal = False
        mock_console.json_output = False
        mock_console.rich_output = False
        yield mock_api, mock_console


@pytest.fixture
def agent_mocks():
    with (
        patch("pipecatcloud.cli.commands.agent.API") as mock_api,
        patch("pipecatcloud.cli.commands.agent.console") as mock_console,
    ):
        mock_console.is_terminal = False
        mock_console.json_output = False
        mock_console.rich_output = False
        yield mock_api, mock_console


INSTALLATION = {
    "id": "inst-1",
    "githubInstallationId": "12345",
    "githubAccountLogin": "daily-co",
    "githubAccountType": "organization",
    "suspendedAt": None,
}


# --- validation helpers -----------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("daily-co/pipecat", True),
        ("owner/repo.name", True),
        ("owner", False),
        ("owner/repo/extra", False),
        ("../etc", False),
        ("owner/..", False),
        ("owner/re po", False),
        ("owner/repo?x=1", False),
    ],
)
def test_repo_full_name_validation(value, expected):
    assert is_valid_repo_full_name(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("main", True),
        ("feat/thing", True),
        ("", False),
        ("feat//thing", False),
        ("feat/../thing", False),
        ("..", False),
    ],
)
def test_branch_name_validation(value, expected):
    assert is_valid_branch_name(value) is expected


def test_ref_to_branch_strips_only_heads_prefix():
    assert ref_to_branch("refs/heads/feat/x") == "feat/x"
    assert ref_to_branch("refs/tags/v1") == "refs/tags/v1"


def test_is_deploy_in_flight():
    assert is_deploy_in_flight({"status": "building"}) is True
    assert is_deploy_in_flight({"status": "succeeded"}) is False
    assert is_deploy_in_flight(None) is False


def test_describe_deploy_includes_reason_only_when_terminal_failure():
    assert describe_deploy({"status": "building", "commitSha": "abc1234def", "reason": "x"}) == (
        "building (abc1234)"
    )
    assert "boom" in describe_deploy(
        {"status": "failed", "commitSha": "abc1234def", "reason": "boom"}
    )


def test_binding_summary_dashes_when_unlinked():
    assert binding_summary(None) == "—"
    assert binding_summary({"repoFullName": "a/b", "branch": "main"}) == "a/b@main"


class TestIsRunningLinkedBinding:
    """The configured binding and the running commit legitimately disagree; the
    three cases that mean 'nothing from this link is live' must all report
    False, and an unknown ref must not be read as a mismatch."""

    GIT = {"repoFullName": "daily-co/bot", "branch": "main"}

    def test_matching_repo_and_branch(self):
        assert is_running_linked_binding(
            self.GIT,
            {"sha": "a" * 40, "ref": "refs/heads/main", "repoFullName": "daily-co/bot"},
        )

    def test_repo_case_is_ignored(self):
        assert is_running_linked_binding(
            self.GIT,
            {"sha": "a" * 40, "ref": "refs/heads/main", "repoFullName": "Daily-Co/Bot"},
        )

    def test_different_branch_is_not_live(self):
        assert not is_running_linked_binding(
            self.GIT,
            {"sha": "a" * 40, "ref": "refs/heads/dev", "repoFullName": "daily-co/bot"},
        )

    def test_repointed_repo_is_not_live(self):
        assert not is_running_linked_binding(
            self.GIT,
            {"sha": "a" * 40, "ref": "refs/heads/main", "repoFullName": "daily-co/other"},
        )

    def test_missing_ref_means_unknown_not_mismatched(self):
        assert is_running_linked_binding(
            self.GIT, {"sha": "a" * 40, "ref": None, "repoFullName": "daily-co/bot"}
        )

    def test_no_deployed_commit_is_not_live(self):
        assert not is_running_linked_binding(self.GIT, None)


# --- github command group ---------------------------------------------------


@pytest.mark.asyncio
async def test_connect_short_circuits_when_already_connected(github_mocks):
    """An org that is already linked must not mint a fresh install state."""
    mock_api, _ = github_mocks
    mock_api.github_installation = AsyncMock(return_value=(INSTALLATION, None))
    mock_api.github_install_url = AsyncMock()

    await connect.aio(organization="test-org")

    mock_api.github_install_url.assert_not_called()


@pytest.mark.asyncio
async def test_connect_polls_until_the_installation_appears(github_mocks):
    """The link is completed server-side by the setup callback, so connect is
    only correct if it keeps polling past the initial not-connected reads."""
    mock_api, mock_console = github_mocks
    mock_api.github_installation = AsyncMock(side_effect=[(None, None), (None, None)])
    mock_api.github_install_url = AsyncMock(return_value=({"url": "https://github.test"}, None))
    mock_api.bubble_error.return_value.github_installation = AsyncMock(
        side_effect=[(None, None), (INSTALLATION, None)]
    )

    with (
        patch("pipecatcloud.cli.commands.github.asyncio.sleep", new_callable=AsyncMock),
        patch("pipecatcloud.cli.commands.auth._open_url", return_value=True),
    ):
        await connect.aio(organization="test-org")

    assert mock_api.bubble_error.return_value.github_installation.await_count == 2
    mock_console.success.assert_called_once()


@pytest.mark.asyncio
async def test_connect_times_out_nonzero(github_mocks):
    """A connect the user never completes must exit non-zero, not hang or
    claim success."""
    mock_api, _ = github_mocks
    mock_api.github_installation = AsyncMock(return_value=(None, None))
    mock_api.github_install_url = AsyncMock(return_value=({"url": "https://github.test"}, None))
    mock_api.bubble_error.return_value.github_installation = AsyncMock(return_value=(None, None))

    with (
        patch("pipecatcloud.cli.commands.github.asyncio.sleep", new_callable=AsyncMock),
        patch("pipecatcloud.cli.commands.github._POLL_TIMEOUT_SECONDS", 0),
        patch("pipecatcloud.cli.commands.auth._open_url", return_value=True),
        pytest.raises(typer.Exit) as excinfo,
    ):
        await connect.aio(organization="test-org")

    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_status_not_connected_is_not_an_error(github_mocks):
    """Not connected is a normal state (the API 404s), so it must not exit
    non-zero — scripts branch on the payload, not on a crash."""
    mock_api, mock_console = github_mocks
    mock_api.github_installation = AsyncMock(return_value=(None, None))

    await github_status.aio(organization="test-org")

    mock_console.error.assert_not_called()


@pytest.mark.asyncio
async def test_status_json_carries_null_installation(github_mocks):
    mock_api, mock_console = github_mocks
    mock_console.json_output = True
    mock_api.github_installation = AsyncMock(return_value=(None, None))

    await github_status.aio(organization="test-org")

    assert mock_console.output_json.call_args.args[0] == {"installation": None}


@pytest.mark.asyncio
async def test_disconnect_without_yes_requires_interactive(github_mocks):
    _, mock_console = github_mocks
    mock_console.require_interactive.side_effect = typer.Exit(2)

    with pytest.raises(typer.Exit) as excinfo:
        await disconnect.aio(organization="test-org", yes=False)

    assert excinfo.value.exit_code == 2
    mock_console.require_interactive.assert_called_once_with("--yes")


@pytest.mark.asyncio
async def test_branches_refuses_malformed_repo_before_calling_the_api(github_mocks):
    """The repo lands in the URL path, so a bad shape is refused locally."""
    mock_api, _ = github_mocks
    mock_api.github_branches = AsyncMock()

    with pytest.raises(typer.Exit) as excinfo:
        await branches.aio("owner/..", query=None, organization="test-org")

    assert excinfo.value.exit_code == 1
    mock_api.github_branches.assert_not_called()


@pytest.mark.asyncio
async def test_branches_empty_still_emits_json(github_mocks):
    mock_api, mock_console = github_mocks
    mock_console.json_output = True
    mock_api.github_branches = AsyncMock(return_value=([], None))

    await branches.aio("daily-co/bot", query=None, organization="test-org")

    out = mock_console.output_json.call_args.args[0]
    assert out == {"repository": "daily-co/bot", "branches": []}


# --- agent link / unlink / deploy -------------------------------------------


@pytest.mark.asyncio
async def test_link_sends_only_provided_fields(agent_mocks):
    """The binding is an upsert where omitted fields keep their stored value,
    so sending defaults would silently reset a stored dockerfile path."""
    mock_api, _ = agent_mocks
    mock_api.agent_git_connect = AsyncMock(
        return_value=({"repoFullName": "daily-co/bot", "branch": "main"}, None)
    )

    await link.aio(
        "my-agent",
        repo="daily-co/bot",
        branch="main",
        dockerfile_path=None,
        subdirectory=None,
        auto_deploy=None,
        organization="test-org",
    )

    payload = mock_api.agent_git_connect.await_args.kwargs["payload"]
    assert payload == {"repoFullName": "daily-co/bot", "branch": "main"}


@pytest.mark.asyncio
async def test_link_sends_auto_deploy_false_explicitly(agent_mocks):
    """--no-auto-deploy is a real value, not an omission: it has to reach the
    wire or push-to-deploy stays on."""
    mock_api, _ = agent_mocks
    mock_api.agent_git_connect = AsyncMock(return_value=({}, None))

    await link.aio(
        "my-agent",
        repo="daily-co/bot",
        branch="main",
        dockerfile_path="docker/Dockerfile",
        subdirectory=None,
        auto_deploy=False,
        organization="test-org",
    )

    payload = mock_api.agent_git_connect.await_args.kwargs["payload"]
    assert payload["autoDeploy"] is False
    assert payload["dockerfilePath"] == "docker/Dockerfile"
    assert "subdirectory" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repo,branch",
    [("not-a-repo", "main"), ("daily-co/bot", "feat//x")],
)
async def test_link_validates_before_calling_the_api(agent_mocks, repo, branch):
    mock_api, _ = agent_mocks
    mock_api.agent_git_connect = AsyncMock()

    with pytest.raises(typer.Exit) as excinfo:
        await link.aio(
            "my-agent",
            repo=repo,
            branch=branch,
            dockerfile_path=None,
            subdirectory=None,
            auto_deploy=None,
            organization="test-org",
        )

    assert excinfo.value.exit_code == 1
    mock_api.agent_git_connect.assert_not_called()


@pytest.mark.asyncio
async def test_unlink_without_force_requires_interactive(agent_mocks):
    _, mock_console = agent_mocks
    mock_console.require_interactive.side_effect = typer.Exit(2)

    with pytest.raises(typer.Exit) as excinfo:
        await unlink.aio("my-agent", organization="test-org", force=False)

    assert excinfo.value.exit_code == 2


@pytest.mark.asyncio
async def test_agent_deploy_requires_the_github_flag(agent_mocks):
    """Without --github the command must not guess; deploying an image is
    still `pipecat cloud deploy`."""
    mock_api, _ = agent_mocks
    mock_api.agent_git_deploy = AsyncMock()

    with pytest.raises(typer.Exit) as excinfo:
        await agent_deploy.aio("my-agent", github=False, wait=False, organization="test-org")

    assert excinfo.value.exit_code == 1
    mock_api.agent_git_deploy.assert_not_called()


@pytest.mark.asyncio
async def test_agent_deploy_json_carries_the_intent(agent_mocks):
    mock_api, mock_console = agent_mocks
    mock_console.json_output = True
    intent = {
        "id": "intent-1",
        "commitSha": "abc1234def",
        "ref": "refs/heads/main",
        "status": "pending",
    }
    mock_api.agent_git_deploy = AsyncMock(return_value=(intent, None))

    await agent_deploy.aio("my-agent", github=True, wait=False, organization="test-org")

    assert mock_console.output_json.call_args.args[0] == {"deployIntent": intent}


@pytest.mark.asyncio
async def test_agent_deploy_wait_exits_nonzero_on_failure(agent_mocks):
    mock_api, _ = agent_mocks
    mock_api.agent_git_deploy = AsyncMock(
        return_value=(
            {"id": "i", "commitSha": "abc1234def", "ref": "refs/heads/main", "status": "pending"},
            None,
        )
    )

    with (
        patch(
            "pipecatcloud.cli.commands.agent.follow_git_deploy",
            new_callable=AsyncMock,
        ) as mock_follow,
        pytest.raises(typer.Exit) as excinfo,
    ):
        mock_follow.return_value = GitDeployResult(
            GitDeployWait.TERMINAL, deploy={"status": "failed", "reason": "build error"}
        )
        await agent_deploy.aio("my-agent", github=True, wait=True, organization="test-org")

    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_agent_deploy_wait_json_reports_the_outcome_and_exits_nonzero(agent_mocks):
    """json consumers need to tell an unconfirmed deploy from a healthy one,
    and the exit code has to agree with the payload."""
    mock_api, mock_console = agent_mocks
    mock_console.json_output = True
    mock_api.agent_git_deploy = AsyncMock(
        return_value=(
            {"id": "i", "commitSha": "abc1234def", "ref": "refs/heads/main", "status": "pending"},
            None,
        )
    )

    with (
        patch(
            "pipecatcloud.cli.commands.agent.follow_git_deploy", new_callable=AsyncMock
        ) as mock_follow,
        pytest.raises(typer.Exit) as excinfo,
    ):
        mock_follow.return_value = GitDeployResult(GitDeployWait.UNOBSERVED)
        await agent_deploy.aio("my-agent", github=True, wait=True, organization="test-org")

    assert excinfo.value.exit_code == 1
    out = mock_console.output_json.call_args.args[0]
    assert out["waitOutcome"] == "unobserved"
    assert out["latestDeploy"] is None


@pytest.mark.asyncio
async def test_agent_deploy_wait_json_reports_a_supersede(agent_mocks):
    mock_api, mock_console = agent_mocks
    mock_console.json_output = True
    mock_api.agent_git_deploy = AsyncMock(
        return_value=(
            {"id": "i", "commitSha": "abc1234def", "ref": "refs/heads/main", "status": "pending"},
            None,
        )
    )

    with patch(
        "pipecatcloud.cli.commands.agent.follow_git_deploy", new_callable=AsyncMock
    ) as mock_follow:
        mock_follow.return_value = GitDeployResult(
            GitDeployWait.SUPERSEDED, superseded_by="999abcdef"
        )
        await agent_deploy.aio("my-agent", github=True, wait=True, organization="test-org")

    out = mock_console.output_json.call_args.args[0]
    assert out["waitOutcome"] == "superseded"
    assert out["supersededBy"] == "999abcdef"


@pytest.mark.asyncio
async def test_agent_deploy_wait_timeout_is_not_a_failure(agent_mocks):
    """Running out of polling budget says nothing about the deploy, which
    continues server-side — so it must not exit non-zero."""
    mock_api, _ = agent_mocks
    mock_api.agent_git_deploy = AsyncMock(
        return_value=(
            {"id": "i", "commitSha": "abc1234def", "ref": "refs/heads/main", "status": "pending"},
            None,
        )
    )

    with patch(
        "pipecatcloud.cli.commands.agent.follow_git_deploy", new_callable=AsyncMock
    ) as mock_follow:
        mock_follow.return_value = GitDeployResult(
            GitDeployWait.IN_FLIGHT, deploy={"status": "building"}
        )
        await agent_deploy.aio("my-agent", github=True, wait=True, organization="test-org")


def _follow_with(polls, commit_sha, max_polls=None):
    """Drive follow_git_deploy over a scripted sequence of poll results.

    The deadline is driven by a scripted monotonic() rather than wall time, so
    "the budget ran out" lands after an exact number of polls instead of
    whenever the test host happens to get there. `polls` repeats its last
    entry, so a test only scripts the responses it cares about.
    """
    from pipecatcloud._utils import deploy_utils

    # bubble_error() is sync and returns the client, so the client itself must
    # be a MagicMock; only the request method is awaited.
    mock_api = MagicMock()

    responses = list(polls)

    async def next_response(*_args, **_kwargs):
        return responses.pop(0) if len(responses) > 1 else responses[0]

    mock_api.bubble_error.return_value.agent = AsyncMock(side_effect=next_response)

    budget = max_polls if max_polls is not None else len(responses)
    # First call sets the deadline; each later call is one loop check. Hand out
    # `budget` in-budget checks, then a value past the deadline.
    clock = [0.0] + [0.0] * budget + [10_000.0]

    async def run():
        with (
            patch("pipecatcloud.cli.api.API", mock_api),
            patch.object(deploy_utils.asyncio, "sleep", new_callable=AsyncMock),
            patch.object(deploy_utils.time, "monotonic", side_effect=clock),
        ):
            return await deploy_utils.follow_git_deploy(
                agent_name="my-agent", org="test-org", commit_sha=commit_sha
            )

    return run(), mock_api


@pytest.mark.asyncio
async def test_follow_git_deploy_returns_our_terminal_attempt():
    ours = {"status": "succeeded", "commitSha": "abc"}
    coro, _ = _follow_with(
        [
            ({"latestDeploy": {"status": "building", "commitSha": "abc"}}, None),
            ({"latestDeploy": ours}, None),
        ],
        commit_sha="abc",
    )
    result = await coro

    assert result.outcome is GitDeployWait.TERMINAL
    assert result.deploy == ours


@pytest.mark.asyncio
async def test_follow_git_deploy_reports_a_newer_attempt_as_superseded():
    """A foreign commit holding `latestDeploy` is positive evidence that a
    newer push took over, not an absence of evidence. Ours can never reclaim
    the spot (the API orders by created_at DESC off the primary), so waiting
    out the budget would report a stale snapshot twenty minutes later."""
    coro, mock_api = _follow_with(
        [({"latestDeploy": {"status": "building", "commitSha": "999"}}, None)],
        commit_sha="abc",
    )
    result = await coro

    assert result.outcome is GitDeployWait.SUPERSEDED
    assert result.superseded_by == "999"
    # Returned on the first sighting rather than polling to the deadline.
    assert mock_api.bubble_error.return_value.agent.await_count == 1


@pytest.mark.asyncio
async def test_follow_git_deploy_never_reports_a_foreign_attempt_as_ours():
    """The property the old ignore-and-keep-polling behaviour protected: a
    succeeded attempt for someone else's commit must never be handed back as
    our terminal result."""
    coro, _ = _follow_with(
        [({"latestDeploy": {"status": "succeeded", "commitSha": "999"}}, None)],
        commit_sha="abc",
    )
    result = await coro

    assert result.outcome is not GitDeployWait.TERMINAL
    assert result.deploy is None


@pytest.mark.asyncio
async def test_follow_git_deploy_reports_never_seeing_an_attempt():
    """Twenty minutes of failed polls is not the same fact as a deploy that is
    still building, and must not be reported as one."""
    coro, _ = _follow_with([(None, {"error": "boom"})], commit_sha="abc", max_polls=3)
    result = await coro

    assert result.outcome is GitDeployWait.UNOBSERVED
    assert result.deploy is None


@pytest.mark.asyncio
async def test_follow_git_deploy_reports_an_unfinished_attempt_as_in_flight():
    coro, _ = _follow_with(
        [({"latestDeploy": {"status": "building", "commitSha": "abc"}}, None)],
        commit_sha="abc",
        max_polls=3,
    )
    result = await coro

    assert result.outcome is GitDeployWait.IN_FLIGHT
    assert result.deploy == {"status": "building", "commitSha": "abc"}


class TestReportGitDeployResult:
    """Exit codes carry the CI contract: only an observed failure and a
    never-observed deploy are non-zero."""

    def _report(self, result, first_deploy=False):
        with patch("pipecatcloud._utils.console_utils.console") as mock_console:
            mock_console.json_output = False
            mock_console.rich_output = False
            report_git_deploy_result(result, "my-agent", first_deploy=first_deploy)
            return mock_console

    def test_success_exits_zero(self):
        console = self._report(
            GitDeployResult(
                GitDeployWait.TERMINAL, deploy={"status": "succeeded", "commitSha": "abc1234def"}
            )
        )
        console.success.assert_called_once()

    def test_observed_failure_exits_nonzero(self):
        with pytest.raises(typer.Exit) as excinfo:
            self._report(
                GitDeployResult(
                    GitDeployWait.TERMINAL,
                    deploy={"status": "failed", "reason": "build error"},
                )
            )
        assert excinfo.value.exit_code == 1

    def test_in_flight_timeout_exits_zero(self):
        console = self._report(
            GitDeployResult(GitDeployWait.IN_FLIGHT, deploy={"status": "building"})
        )
        assert "continues" in console.print.call_args.args[0]

    def test_superseded_names_the_newer_commit_and_exits_zero(self):
        console = self._report(GitDeployResult(GitDeployWait.SUPERSEDED, superseded_by="999abcdef"))
        assert "999abcd" in console.print.call_args.args[0]

    def test_unobserved_exits_nonzero(self):
        """A wait that saw nothing cannot tell a healthy build from an API
        that was down the whole time, so it must not read as success."""
        with pytest.raises(typer.Exit) as excinfo:
            self._report(GitDeployResult(GitDeployWait.UNOBSERVED))
        assert excinfo.value.exit_code == 1

    def test_first_deploy_labels_the_failure(self):
        with pytest.raises(typer.Exit):
            console = self._report(
                GitDeployResult(GitDeployWait.TERMINAL, deploy={"status": "failed"}),
                first_deploy=True,
            )
            assert "First deploy" in console.error.call_args.args[0]


# --- agent status / list ----------------------------------------------------


def test_git_status_rows_empty_for_unlinked_agent():
    assert _git_status_rows({"name": "a"}) == []


def test_git_status_rows_flag_a_pre_link_image():
    rows = dict(
        _git_status_rows(
            {
                "git": {"repoFullName": "daily-co/bot", "branch": "main"},
                "deployedCommit": None,
            }
        )
    )
    assert "nothing from this link is live yet" in rows["Running Commit"]


def test_git_status_rows_name_the_origin_of_a_mismatched_commit():
    """After a re-point, the running commit came from somewhere else; naming
    it is what makes the mismatch actionable."""
    rows = dict(
        _git_status_rows(
            {
                "git": {"repoFullName": "daily-co/bot", "branch": "main"},
                "deployedCommit": {
                    "sha": "abc1234def",
                    "ref": "refs/heads/old",
                    "repoFullName": "daily-co/legacy",
                },
            }
        )
    )
    assert "daily-co/legacy@old" in rows["Running Commit"]
    assert "not the current link" in rows["Running Commit"]


def test_git_status_rows_report_the_default_dockerfile_path():
    rows = dict(_git_status_rows({"git": {"repoFullName": "daily-co/bot", "branch": "main"}}))
    assert rows["Dockerfile Path"] == "Dockerfile"
    assert rows["Auto-deploy On Push"] == "yes"


@pytest.mark.asyncio
async def test_agent_list_requests_the_git_binding(agent_mocks):
    from pipecatcloud.cli.commands.agent import list_agents

    mock_api, _ = agent_mocks
    mock_api.agents = AsyncMock(
        return_value=(
            [
                {
                    "name": "a",
                    "region": "us-west",
                    "id": "1",
                    "activeDeploymentId": "d1",
                    "createdAt": "t",
                    "updatedAt": "t",
                    "git": {"repoFullName": "daily-co/bot", "branch": "main"},
                }
            ],
            None,
        )
    )

    await list_agents.aio(organization="test-org", region=None)

    assert mock_api.agents.await_args.kwargs["include"] == ["git"]


# --- build provenance -------------------------------------------------------


def test_build_source_dashes_without_commit_provenance():
    assert _build_source({"id": "b1"}) == "—"


def test_build_source_pairs_repo_and_short_sha():
    assert (
        _build_source({"commitSha": "abc1234def", "repoFullName": "daily-co/bot"})
        == "daily-co/bot@abc1234"
    )


def test_build_source_falls_back_to_the_sha_alone():
    assert _build_source({"commitSha": "abc1234def"}) == "abc1234"


# --- deploy config ----------------------------------------------------------


class TestGitSourceConfig:
    def test_requires_both_repo_and_branch(self):
        with pytest.raises(ValueError, match="both 'repo' and 'branch'"):
            GitSourceConfig(repo="daily-co/bot")

    def test_rejects_a_malformed_repo(self):
        with pytest.raises(ValueError, match="owner/repo"):
            GitSourceConfig(repo="daily-co", branch="main")

    def test_payload_omits_unset_optionals(self):
        config = GitSourceConfig(repo="daily-co/bot", branch="main")
        assert config.to_payload() == {"repoFullName": "daily-co/bot", "branch": "main"}

    def test_payload_carries_optionals(self):
        config = GitSourceConfig(
            repo="daily-co/bot", branch="main", dockerfile_path="d/Dockerfile", subdirectory="app"
        )
        assert config.to_payload()["dockerfilePath"] == "d/Dockerfile"
        assert config.to_payload()["subdirectory"] == "app"

    def test_is_mutually_exclusive_with_an_image(self):
        with pytest.raises(ValueError, match="GitHub source together with"):
            DeployConfigParams(
                agent_name="a",
                image="img:latest",
                git=GitSourceConfig(repo="daily-co/bot", branch="main"),
            )

    def test_is_mutually_exclusive_with_a_build_id(self):
        with pytest.raises(ValueError, match="GitHub source together with"):
            DeployConfigParams(
                agent_name="a",
                build_id="b-1",
                git=GitSourceConfig(repo="daily-co/bot", branch="main"),
            )


def test_deploy_config_file_reads_the_git_section(tmp_path, monkeypatch):
    config_file = tmp_path / "pcc-deploy.toml"
    config_file.write_text(
        'agent_name = "my-agent"\n\n[git]\nrepo = "daily-co/bot"\nbranch = "main"\n'
        'dockerfile_path = "docker/Dockerfile"\n'
    )
    monkeypatch.setenv("PIPECAT_DEPLOY_CONFIG_PATH", str(config_file))

    # cli.config resolves the path at import time, so patch the resolved value.
    with patch("pipecatcloud.cli.config.deploy_config_path", str(config_file)):
        from pipecatcloud._utils.deploy_utils import load_deploy_config_file

        loaded = load_deploy_config_file()

    assert loaded is not None
    assert loaded.git.repo == "daily-co/bot"
    assert loaded.git.branch == "main"
    assert loaded.git.dockerfile_path == "docker/Dockerfile"


@pytest.mark.asyncio
async def test_api_deploy_sends_git_and_no_image():
    """A git-sourced create has no image of its own, and must not carry an
    image pull secret over from a prior image config."""
    api_client = _API(token="t", is_cli=True)
    params = DeployConfigParams(
        agent_name="my-agent",
        image_credentials="my-pull-secret",
        git=GitSourceConfig(repo="daily-co/bot", branch="main"),
    )

    with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {}
        await api_client._deploy(deploy_config=params, org="test-org")

    payload = mock_request.call_args[1]["json"]
    assert payload["git"] == {"repoFullName": "daily-co/bot", "branch": "main"}
    assert "image" not in payload
    assert "buildId" not in payload
    assert "imagePullSecretSet" not in payload


@pytest.mark.asyncio
async def test_api_agents_include_is_comma_joined():
    api_client = _API(token="t", is_cli=True)

    with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"services": []}
        await api_client._agents(org="test-org", region="us-west", include=["git", "conditions"])

    assert mock_request.call_args[1]["params"] == {
        "region": "us-west",
        "include": "git,conditions",
    }


@pytest.mark.asyncio
async def test_api_agents_without_filters_sends_no_params():
    api_client = _API(token="t", is_cli=True)

    with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"services": []}
        await api_client._agents(org="test-org")

    assert mock_request.call_args[1]["params"] is None


@pytest.mark.asyncio
async def test_api_branches_url_encodes_each_segment():
    api_client = _API(token="t", is_cli=True)

    with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"branches": []}
        await api_client._github_branches(
            org="test-org", repo_full_name="daily-co/bot", query="feat/x"
        )

    url = mock_request.call_args[0][1]
    assert url.endswith("/github/repositories/daily-co/bot/branches")
    assert mock_request.call_args[1]["params"] == {"query": "feat/x"}


@pytest.mark.asyncio
async def test_api_installation_404_is_the_unconnected_state():
    """The API 404s when nothing is linked; that has to read as None rather
    than an error, or `github status` would exit non-zero for every org that
    simply hasn't connected yet."""
    api_client = _API(token="t", is_cli=True)

    with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = None
        result = await api_client._github_installation(org="test-org")

    assert result is None
    assert mock_request.call_args[1]["not_found_is_empty"] is True


class TestValidateGitSourceCombination:
    """The deploy command merges flags into the config field by field, so the
    dataclass' own validation never sees the merged result — these rules are
    the only thing standing between a bad combination and the API."""

    GIT = GitSourceConfig(repo="daily-co/bot", branch="main")

    def test_no_git_source_is_always_fine(self):
        assert validate_git_source_combination(DeployConfigParams(image="img:latest")) is None

    def test_git_alone_is_fine(self):
        assert validate_git_source_combination(DeployConfigParams(git=self.GIT)) is None

    def test_git_with_an_image_is_refused(self):
        config = DeployConfigParams(git=self.GIT)
        config.image = "img:latest"
        error = validate_git_source_combination(config)
        assert error is not None and "--image" in error

    def test_git_with_a_build_id_is_refused(self):
        config = DeployConfigParams(git=self.GIT)
        config.build_id = "b-1"
        error = validate_git_source_combination(config)
        assert error is not None and "--build-id" in error

    def test_git_with_explicit_resources_is_refused(self):
        """The API stores a git agent's first-deploy config on its binding,
        which explicit resources don't reach, so it rejects the pair."""
        config = DeployConfigParams(git=self.GIT, resources=ResourcesConfig(cpu="2", memory="4Gi"))
        error = validate_git_source_combination(config)
        assert error is not None and "--profile" in error

    def test_git_with_a_profile_is_fine(self):
        config = DeployConfigParams(git=self.GIT, agent_profile="agent-1x")
        assert validate_git_source_combination(config) is None
