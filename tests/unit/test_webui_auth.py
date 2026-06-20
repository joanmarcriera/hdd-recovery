"""Unit tests for optional HTTP Basic auth in bin/image-serve.py."""
import base64
import unittest

from _loader import load_module

srv = load_module("bin/image-serve.py")


def _header(username, password):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


class TestWebUiAuth(unittest.TestCase):
    def test_auth_disabled_when_no_password_env_is_set(self):
        self.assertTrue(srv._webui_auth_ok("/sql", None, {}))
        self.assertIsNone(srv._webui_auth_config({}))

    def test_ttyd_password_protects_review_routes(self):
        env = {"TTYD_PASSWORD": "secret"}

        self.assertFalse(srv._webui_auth_ok("/", None, env))
        self.assertFalse(srv._webui_auth_ok("/", _header("admin", "wrong"), env))
        self.assertFalse(srv._webui_auth_ok("/", _header("other", "secret"), env))
        self.assertTrue(srv._webui_auth_ok("/", _header("admin", "secret"), env))

    def test_webui_credentials_override_ttyd_credentials(self):
        env = {
            "TTYD_USER": "admin",
            "TTYD_PASSWORD": "terminal-secret",
            "WEBUI_USER": "review",
            "WEBUI_PASSWORD": "review-secret",
        }

        self.assertFalse(
            srv._webui_auth_ok("/wallets", _header("admin", "terminal-secret"), env)
        )
        self.assertTrue(
            srv._webui_auth_ok("/wallets", _header("review", "review-secret"), env)
        )

    def test_health_and_status_are_exempt(self):
        env = {"TTYD_PASSWORD": "secret"}

        self.assertTrue(srv._webui_auth_ok("/health", None, env))
        self.assertTrue(srv._webui_auth_ok("/health?probe=1", None, env))
        self.assertTrue(srv._webui_auth_ok("/status", None, env))
        self.assertTrue(srv._webui_auth_ok("/status?full=1", None, env))
        self.assertFalse(srv._webui_auth_ok("/api/queue", None, env))

    def test_malformed_authorization_headers_are_rejected(self):
        env = {"WEBUI_PASSWORD": "secret"}
        no_colon = base64.b64encode(b"admin").decode("ascii")

        self.assertFalse(srv._webui_auth_ok("/", "Bearer token", env))
        self.assertFalse(srv._webui_auth_ok("/", "Basic !!!", env))
        self.assertFalse(srv._webui_auth_ok("/", f"Basic {no_colon}", env))


if __name__ == "__main__":
    unittest.main()
