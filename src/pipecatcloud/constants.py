#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Constants for Pipecat Cloud CLI.

IMPORTANT: These values must be kept in sync with API configuration.
When adding new models or options, update both the API ConfigMap and these constants.
"""

from typing import Literal, get_args

# Krisp VIVA audio filter models
# These must match the keys in the API's KRISP_AUDIO_FILTER_MODELS ConfigMap
# Location: pipecat-cloud-sandbox/api/helm/chart/values.yaml -> krispConfig.audioFilterModels
KrispVivaAudioFilter = Literal["tel", "pro"]

# Derive runtime list from the Literal type for validation
KRISP_VIVA_MODELS = list(get_args(KrispVivaAudioFilter))

# Regions
# Region type alias for CLI parameters
# Valid region codes are fetched dynamically from the API
# Use pipecatcloud._utils.regions.get_regions() to fetch available regions
# Use pipecatcloud._utils.regions.validate_region() to validate user input
Region = str
