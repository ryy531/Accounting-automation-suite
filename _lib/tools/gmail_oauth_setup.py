"""One-time Gmail OAuth setup for the shared EmailClient.

The tool requests only ``gmail.modify`` and stores the refresh token separately
from the downloaded OAuth client JSON. It never sends or reads mailbox data.
"""

import argparse
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
SECRET_DIR = Path(__file__).resolve().parents[1] / "_secrets" / "gmail"
DEFAULT_CLIENT_PATH = SECRET_DIR / "google_oauth_client_secret.json"
DEFAULT_TOKEN_PATH = SECRET_DIR / "gmail_refresh_token.json"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Capture one localhost OAuth callback without request logging."""

    result = None
    expected_state = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        state = query.get("state", [None])[0]
        error = query.get("error", [None])[0]
        code = query.get("code", [None])[0]
        if state != self.expected_state:
            self.__class__.result = {"error": "OAuth state mismatch"}
        elif error:
            self.__class__.result = {"error": f"Google authorization: {error}"}
        elif not code:
            self.__class__.result = {"error": "Authorization code missing"}
        else:
            self.__class__.result = {"code": code}

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<h2>ExampleCo Gmail authorization received.</h2>"
            "<p>You may close this tab and return to the terminal.</p>".encode()
        )

    def log_message(self, format, *args):
        return


def _load_client(path: Path) -> dict:
    with path.open(encoding="utf-8") as file_handle:
        document = json.load(file_handle)
    client = document.get("installed") or document.get("web")
    if not client or not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("OAuth client JSON is missing installed client data")
    return client


def _exchange_code(client: dict, code: str, redirect_uri: str) -> dict:
    form = urllib.parse.urlencode({
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode("ascii")
    request = urllib.request.Request(
        client.get("token_uri", DEFAULT_TOKEN_URI),
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Google OAuth token exchange failed (HTTP {exc.code})."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Google OAuth token exchange network error.") from exc


def authorize(client_path: Path, token_path: Path,
              open_browser: bool = True, timeout: int = 300):
    """Run the loopback authorization flow and write a refresh-token file."""
    client = _load_client(client_path)
    state = secrets.token_urlsafe(32)
    OAuthCallbackHandler.expected_state = state
    OAuthCallbackHandler.result = None

    server = HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
    server.timeout = timeout
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    auth_url = AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_MODIFY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })

    print("Open this Google authorization URL in the infosk account:")
    print(auth_url)
    if open_browser:
        webbrowser.open(auth_url)
    server.handle_request()
    server.server_close()

    result = OAuthCallbackHandler.result
    if not result:
        raise TimeoutError(f"No OAuth callback received within {timeout} seconds")
    if result.get("error"):
        raise RuntimeError(result["error"])

    tokens = _exchange_code(client, result["code"], redirect_uri)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token; revoke the app grant and retry."
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = token_path.with_suffix(token_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps({
            "refresh_token": refresh_token,
            "scope": tokens.get("scope", GMAIL_MODIFY_SCOPE),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, token_path)
    print(f"Refresh token saved to: {token_path}")
    print("Restrict this file to the Windows account that runs scheduled tasks.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the Gmail API refresh token used by ExampleCo scripts."
    )
    parser.add_argument("--client", type=Path, default=DEFAULT_CLIENT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing refresh-token file.",
    )
    args = parser.parse_args()

    if not args.client.is_file():
        parser.error(f"OAuth client JSON not found: {args.client}")
    if args.output.exists() and not args.force:
        parser.error(f"Token file already exists: {args.output}; use --force")
    authorize(
        args.client,
        args.output,
        open_browser=not args.no_browser,
        timeout=max(args.timeout, 30),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
