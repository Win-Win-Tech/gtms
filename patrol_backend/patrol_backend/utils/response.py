# utils/response.py

def api_response(status="success", message="", data=None, status_code=200):
    """
    Standardized API response format.

    Args:
        status (str): "success" or "error"
        message (str): Description of the response
        data (dict or list): Payload data
        status_code (int): HTTP status code

    Returns:
        dict: Formatted response dictionary
    """
    return {
        "status": status,
        "message": message,
        "data": data if data is not None else {},
        "status_code": status_code
    }
