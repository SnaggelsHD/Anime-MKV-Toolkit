"""Manual HTTP Range support for serving local files.

Starlette's FileResponse doesn't honor Range headers, and browsers rely on
Range requests to seek within a <video> - without this, scrubbing the
preview player would require downloading the whole file first.
"""
import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

_CHUNK_SIZE = 256 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _parse_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        # suffix range: last N bytes
        length = int(end_s)
        start = max(0, file_size - length)
        end = file_size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s != "" else file_size - 1
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        return None
    return start, end


def _iter_file_range(path: Path, start: int, end: int):
    remaining = end - start + 1
    with open(path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def serve_file_with_ranges(request: Request, path: Path, media_type: str) -> Response:
    """Serve `path`, honoring a Range request header if present so <video>
    seeking works. Falls back to a normal full-file streaming response for
    a plain GET or a Range this server can't satisfy."""
    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        parsed = _parse_range(range_header, file_size)
        if parsed is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        start, end = parsed
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
        }
        return StreamingResponse(
            _iter_file_range(path, start, end), status_code=206, media_type=media_type, headers=headers,
        )

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size)}
    return StreamingResponse(
        _iter_file_range(path, 0, file_size - 1), status_code=200, media_type=media_type, headers=headers,
    )
