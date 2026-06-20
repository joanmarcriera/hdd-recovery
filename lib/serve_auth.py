"""Optional HTTP Basic auth for the review UI (P2), extracted from
bin/image-serve.py (#18). Pure stdlib; no server state. The leading-underscore
names are preserved so existing call sites and tests keep working unchanged.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import os
import urllib.parse

# Health/status probes stay unauthenticated for TrueNAS/Docker.
_WEB_AUTH_EXEMPT_PATHS = {"/health", "/status"}


def _webui_auth_config(environ=None):
    """Return (username, password) for optional web auth, or None when disabled."""
    environ = os.environ if environ is None else environ
    password = environ.get("WEBUI_PASSWORD") or environ.get("TTYD_PASSWORD") or ""
    if not password:
        return None
    username = environ.get("WEBUI_USER") or environ.get("TTYD_USER") or "admin"
    return username, password


def _basic_auth_credentials(header):
    """Parse an HTTP Basic Authorization header into (username, password)."""
    if not header:
        return None
    scheme, sep, token = header.partition(" ")
    if not sep or scheme.lower() != "basic" or not token.strip():
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def _auth_path_exempt(path):
    return urllib.parse.urlparse(path).path in _WEB_AUTH_EXEMPT_PATHS


def _constant_time_equal(left, right):
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _webui_auth_ok(path, auth_header, environ=None):
    config = _webui_auth_config(environ)
    if config is None or _auth_path_exempt(path):
        return True
    credentials = _basic_auth_credentials(auth_header)
    if credentials is None:
        return False
    expected_user, expected_password = config
    username, password = credentials
    return (_constant_time_equal(username, expected_user)
            and _constant_time_equal(password, expected_password))
