"""Small bounded HTTP readers shared by external capability clients."""

import json
from collections.abc import AsyncIterator

import httpx


class InvalidUpstreamResponse(ValueError):
    pass


async def read_json(response: httpx.Response, *, max_bytes: int) -> object:
    length = response.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            raise InvalidUpstreamResponse("invalid response size") from None
        if size < 0 or size > max_bytes:
            raise InvalidUpstreamResponse("oversized response")
    chunks: list[bytes] = []
    size = 0
    # Callers must open a streamed response: checking after get()/post() has
    # already buffered the body would not bound memory.
    async for chunk in response.aiter_bytes(chunk_size=4096):
        size += len(chunk)
        if size > max_bytes:
            raise InvalidUpstreamResponse("oversized response")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise InvalidUpstreamResponse("invalid JSON") from None


async def sse_lines(
    response: httpx.Response, *, max_bytes: int, max_line_bytes: int
) -> AsyncIterator[str]:
    """Bound bytes before line decoding, including unterminated lines/comments."""
    size = 0
    line = bytearray()
    after_cr = False
    try:
        async for chunk in response.aiter_bytes(chunk_size=4096):
            size += len(chunk)
            if size > max_bytes:
                raise InvalidUpstreamResponse("oversized research stream")
            for byte in chunk:
                if after_cr and byte == 10:
                    after_cr = False
                    continue
                after_cr = False
                if byte in (10, 13):
                    yield line.decode("utf-8")
                    line.clear()
                    after_cr = byte == 13
                else:
                    line.append(byte)
                    if len(line) > max_line_bytes:
                        raise InvalidUpstreamResponse("oversized research stream")
        if line:
            yield line.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidUpstreamResponse("invalid research stream encoding") from None
