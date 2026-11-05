#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Region utilities for Pipecat Cloud CLI.

Provides functions to fetch and cache available regions from the API.
"""

from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

# Module-level cache of region lists, keyed by organization. Each organization
# has its own list (a self-hosted region is visible only to its organization),
# so one list must never answer for another.
_regions_cache: dict[str, list[dict[str, str]]] | None = None


async def get_regions(org: str | None = None) -> list[dict[str, str]]:
    """
    Fetch available regions for an organization from the API with caching.

    The list is cached per organization, so the API is called once per
    organization per CLI session.

    Args:
        org: Organization to list regions for. Defaults to the organization in
            the config file.

    Returns:
        List of region objects with 'code' and 'display_name' fields, or an
        empty list when the list cannot be fetched.
        Example: [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"}
        ]
    """
    global _regions_cache

    org = org or config.get("org")
    if _regions_cache is None:
        _regions_cache = {}

    if org not in _regions_cache:
        # API returns (data, error) tuple
        data, error = await API.regions(org=org)
        if error or not data:
            return []
        _regions_cache[org] = data

    return _regions_cache[org]


async def region_error(region: str, org: str | None = None) -> str | None:
    """
    Check a region code against the regions of the organization in use.

    Fetches the list once. Returns None when the region is valid, otherwise a
    message for the user. When the list cannot be fetched, the message says so
    rather than calling the region invalid. The API client prints its own error
    for the failed call before this message is returned (in json mode that error
    goes to stdout as the error object), so the caller shows both: why the call
    failed, then that the command stopped.
    """
    org = org or config.get("org")
    regions = await get_regions(org)
    if not regions:
        return (
            f"Could not list the regions of organization '{org}', so region "
            f"'{region}' could not be checked. Not continuing."
        )
    codes = [r["code"] for r in regions]
    if region in codes:
        return None
    return f"Invalid region '{region}'. Valid regions are: {', '.join(codes)}"


def validate_architecture_for_region(
    architecture: str, region: str, regions: list[dict]
) -> str | None:
    """Pre-validate an architecture choice against a region's capability
    (PCC-1105). Returns an error message, or None when the choice is valid
    or cannot be checked locally.

    The regions payload carries ``supported_architectures`` only on newer
    APIs — when absent, defer to the server's own validation (its 400 names
    the supported list).
    """
    entry = next((r for r in regions if r.get("code") == region), None)
    supported = (entry or {}).get("supported_architectures")
    if not supported or architecture in supported:
        return None
    return (
        f"Architecture '{architecture}' is not supported in region '{region}' "
        f"(supported: {', '.join(supported)})"
    )
