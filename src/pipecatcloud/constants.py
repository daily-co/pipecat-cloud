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

# Krisp VIVA audio filter models
# These must match the keys in the API's KRISP_AUDIO_FILTER_MODELS ConfigMap
# Location: pipecat-cloud-sandbox/api/helm/chart/values.yaml -> krispConfig.audioFilterModels
KRISP_VIVA_MODELS = ["tel", "pro"]
