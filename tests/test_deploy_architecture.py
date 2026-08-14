"""Architecture selection on deploy (PCC-1105): config validation, the
omit-preserving payload field, and region-capability pre-validation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams
from pipecatcloud._utils.regions import validate_architecture_for_region


def test_config_accepts_valid_architecture():
    cfg = DeployConfigParams(architecture="arm64")
    assert cfg.to_dict()["architecture"] == "arm64"


def test_config_defaults_to_no_architecture():
    assert DeployConfigParams().to_dict()["architecture"] is None


def test_config_rejects_invalid_architecture():
    # kubernetes.io/arch vocabulary only — never arm/amd.
    with pytest.raises(ValueError, match="amd64"):
        DeployConfigParams(architecture="arm")


REGIONS = [
    {"code": "us-west", "display_name": "US West"},  # old API: no capability
    {
        "code": "ms-dev",
        "display_name": "MS Dev",
        "supported_architectures": ["amd64", "arm64"],
        "default_architecture": "amd64",
    },
    {
        "code": "cloud-arm",
        "display_name": "Cloud",
        "supported_architectures": ["arm64"],
        "default_architecture": "arm64",
    },
]


def test_prevalidation_passes_supported_choice():
    assert validate_architecture_for_region("arm64", "ms-dev", REGIONS) is None


def test_prevalidation_rejects_unsupported_choice():
    error = validate_architecture_for_region("amd64", "cloud-arm", REGIONS)
    assert error is not None
    assert "amd64" in error and "cloud-arm" in error and "arm64" in error


def test_prevalidation_defers_when_capability_absent():
    # Older API payloads carry no capability — the server's own 400 decides.
    assert validate_architecture_for_region("amd64", "us-west", REGIONS) is None


def test_prevalidation_defers_for_unknown_region():
    assert validate_architecture_for_region("amd64", "ghost", REGIONS) is None
