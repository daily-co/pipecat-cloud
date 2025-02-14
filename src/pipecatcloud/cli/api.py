from pipecatcloud.api import _API
from pipecatcloud.cli.config import config

API = _API(config.get("token"))
