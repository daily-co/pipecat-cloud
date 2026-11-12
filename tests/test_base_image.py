"""The warning for a Dockerfile that builds FROM dailyco/pipecat-base:latest."""

from unittest.mock import patch

import pytest

from pipecatcloud._utils.base_image import latest_base_images, warn_latest_base_image


@pytest.mark.parametrize(
    "image",
    [
        "dailyco/pipecat-base:latest",
        "dailyco/pipecat-base",
        "docker.io/dailyco/pipecat-base:latest",
        "index.docker.io/dailyco/pipecat-base",
        "registry-1.docker.io/dailyco/pipecat-base:latest",
    ],
)
def test_latest_is_found(image):
    assert latest_base_images(f"FROM {image}\n") == [(1, image)]


@pytest.mark.parametrize(
    "image",
    [
        "dailyco/pipecat-base:latest-py3.14",
        "dailyco/pipecat-base:latest-py3.12",
        "dailyco/pipecat-base:0.3.0-py3.12",
        # Deprecated too, but fixed to one image that stays published.
        "dailyco/pipecat-base:0.3.0",
        "dailyco/pipecat-base@sha256:" + "0" * 64,
        "dailyco/pipecat-base:latest@sha256:" + "0" * 64,
        "dailyco/pipecat-base-other:latest",
        "example.com/dailyco/pipecat-base:latest",
        "python:3.12-slim",
        "dailyco/pipecat-base:${TAG}",
        "builder",
    ],
)
def test_others_are_not(image):
    assert latest_base_images(f"FROM {image}\n") == []


def test_reads_each_from_with_its_line():
    text = (
        "# syntax=docker/dockerfile:1\n"
        "FROM python:3.12-slim AS builder\n"
        "RUN echo build\n"
        "\n"
        "from --platform=linux/arm64 dailyco/pipecat-base AS final\n"
        "COPY --from=builder /out /out\n"
        "  FROM dailyco/pipecat-base:latest\n"
    )
    assert latest_base_images(text) == [
        (5, "dailyco/pipecat-base"),
        (7, "dailyco/pipecat-base:latest"),
    ]


def test_joins_continuation_lines():
    text = "FROM \\\n  --platform=linux/arm64 \\\n# a comment\n  dailyco/pipecat-base:latest\n"
    assert latest_base_images(text) == [(1, "dailyco/pipecat-base:latest")]


def test_skips_comments_and_other_instructions():
    text = (
        "# FROM dailyco/pipecat-base:latest\n"
        "FROM dailyco/pipecat-base:latest-py3.14\n"
        "RUN echo FROM dailyco/pipecat-base:latest\n"
    )
    assert latest_base_images(text) == []


def test_warns_with_the_line_and_the_fix(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM dailyco/pipecat-base:latest\nCOPY bot.py bot.py\n")
    with patch("pipecatcloud._utils.base_image.console") as console:
        warn_latest_base_image(dockerfile)
    console.print.assert_called_once()
    message = console.print.call_args.args[0]
    assert "Dockerfile line 1 builds FROM dailyco/pipecat-base:latest" in message
    assert "stops building on 2027-04-05" in message
    assert "dailyco/pipecat-base:latest-py3.12" in message


def test_is_silent_for_a_named_python(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM dailyco/pipecat-base:latest-py3.14\n")
    with patch("pipecatcloud._utils.base_image.console") as console:
        warn_latest_base_image(dockerfile)
    console.print.assert_not_called()


def test_leaves_a_missing_dockerfile_to_the_build(tmp_path):
    with patch("pipecatcloud._utils.base_image.console") as console:
        warn_latest_base_image(tmp_path / "Dockerfile")
    console.print.assert_not_called()
