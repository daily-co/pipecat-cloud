from pathlib import Path

import toml

pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
version = toml.load(pyproject_path)["project"]["version"]
