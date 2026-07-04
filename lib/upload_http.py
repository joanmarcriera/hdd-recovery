"""Minimal stdlib HTTP helpers for the Immich / Docspell exporters.

Both external services ingest via a ``multipart/form-data`` POST and speak JSON
elsewhere. Rather than add a ``requests``/``immich``/``dsc`` dependency to the
forensics container, the exporters share these tiny ``urllib`` wrappers.

Nothing here is recovery-specific; it only encodes requests and returns
``(status, parsed_body)``.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def _rand_boundary(seed):
    """A deterministic-enough multipart boundary (no RNG — keeps runs testable)."""
    return "----hddrecovery" + str(abs(hash(seed)) % (10 ** 16))


def encode_multipart(fields, files, boundary):
    """Encode form ``fields`` (dict) and ``files`` (list of
    ``(name, filename, content_bytes, content_type)``) as multipart bytes.
    Returns ``(body_bytes, content_type_header)``."""
    crlf = b"\r\n"
    buf = bytearray()
    for name, value in (fields or {}).items():
        buf += b"--" + boundary.encode() + crlf
        buf += f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
        buf += str(value).encode() + crlf
    for name, filename, content, ctype in (files or []):
        buf += b"--" + boundary.encode() + crlf
        buf += (f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"').encode() + crlf
        buf += f"Content-Type: {ctype}".encode() + crlf + crlf
        buf += content + crlf
    buf += b"--" + boundary.encode() + b"--" + crlf
    return bytes(buf), f"multipart/form-data; boundary={boundary}"


def _parse(resp_bytes):
    try:
        return json.loads(resp_bytes.decode("utf-8"))
    except Exception:
        return resp_bytes.decode("utf-8", errors="replace")


def http_json(method, url, headers=None, json_body=None, timeout=60):
    """Perform a JSON request. Returns ``(status_code, parsed_body_or_text)``.
    A network/HTTP error is returned as ``(status_or_0, error_text)`` — callers
    branch on the status rather than catching exceptions."""
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())
    except Exception as e:
        return 0, str(e)


def http_upload_file(url, file_path, field_name, fields, headers=None,
                     timeout=600, content_type="application/octet-stream"):
    """POST a single file plus form ``fields`` as multipart/form-data.
    Returns ``(status_code, parsed_body_or_text)``."""
    with open(file_path, "rb") as fh:
        content = fh.read()
    boundary = _rand_boundary(file_path)
    files = [(field_name, os.path.basename(file_path), content, content_type)]
    body, ctype = encode_multipart(fields, files, boundary)
    hdrs = dict(headers or {})
    hdrs["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())
    except Exception as e:
        return 0, str(e)
