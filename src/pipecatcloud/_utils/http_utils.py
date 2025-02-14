def construct_api_url(path: str) -> str:
    # @ DEPRECATED
    from pipecatcloud.config import config

    if not config.get("server_url", ""):
        raise ValueError("Server URL is not set")

    if not config.get(path, ""):
        raise ValueError(f"Endpoint {path} is not set")

    return f"{config.get('server_url', '')}{config.get(path, '')}"
