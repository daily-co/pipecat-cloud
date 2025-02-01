from loguru import logger


def load_deploy_config_file() -> dict | None:
    import os

    import toml

    from pipecatcloud.config import deploy_config_path

    logger.info(f"Deploy config path: {deploy_config_path}")
    logger.info(f"Deploy config path exists: {os.path.exists(deploy_config_path)}")

    try:
        with open(deploy_config_path, "r") as f:
            return toml.load(f)
    except Exception:
        return None
