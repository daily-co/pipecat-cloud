"""Tests for the project command group (PCC-1255).

A project is an agent with environments; each environment is an agent of its
own, addressed through its project with `--env`. One group covers both, and
`agent` is the same group under a deprecated name.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.environment_utils import (
    environment_name_problem,
    environment_sort_key,
    environments_of,
    is_preview_name,
    previews_on,
    project_label,
    projects_in,
    promotion_source,
)
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

BUILD_ID = "4bb223c4-9f31-44f5-9b3c-c6b8ea9c3a01"


@contextmanager
def api_mock():
    """One mock for every module the group's commands reach the API through."""
    with (
        patch("pipecatcloud.cli.commands.agent.API") as api,
        patch("pipecatcloud.cli.commands.project.API", api),
        patch("pipecatcloud.cli.commands.project_group.API", api),
    ):
        api.bubble_error = lambda: api
        yield api


def service(
    name: str,
    service_id: str,
    *,
    parent: str | None = None,
    environment_name: str | None = None,
    region: str = "us-west",
    git: dict | None = None,
    deployment: dict | None = None,
    deployed_sha: str | None = None,
) -> dict:
    return {
        "name": name,
        "id": service_id,
        "region": region,
        "activeDeploymentId": "dep-1",
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",
        "parentServiceId": parent,
        "environmentName": environment_name,
        "git": git,
        "deployment": deployment,
        "deployedCommit": (
            {"sha": deployed_sha, "ref": None, "repoFullName": None} if deployed_sha else None
        ),
    }


def binding(**overrides) -> dict:
    return {
        "repoFullName": "my-org/my-bot",
        "branch": "main",
        "dockerfilePath": "Dockerfile",
        "autoDeploy": True,
        "previewSecretSet": None,
        "previewBotPullRequests": False,
        "pullRequestNumber": None,
        "watchPaths": [],
        **overrides,
    }


def status_payload(**overrides) -> dict:
    payload = {
        "ready": True,
        "activeSessionCount": 0,
        "deployment": {"manifest": {"spec": {"image": "img:1"}}},
        "activeDeploymentId": "dep-1",
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",
        "parentServiceId": None,
        "environmentName": None,
        "git": None,
        "errors": [],
    }
    payload.update(overrides)
    return payload


# A project, a named environment and a PR preview of it.
PROJECT = service(
    "my-bot",
    "s-1",
    git=binding(previewSecretSet="preview-secrets", watchPaths=["bot/**"]),
    deployed_sha="abc1234def",
)
STAGING = service(
    "my-bot-staging",
    "s-2",
    parent="s-1",
    environment_name="staging",
    git=binding(branch="staging"),
    deployed_sha="bbb2222ccc",
)
PREVIEW = service(
    "my-bot-pr-7",
    "s-3",
    parent="s-1",
    environment_name="pr-7",
    git=binding(branch="feature", pullRequestNumber=7),
    deployed_sha="ccc3333ddd",
)
UNRELATED = service("other-bot", "s-4", git=None)

ALL_SERVICES = [PROJECT, STAGING, PREVIEW, UNRELATED]


class TestEnvironmentNameRules:
    def test_production_is_reserved(self):
        assert "production" in (environment_name_problem("production") or "")

    def test_preview_prefix_is_reserved(self):
        assert "pr-" in (environment_name_problem("pr-9") or "")

    def test_empty_is_refused(self):
        assert environment_name_problem("") is not None

    def test_single_character_is_refused(self):
        # The rule is two or more characters, as the API's regex requires.
        assert environment_name_problem("a") is not None

    def test_uppercase_and_underscores_are_refused(self):
        assert environment_name_problem("Staging") is not None
        assert environment_name_problem("stag_ing") is not None

    def test_leading_or_trailing_dash_is_refused(self):
        assert environment_name_problem("-dev") is not None
        assert environment_name_problem("dev-") is not None

    def test_too_long_is_refused(self):
        assert environment_name_problem("a" * 51) is not None

    def test_ordinary_name_is_fine(self):
        assert environment_name_problem("staging") is None
        assert environment_name_problem("eu-west-2") is None


class TestEnvironmentShape:
    def test_projects_exclude_environments(self):
        assert [p["name"] for p in projects_in(ALL_SERVICES)] == ["my-bot", "other-bot"]

    def test_environments_of_a_project(self):
        assert [e["environmentName"] for e in environments_of(ALL_SERVICES, PROJECT)] == [
            "staging",
            "pr-7",
        ]

    def test_named_environments_sort_before_previews(self):
        names = ["pr-2", "staging", "pr-11", "dev"]
        assert sorted(names, key=environment_sort_key) == ["dev", "staging", "pr-11", "pr-2"]

    def test_preview_names(self):
        assert is_preview_name("pr-7")
        assert not is_preview_name("staging")
        assert not is_preview_name("pr-seven")

    def test_previews_on_reads_the_secret_set(self):
        assert previews_on(PROJECT)
        assert not previews_on(STAGING)

    def test_project_label_resolves_the_parent_name(self):
        names = {s["id"]: s["name"] for s in ALL_SERVICES}
        assert project_label(STAGING, names) == "my-bot > staging"
        assert project_label(PROJECT, names) == "—"


class TestPromotionSource:
    def test_build_wins_over_image(self):
        environment = service(
            "e",
            "s-9",
            deployment={"buildId": BUILD_ID, "manifest": {"spec": {"image": "img:1"}}},
        )
        assert promotion_source(environment) == {"buildId": BUILD_ID}

    def test_image_when_there_is_no_build(self):
        environment = service("e", "s-9", deployment={"manifest": {"spec": {"image": "img:1"}}})
        assert promotion_source(environment) == {"image": "img:1"}

    def test_nothing_deployed(self):
        assert promotion_source(service("e", "s-9")) is None


class TestList:
    def test_project_list_shows_projects_only(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "list"])
        assert result.exit_code == 0
        # Plain mode is tab-separated: one named environment, one open
        # preview, previews on.
        assert "my-bot\tus-west\ts-1\tdep-1" in result.output
        assert "\t1\ton (1 open)" in result.output
        assert "other-bot" in result.output
        # Environments and previews are not projects of their own.
        assert "my-bot-staging" not in result.output
        assert "my-bot-pr-7" not in result.output

    def test_all_includes_environments_and_names_their_project(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "list", "--all"])
        assert result.exit_code == 0
        assert "my-bot > staging" in result.output
        assert "my-bot > pr-7" in result.output
        # An environment can have neither, so both columns are a dash.
        assert "my-bot-staging\tus-west\ts-2\tdep-1" in result.output

    def test_the_deprecated_agent_list_still_lists_everything(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "list"])
        assert result.exit_code == 0
        assert "my-bot > staging" in result.output
        assert "my-bot-pr-7" in result.output

    def test_no_agents_exits_nonzero(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([], None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "list"])
        assert result.exit_code == 1


class TestDeprecatedAlias:
    def test_agent_warns(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "list"])
        assert "'pipecat cloud agent' is deprecated" in result.output

    def test_project_does_not_warn(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "list"])
        assert "deprecated" not in result.output

    def test_the_warning_stays_off_stdout(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "list"])
        assert "deprecated" in result.stderr
        assert "deprecated" not in result.stdout

    def test_the_alias_runs_the_same_command(self):
        with api_mock() as api:
            api.environment_link = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["agent", "env", "add", "my-bot", "dev", "--agent", "my-bot-dev"],
            )
        assert result.exit_code == 0
        api.environment_link.assert_awaited_once()


class TestEnvironmentAddressing:
    def test_env_resolves_to_the_environments_agent(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.agent = AsyncMock(return_value=(status_payload(), None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "status", "my-bot", "--env", "staging"]
            )
        assert result.exit_code == 0
        assert api.agent.await_args.kwargs["agent_name"] == "my-bot-staging"

    def test_a_preview_is_addressed_the_same_way(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.agent_logs = AsyncMock(
                return_value=(
                    {"logs": [{"timestamp": "2026-01-01T00:00:00Z", "log": "INFO hello"}]},
                    None,
                )
            )
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "logs", "my-bot", "--env", "pr-7"]
            )
        assert result.exit_code == 0
        assert api.agent_logs.await_args.kwargs["agent_name"] == "my-bot-pr-7"

    def test_sessions_follow_the_environment(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.agent_sessions = AsyncMock(return_value=({"sessions": []}, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "sessions", "my-bot", "--env", "staging"]
            )
        assert result.exit_code == 0
        assert api.agent_sessions.await_args.kwargs["agent_name"] == "my-bot-staging"

    def test_delete_follows_the_environment(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.agent_delete = AsyncMock(return_value=({"ok": True}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "delete", "my-bot", "--env", "staging", "--force"],
            )
        assert result.exit_code == 0
        assert api.agent_delete.await_args.kwargs["agent_name"] == "my-bot-staging"

    def test_without_env_nothing_is_looked_up(self):
        with api_mock() as api:
            api.agents = AsyncMock()
            api.agent = AsyncMock(return_value=(status_payload(), None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "status", "my-bot"])
        assert result.exit_code == 0
        assert api.agent.await_args.kwargs["agent_name"] == "my-bot"
        api.agents.assert_not_awaited()

    def test_an_unknown_environment_is_refused(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.agent = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "status", "my-bot", "--env", "nope"]
            )
        assert result.exit_code == 1
        assert "staging" in result.output
        api.agent.assert_not_awaited()

    def test_env_on_an_agent_that_is_an_environment_is_refused(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.agent = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "status", "my-bot-staging", "--env", "dev"]
            )
        assert result.exit_code == 1
        assert "environment of" in result.output
        api.agent.assert_not_awaited()


class TestEnvList:
    def test_lists_the_projects_environments(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "env", "list", "my-bot"])
        assert result.exit_code == 0
        assert "staging" in result.output
        assert "#7" in result.output

    def test_refuses_an_environment(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "env", "list", "my-bot-staging"]
            )
        assert result.exit_code == 1
        assert "environment of" in result.output

    def test_unknown_agent(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "env", "list", "nope"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_no_environments_points_at_add(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([UNRELATED], None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "env", "list", "other-bot"])
        assert result.exit_code == 0
        assert "no environments" in result.output
        assert "project env add" in result.output


class TestEnvironmentLink:
    def test_add_sends_the_parent_and_name(self):
        with api_mock() as api:
            api.environment_link = AsyncMock(
                return_value=(
                    {"serviceName": "my-bot-dev", "parent": "my-bot", "environmentName": "dev"},
                    None,
                )
            )
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "env", "add", "my-bot", "dev", "--agent", "my-bot-dev"],
            )
        assert result.exit_code == 0
        assert api.environment_link.await_args.kwargs == {
            "agent_name": "my-bot-dev",
            "org": "test-org",
            "parent": "my-bot",
            "environment_name": "dev",
        }

    def test_a_refused_name_never_reaches_the_api(self):
        with api_mock() as api:
            api.environment_link = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "env", "add", "my-bot", "production", "--agent", "my-bot-dev"],
            )
        assert result.exit_code == 1
        api.environment_link.assert_not_awaited()

    def test_remove_resolves_the_environment_to_its_agent(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.environment_unlink = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "env", "remove", "my-bot", "staging"]
            )
        assert result.exit_code == 0
        assert api.environment_unlink.await_args.kwargs["agent_name"] == "my-bot-staging"

    def test_remove_names_the_environments_it_does_have(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            api.environment_unlink = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "env", "remove", "my-bot", "nope"]
            )
        assert result.exit_code == 1
        assert "staging" in result.output
        api.environment_unlink.assert_not_awaited()


class TestPromote:
    def test_promotes_the_build_onto_the_project(self):
        staging = dict(STAGING, deployment={"buildId": BUILD_ID, "manifest": {"spec": {}}})
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([PROJECT, staging], None))
            api.agent_update_source = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "env", "promote", "my-bot", "staging", "--force"],
            )
        assert result.exit_code == 0
        assert api.agent_update_source.await_args.kwargs == {
            "agent_name": "my-bot",
            "org": "test-org",
            "build_id": BUILD_ID,
            "image": None,
        }

    def test_promotes_an_image_when_there_is_no_build(self):
        staging = dict(STAGING, deployment={"manifest": {"spec": {"image": "docker.io/me/bot:2"}}})
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([PROJECT, staging], None))
            api.agent_update_source = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "env", "promote", "my-bot", "staging", "--force"],
            )
        assert result.exit_code == 0
        assert api.agent_update_source.await_args.kwargs["image"] == "docker.io/me/bot:2"
        assert api.agent_update_source.await_args.kwargs["build_id"] is None

    def test_nothing_deployed_is_refused(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([PROJECT, STAGING], None))
            api.agent_update_source = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "env", "promote", "my-bot", "staging", "--force"],
            )
        assert result.exit_code == 1
        assert "nothing deployed" in result.output
        api.agent_update_source.assert_not_awaited()

    def test_declining_the_prompt_deploys_nothing(self):
        staging = dict(STAGING, deployment={"buildId": BUILD_ID, "manifest": {"spec": {}}})
        with (
            api_mock() as api,
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=True),
            patch("pipecatcloud.cli.commands.project.questionary") as prompt,
        ):
            prompt.confirm.return_value.ask_async = AsyncMock(return_value=False)
            api.agents = AsyncMock(return_value=([PROJECT, staging], None))
            api.agent_update_source = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "env", "promote", "my-bot", "staging"]
            )
        assert result.exit_code == 1
        assert f"build {BUILD_ID}" in prompt.confirm.call_args.args[0]
        api.agent_update_source.assert_not_awaited()

    def test_confirmation_is_required_without_force(self):
        staging = dict(STAGING, deployment={"buildId": BUILD_ID, "manifest": {"spec": {}}})
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([PROJECT, staging], None))
            api.agent_update_source = AsyncMock()
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "env", "promote", "my-bot", "staging"]
            )
        # Exit 2: a prompt was needed and stdin is not a terminal.
        assert result.exit_code == 2
        api.agent_update_source.assert_not_awaited()


class TestPreviews:
    def test_enable_sends_the_secret_set(self):
        with api_mock() as api:
            api.previews_update = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "previews", "enable", "my-bot", "--secret-set", "preview-secrets"],
            )
        assert result.exit_code == 0
        assert api.previews_update.await_args.kwargs == {
            "agent_name": "my-bot",
            "org": "test-org",
            "secret_set": "preview-secrets",
            "bot_pull_requests": None,
        }

    def test_bot_pull_requests_is_sent_when_asked_for(self):
        with api_mock() as api:
            api.previews_update = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                [
                    "project",
                    "previews",
                    "enable",
                    "my-bot",
                    "--secret-set",
                    "preview-secrets",
                    "--bot-pull-requests",
                ],
            )
        assert result.exit_code == 0
        assert api.previews_update.await_args.kwargs["bot_pull_requests"] is True

    def test_bot_pull_requests_is_turned_off_when_refused(self):
        with api_mock() as api:
            api.previews_update = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                [
                    "project",
                    "previews",
                    "enable",
                    "my-bot",
                    "--secret-set",
                    "preview-secrets",
                    "--no-bot-pull-requests",
                ],
            )
        assert result.exit_code == 0
        assert api.previews_update.await_args.kwargs["bot_pull_requests"] is False

    def test_disable_clears_the_secret_set(self):
        with api_mock() as api:
            api.previews_update = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "previews", "disable", "my-bot"]
            )
        assert result.exit_code == 0
        assert api.previews_update.await_args.kwargs["secret_set"] is None

    def test_status_when_previews_are_off(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([UNRELATED], None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "previews", "status", "other-bot"]
            )
        assert result.exit_code == 0
        assert "previews are off" in result.output

    def test_status_when_previews_are_on(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "previews", "status", "my-bot"]
            )
        assert result.exit_code == 0
        assert "previews are on" in result.output
        assert "preview-secrets" in result.output
        assert "#7" in result.output


class TestWatchPaths:
    def test_set_sends_every_path(self):
        with api_mock() as api:
            api.watch_paths_update = AsyncMock(
                return_value=({"watchPaths": ["bot/**", "shared/**"]}, None)
            )
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "watch-paths", "set", "my-bot", "bot/**", "shared/**"],
            )
        assert result.exit_code == 0
        assert api.watch_paths_update.await_args.kwargs["watch_paths"] == ["bot/**", "shared/**"]

    def test_clear_sends_an_empty_list(self):
        with api_mock() as api:
            api.watch_paths_update = AsyncMock(return_value=({"watchPaths": []}, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "watch-paths", "clear", "my-bot"]
            )
        assert result.exit_code == 0
        assert api.watch_paths_update.await_args.kwargs["watch_paths"] == []

    def test_show_says_when_there_are_none(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([service("bare", "s-5", git=binding())], None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "watch-paths", "show", "bare"])
        assert result.exit_code == 0
        assert "every change deploys" in result.output

    def test_show_refuses_an_unconnected_project(self):
        with api_mock() as api:
            api.agents = AsyncMock(return_value=([UNRELATED], None))
            result = runner.invoke(
                entrypoint_cli_typer, ["project", "watch-paths", "show", "other-bot"]
            )
        assert result.exit_code == 1
        assert "no GitHub repository" in result.output


class TestStatusCarriesTheProject:
    def test_status_names_the_project_and_environment(self):
        payload = status_payload(
            parentServiceId="s-1", environmentName="staging", git=binding(branch="staging")
        )
        with api_mock() as api:
            api.agent = AsyncMock(return_value=(payload, None))
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "status", "my-bot-staging"])
        assert result.exit_code == 0
        assert "Project: my-bot" in result.output
        assert "Environment: staging" in result.output

    def test_status_shows_watch_paths_and_previews(self):
        payload = status_payload(
            git=binding(previewSecretSet="preview-secrets", watchPaths=["bot/**"])
        )
        with api_mock() as api:
            api.agent = AsyncMock(return_value=(payload, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "status", "my-bot"])
        assert result.exit_code == 0
        assert "Watch Paths: bot/**" in result.output
        assert "PR Previews: on (secrets: preview-secrets)" in result.output
        assert "Preview Bot Pull Requests: no" in result.output

    def test_status_names_the_pull_request_of_a_preview(self):
        payload = status_payload(
            parentServiceId="s-1",
            environmentName="pr-7",
            git=binding(branch="feature", pullRequestNumber=7),
        )
        with api_mock() as api:
            api.agent = AsyncMock(return_value=(payload, None))
            api.agents = AsyncMock(return_value=(ALL_SERVICES, None))
            result = runner.invoke(entrypoint_cli_typer, ["project", "status", "my-bot-pr-7"])
        assert result.exit_code == 0
        assert "Pull Request: #7" in result.output
        assert "PR Previews" not in result.output


@pytest.mark.asyncio
class TestTheRequestsTheApiSends:
    """The new endpoints through the real wrapper, transport mocked and nothing else.

    `create_api_method()` catches every exception and answers `(None, None)`,
    which a command reads as success. A keyword a wrapped method doesn't take
    therefore looks like a clean no-op, and a mocked `API` accepts it happily
    — only going through the real wrapper shows the request that was never
    sent.
    """

    @staticmethod
    def api():
        from pipecatcloud.api import _API

        return _API("fake-token", is_cli=True)

    @staticmethod
    def sent(request) -> tuple[str, str, dict | None]:
        method, url = request.await_args.args
        return method, url, request.await_args.kwargs.get("json")

    async def test_promoting_a_build_posts_the_build_id(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {"name": "my-bot"}
            result, error = await api.agent_update_source(
                agent_name="my-bot", org="test-org", build_id=BUILD_ID
            )
        assert (result, error) == ({"name": "my-bot"}, None)
        method, url, payload = self.sent(request)
        assert method == "POST"
        assert url.endswith("/v1/organizations/test-org/services/my-bot")
        assert payload == {"buildId": BUILD_ID}

    async def test_promoting_an_image_posts_the_image(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await api.agent_update_source(
                agent_name="my-bot", org="test-org", image="docker.io/me/bot:2"
            )
        assert self.sent(request)[2] == {"image": "docker.io/me/bot:2"}

    async def test_linking_an_environment_puts_the_parent_and_name(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await api.environment_link(
                agent_name="my-bot-staging",
                org="test-org",
                parent="my-bot",
                environment_name="staging",
            )
        method, url, payload = self.sent(request)
        assert method == "PUT"
        assert url.endswith("/v1/organizations/test-org/services/my-bot-staging/parent")
        assert payload == {"parent": "my-bot", "environmentName": "staging"}

    async def test_unlinking_an_environment_deletes_the_parent(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await api.environment_unlink(agent_name="my-bot-staging", org="test-org")
        method, url, payload = self.sent(request)
        assert method == "DELETE"
        assert url.endswith("/v1/organizations/test-org/services/my-bot-staging/parent")
        assert payload is None

    async def test_turning_previews_on_puts_the_secret_set(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await api.previews_update(
                agent_name="my-bot",
                org="test-org",
                secret_set="preview-secrets",
                bot_pull_requests=True,
            )
        method, url, payload = self.sent(request)
        assert method == "PUT"
        assert url.endswith("/v1/organizations/test-org/services/my-bot/previews")
        assert payload == {"secretSet": "preview-secrets", "botPullRequests": True}

    async def test_leaving_bot_pull_requests_unsaid_keeps_the_stored_value(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await api.previews_update(
                agent_name="my-bot",
                org="test-org",
                secret_set="preview-secrets",
                bot_pull_requests=None,
            )
        assert self.sent(request)[2] == {"secretSet": "preview-secrets"}

    async def test_turning_previews_off_puts_a_null_secret_set(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {}
            await api.previews_update(
                agent_name="my-bot", org="test-org", secret_set=None, bot_pull_requests=None
            )
        assert self.sent(request)[2] == {"secretSet": None}

    async def test_watch_paths_put_the_whole_list(self):
        api = self.api()
        with patch.object(api, "_base_request", new_callable=AsyncMock) as request:
            request.return_value = {"watchPaths": ["bot/**"]}
            result, _ = await api.watch_paths_update(
                agent_name="my-bot", org="test-org", watch_paths=["bot/**"]
            )
        assert result == {"watchPaths": ["bot/**"]}
        method, url, payload = self.sent(request)
        assert method == "PUT"
        assert url.endswith("/v1/organizations/test-org/services/my-bot/watch-paths")
        assert payload == {"watchPaths": ["bot/**"]}

    async def test_an_api_error_is_returned_rather_than_raised(self):
        api = self.api()
        refusal = {"code": "409", "error": "already an environment"}

        async def refuse(*args, **kwargs):
            # The transport records the refusal on the client, then raises;
            # the wrapper hands the recorded error back to the command.
            api.error = refusal
            raise Exception("409")

        with patch.object(api, "_base_request", side_effect=refuse):
            result, error = await api.bubble_error().environment_link(
                agent_name="my-bot-staging",
                org="test-org",
                parent="my-bot",
                environment_name="staging",
            )
        assert result is None
        assert error == refusal


@pytest.mark.asyncio
class TestPromotionReachesTheApi:
    """`env promote` end to end, with only the transport mocked.

    The regression for the keyword mismatch: the command used to spread the
    HTTP payload's own keys into the wrapper, which raised locally, was
    swallowed, and printed success with nothing deployed.
    """

    async def test_a_build_promotion_posts_it_to_the_project(self):
        from pipecatcloud.cli.api import API

        staging = dict(STAGING, deployment={"buildId": BUILD_ID, "manifest": {"spec": {}}})
        sent: list[tuple[str, str, dict | None]] = []

        async def transport(method, url, **kwargs):
            sent.append((method, url, kwargs.get("json")))
            if method == "GET":
                return {"services": [PROJECT, staging]}
            return {"name": "my-bot"}

        with patch.object(API, "_base_request", side_effect=transport):
            result = runner.invoke(
                entrypoint_cli_typer,
                ["project", "env", "promote", "my-bot", "staging", "--force"],
            )

        assert result.exit_code == 0, result.output
        writes = [(url, payload) for method, url, payload in sent if method == "POST"]
        assert len(writes) == 1
        url, payload = writes[0]
        assert url.endswith("/v1/organizations/test-org/services/my-bot")
        assert payload == {"buildId": BUILD_ID}
        assert f"Promoted build {BUILD_ID}" in result.output
