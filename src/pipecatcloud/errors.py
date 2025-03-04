ERROR_CODES = {
    "401": "Unauthorized request to API. Please run `pcc auth login` to login again.\nIf you are attempting to start an agent, please ensure the public API key used as part of the request is valid.",
    "404": "API endpoint not found / namespace or organization not found.",
    "PCC-1000": "Unable to start agent.",
    "PCC-1001": "Attempt to start agent when deployment is not in ready state",
    "PCC-1002": "Attempt to start agent without public api key. Try running `pcc organizations keys use`.",
    "PCC-1003": "Unknown error occurred. Please check logs for more information.",
    "PCC-1004": "Billing credentials not set. Please set billing credentials via the Pipecat Cloud dashboard.",
    "PCC-1005": "Agent deployment with name not found.",
    "PCC-1006": "Not authorized / invalid API key.",
}
