"""Low-level Gmail API OAuth transport for the shared EmailClient."""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from email import message_from_bytes


class GmailApiTransport:
    """Raw Gmail operations authorized by the ``gmail.modify`` scope."""

    API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, credentials: dict):
        self._credentials = credentials
        self._access_token = None
        self._access_token_expires_at = 0.0
        self._label_cache = {}

    def connect(self):
        """Validate the OAuth material and obtain an access token."""
        self._get_access_token()

    def send_raw(self, raw_bytes: bytes) -> dict:
        """Send RFC 822 bytes; Gmail automatically stores the message in Sent."""
        return self._request(
            "POST", "/messages/send", {"raw": self._b64encode(raw_bytes)}
        )

    def fetch_unread_raw(self, folder: str = "INBOX") -> list[tuple[str, bytes]]:
        """Return unread Gmail message IDs and RFC 822 payloads in a label."""
        return self.fetch_raw("is:unread", folder)

    def fetch_raw(self, query: str,
                  folder: str = "INBOX") -> list[tuple[str, bytes]]:
        """Return Gmail message IDs and RFC 822 payloads matching a query."""
        label_id = self._label_id(folder, create=False)
        if label_id is None:
            return []
        refs = []
        page_token = None
        while True:
            params = {"q": query, "labelIds": label_id, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            result = self._request(
                "GET", "/messages?" + urllib.parse.urlencode(params)
            )
            refs.extend(result.get("messages", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        messages = []
        for item in refs:
            message_id = item.get("id")
            if not message_id:
                continue
            result = self._request(
                "GET",
                f"/messages/{urllib.parse.quote(message_id, safe='')}?format=raw",
            )
            raw = self._b64decode(result.get("raw", ""))
            if raw:
                messages.append((message_id, raw))
        return messages

    def add_raw_to_label(self, raw_bytes: bytes, folder: str) -> bool:
        """Label an existing message, or insert it if it is not in Gmail."""
        label_id = self._label_id(folder, create=True)
        rfc822_message_id = message_from_bytes(raw_bytes).get("Message-ID")
        if rfc822_message_id:
            matches = self._search_by_message_id(rfc822_message_id)
            if matches:
                self.add_label(matches[-1]["id"], folder)
                return True
        self._request(
            "POST", "/messages",
            {"raw": self._b64encode(raw_bytes), "labelIds": [label_id]},
        )
        return True

    def add_label(self, gmail_message_id: str, folder: str):
        label_id = self._label_id(folder, create=True)
        return self._modify_message(gmail_message_id, [label_id], [])

    def move_inbox_by_message_id(self, rfc822_message_id: str, folder: str,
                                 attempts: int = 5,
                                 delay_seconds: int = 3) -> bool:
        """Move a delivered INBOX message into a Gmail label."""
        label_id = self._label_id(folder, create=True)
        for attempt in range(attempts):
            matches = self._search_by_message_id(rfc822_message_id)
            if matches:
                self._modify_message(matches[-1]["id"], [label_id], ["INBOX"])
                return True
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
        return False

    def _get_access_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh and self._access_token
            and time.time() < self._access_token_expires_at
        ):
            return self._access_token
        oauth = self._load_oauth_settings()
        form = urllib.parse.urlencode({
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "refresh_token": oauth["refresh_token"],
            "grant_type": "refresh_token",
        }).encode("ascii")
        request = urllib.request.Request(
            oauth["token_uri"], data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode("utf-8"))
                token = result.get("access_token")
                if not token:
                    raise RuntimeError("Google OAuth response omitted access_token.")
                expires_in = max(int(result.get("expires_in", 3600)), 120)
                self._access_token = token
                self._access_token_expires_at = time.time() + expires_in - 60
                return token
            except urllib.error.HTTPError as exc:
                if exc.code not in self.RETRYABLE_STATUS or attempt == 2:
                    raise RuntimeError(
                        f"Google OAuth token request failed (HTTP {exc.code})."
                    ) from exc
            except urllib.error.URLError as exc:
                if attempt == 2:
                    raise RuntimeError("Google OAuth token network error.") from exc
            time.sleep(2 ** attempt)
        raise RuntimeError("Google OAuth token request failed.")

    def _load_oauth_settings(self) -> dict:
        client_path = self._credentials.get("oauth_client_secret_path")
        if not client_path:
            raise ValueError("oauth_client_secret_path is required for gmail_api")
        with open(client_path, encoding="utf-8") as file_handle:
            document = json.load(file_handle)
        client = document.get("installed") or document.get("web")
        if not client:
            raise ValueError("OAuth client JSON must contain installed or web data")
        refresh_token = self._credentials.get("oauth_refresh_token")
        refresh_path = self._credentials.get("oauth_refresh_token_path")
        if not refresh_token and refresh_path:
            with open(refresh_path, encoding="utf-8") as file_handle:
                refresh_token = json.load(file_handle).get("refresh_token")
        if not refresh_token:
            raise ValueError("A Gmail OAuth refresh token is required")
        if not client.get("client_id") or not client.get("client_secret"):
            raise ValueError("OAuth client JSON is missing client credentials")
        return {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh_token,
            "token_uri": client.get("token_uri", self.TOKEN_URI),
        }

    def _request(self, method: str, path: str,
                 body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in range(3):
            token = self._get_access_token(force_refresh=attempt > 0)
            headers = {"Authorization": f"Bearer {token}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(
                self.API_ROOT + path, data=data, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read()
                return json.loads(payload.decode("utf-8")) if payload else {}
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 401 or exc.code in self.RETRYABLE_STATUS
                if not retryable or attempt == 2:
                    raise RuntimeError(
                        f"Gmail API request failed (HTTP {exc.code})."
                    ) from exc
                self._access_token = None
                self._access_token_expires_at = 0.0
            except urllib.error.URLError as exc:
                if attempt == 2:
                    raise RuntimeError("Gmail API network error.") from exc
            time.sleep(2 ** attempt)
        raise RuntimeError("Gmail API request failed.")

    def _label_id(self, folder: str, create: bool) -> str | None:
        normalized = folder.strip()
        system = {"INBOX", "SENT", "DRAFT", "TRASH", "SPAM",
                  "STARRED", "IMPORTANT"}
        if normalized.upper() in system:
            return normalized.upper()
        if normalized in self._label_cache:
            return self._label_cache[normalized]
        result = self._request("GET", "/labels")
        for label in result.get("labels", []):
            if label.get("name", "").casefold() == normalized.casefold():
                self._label_cache[normalized] = label["id"]
                return label["id"]
        if not create:
            return None
        result = self._request(
            "POST", "/labels",
            {"name": normalized, "labelListVisibility": "labelShow",
             "messageListVisibility": "show"},
        )
        label_id = result.get("id")
        if not label_id:
            raise RuntimeError("Gmail API did not return a label ID.")
        self._label_cache[normalized] = label_id
        return label_id

    def _search_by_message_id(self, message_id: str) -> list[dict]:
        result = self._request(
            "GET", "/messages?" + urllib.parse.urlencode({
                "q": f"rfc822msgid:{message_id}", "maxResults": 10
            })
        )
        return result.get("messages", [])

    def _modify_message(self, message_id: str,
                        add_label_ids: list[str], remove_label_ids: list[str]):
        return self._request(
            "POST",
            f"/messages/{urllib.parse.quote(message_id, safe='')}/modify",
            {"addLabelIds": add_label_ids, "removeLabelIds": remove_label_ids},
        )

    @staticmethod
    def _b64encode(raw_bytes: bytes) -> str:
        return base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        if not value:
            return b""
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
