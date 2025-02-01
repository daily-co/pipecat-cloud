def handle_agent_start_error(response_code: int) -> str:
    if response_code == 404:
        return "PCC-1005"
    elif response_code == 400:
        return "PCC-1004"
    elif response_code == 401:
        return "PCC-1006"
    else:
        return "PCC-1000"
