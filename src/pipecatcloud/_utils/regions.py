#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Region utilities for Pipecat Cloud CLI.

Provides functions to fetch and cache available regions from the API.
"""

from typing import Dict, List, Optional

from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

# Module-level cache for regions
_regions_cache: Optional[List[Dict[str, str]]] = None


async def get_regions() -> List[Dict[str, str]]:
    """
    Fetch available regions from the API with caching.

    This function is cached so the API is only called once per CLI session.

    Returns:
        List of region objects with 'code' and 'display_name' fields
        Example: [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"}
        ]
    """
    global _regions_cache

    if _regions_cache is None:
        org = config.get("org")
        # API returns (data, error) tuple
        data, error = await API.regions(org=org)
        if error or not data:
            return []
        _regions_cache = data

    return _regions_cache


async def get_region_codes() -> List[str]:
    """
    Get list of region codes only.

    Returns:
        List of region codes (e.g., ['us-west', 'eu-central', 'ap-south'])
    """
    regions = await get_regions()
    return [r["code"] for r in regions]


async def validate_region(region: str) -> bool:
    """
    Validate that a region code is supported.

    Args:
        region: Region code to validate

    Returns:
        True if region is valid, False otherwise
    """
    valid_codes = await get_region_codes()
    return region in valid_codes
