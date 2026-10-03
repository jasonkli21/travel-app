from urllib.parse import urlsplit


def validate_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    if value != value.strip() or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("URL must be an HTTP or HTTPS URL without credentials")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("URL must be an HTTP or HTTPS URL without credentials") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65535)
        or "\\" in value
    ):
        raise ValueError("URL must be an HTTP or HTTPS URL without credentials")
    return value
