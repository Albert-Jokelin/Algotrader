"""Upstox OAuth 2.0 login flow.

Handles the once-per-day authentication required by Upstox:
  1. Build the authorization URL.
  2. Open the user's default browser.
  3. Start a local HTTP server (localhost:8765) to capture the callback code.
  4. Exchange the auth code for an access token.
  5. Persist the token to ~/.algotrader/token.json with mode 0o600.

Usage
-----
    python -m algotrader login

Or programmatically:
    oauth = UpstoxOAuth(client_id="…", client_secret="…")
    access_token = oauth.login()

Edge constraints
----------------
* Upstox tokens expire at 06:00 IST the following morning — not on a fixed
  TTL.  The expiry is stored in the JSON file so stale tokens are detected
  on next load without an API call.
* The callback server runs on localhost only; no internet exposure.
* Token file permissions are set to 0o600 (owner read/write only).
* If the user does not complete auth within AUTH_TIMEOUT_SECS, login() raises
  OAuthError — the caller should surface this clearly.
* Constant-time comparison is not needed here (this is a local server, not an
  API endpoint; CSRF is out of scope for a desktop OAuth flow).
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_DEFAULT_TOKEN_PATH = os.path.expanduser("~/.algotrader/token.json")
_CALLBACK_PORT = 8765
_AUTH_TIMEOUT_SECS = 300  # 5 minutes


class OAuthError(Exception):
    """Raised when the OAuth flow fails for any reason."""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the Upstox auth code from the callback URL."""

    captured_code: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.captured_code = (params.get("code") or [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body>"
            b"<h2>Upstox login successful!</h2>"
            b"<p>You can close this browser tab and return to the terminal.</p>"
            b"</body></html>"
        )

    def log_message(self, *args: Any) -> None:  # noqa: ARG002
        pass  # Suppress default HTTP access log


class UpstoxOAuth:
    """OAuth 2.0 authentication for the Upstox API."""

    _AUTH_DIALOG_URL = (
        "https://api.upstox.com/v2/login/authorization/dialog"
    )
    _TOKEN_EXCHANGE_URL = (
        "https://api.upstox.com/v2/login/authorization/token"
    )

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = f"http://localhost:{_CALLBACK_PORT}/callback",
        token_path: str = _DEFAULT_TOKEN_PATH,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_path = token_path

    # ── Public API ─────────────────────────────────────────────────────────────

    def login(self) -> str:
        """Run the full interactive OAuth flow and return the access token.

        Opens the browser, waits for the callback, exchanges the auth code,
        saves the token to disk, and returns the access_token string.

        Raises:
            OAuthError on timeout, cancellation, or API failure.
        """
        auth_url = self.build_auth_url()
        print(f"\nOpening browser for Upstox login…")
        print(f"If the browser does not open automatically, visit:\n  {auth_url}\n")
        webbrowser.open(auth_url)

        code = self._wait_for_callback(timeout=_AUTH_TIMEOUT_SECS)
        if not code:
            raise OAuthError(
                "Authentication timed out after "
                f"{_AUTH_TIMEOUT_SECS}s.  Please try again."
            )

        token_data = self._exchange_code(code)
        self.save_token(token_data)
        access_token = token_data["access_token"]
        print(f"Login successful.  Token saved to: {self._token_path}")
        return access_token

    def build_auth_url(self) -> str:
        """Return the Upstox authorization dialog URL."""
        params = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id":     self._client_id,
                "redirect_uri":  self._redirect_uri,
            }
        )
        return f"{self._AUTH_DIALOG_URL}?{params}"

    def save_token(self, token_data: Dict[str, Any]) -> None:
        """Write token data to the token file with 0o600 permissions."""
        os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
        with open(self._token_path, "w") as fh:
            json.dump(token_data, fh, indent=2)
        os.chmod(self._token_path, 0o600)
        log.info("Token persisted to %s", self._token_path)

    def load_token(self) -> Optional[Dict[str, Any]]:
        """Load the saved token; return None if missing or expired."""
        if not os.path.exists(self._token_path):
            return None
        try:
            with open(self._token_path) as fh:
                data = json.load(fh)

            expires_at = data.get("expires_at")
            if expires_at:
                exp = datetime.fromisoformat(expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) >= exp:
                    log.warning("Saved token expired at %s; re-login required", exp)
                    return None

            return data
        except Exception as exc:
            log.warning("Failed to read token file: %s", exc)
            return None

    def access_token_from_file(self) -> Optional[str]:
        """Return the stored access_token string, or None if absent/expired."""
        data = self.load_token()
        return data.get("access_token") if data else None

    # ── Internals ──────────────────────────────────────────────────────────────

    def _wait_for_callback(self, timeout: float) -> Optional[str]:
        """Start a local HTTP server and block until the OAuth callback arrives."""
        _CallbackHandler.captured_code = None
        server = http.server.HTTPServer(
            ("localhost", _CALLBACK_PORT), _CallbackHandler
        )
        server.timeout = 1.0  # handle_request() blocks at most 1s
        deadline = time.time() + timeout

        while time.time() < deadline:
            server.handle_request()
            if _CallbackHandler.captured_code:
                server.server_close()
                return _CallbackHandler.captured_code

        server.server_close()
        return None

    def _exchange_code(self, code: str) -> Dict[str, Any]:
        """POST the auth code to Upstox and return the token JSON."""
        payload = urllib.parse.urlencode(
            {
                "code":          code,
                "client_id":     self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri":  self._redirect_uri,
                "grant_type":    "authorization_code",
            }
        ).encode()

        req = urllib.request.Request(
            self._TOKEN_EXCHANGE_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body: Dict[str, Any] = json.loads(resp.read())
        except Exception as exc:
            raise OAuthError(f"Token exchange request failed: {exc}") from exc

        if "access_token" not in body:
            raise OAuthError(
                f"Upstox token exchange returned no access_token: {body}"
            )

        # Upstox tokens expire at 06:00 IST the following morning.
        # Pre-compute and store the expiry so load_token() can verify offline.
        try:
            import zoneinfo
            IST = zoneinfo.ZoneInfo("Asia/Kolkata")
        except ImportError:
            import pytz  # type: ignore[import]
            IST = pytz.timezone("Asia/Kolkata")

        now_ist = datetime.now(IST)
        next_6am = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
        if now_ist.hour >= 6:
            next_6am += timedelta(days=1)
        body["expires_at"] = next_6am.isoformat()

        return body
