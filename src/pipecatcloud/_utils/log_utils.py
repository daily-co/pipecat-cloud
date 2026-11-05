#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
What a log line may say about a request, a response or a failure.

Request and response bodies can hold secret values and credentials, under any name
and at any depth, and an exception's text can quote them. So a log line says how
much is there and what kind of thing it is, and never quotes any of it. Headers are
not logged at all: they carry the token.
"""


def describe_url(url: str) -> str:
    """A URL without its query string, whose values are not for a log."""
    return url.split("?", 1)[0]


def describe_body(body: object) -> str:
    """How much a request or response body holds, and never what."""
    if body is None:
        return "none"
    if isinstance(body, dict | list | tuple):
        return f"{type(body).__name__} of {len(body)}"
    return type(body).__name__


def describe_exception(exc: BaseException) -> str:
    """An exception's type, and its HTTP status if it has one, without its text."""
    status = getattr(exc, "status", None)
    return (
        f"{type(exc).__name__} (status {status})" if isinstance(status, int) else type(exc).__name__
    )


def describe_api_error(error: object) -> str:
    """The code of an error body from the API, without its text."""
    code = error.get("code") if isinstance(error, dict) else None
    return f"code {code}" if code else "no code"
