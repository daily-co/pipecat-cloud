#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

try:
    from importlib.metadata import version as get_version
    version = get_version("pipecatcloud")
except ImportError:
    # For Python < 3.8
    try:
        from importlib_metadata import version as get_version
        version = get_version("pipecatcloud")
    except ImportError:
        version = "Unknown"
