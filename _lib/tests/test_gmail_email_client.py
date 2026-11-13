"""Offline tests for Gmail OAuth transport and EmailClient routing."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.email_client import EmailClient
from connectors.gmail_api_transport import GmailApiTransport


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class GmailApiTransportTests(unittest.TestCase):
    def test_send_exchanges_refresh_token_without_exposing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            client_path = os.path.join(directory, "client.json")
            token_path = os.path.join(directory, "token.json")
            with open(client_path, "w", encoding="utf-8") as file_handle:
                json.dump({"installed": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "token_uri": "https://oauth.example/token",
                }}, file_handle)
            with open(token_path, "w", encoding="utf-8") as file_handle:
                json.dump({"refresh_token": "refresh-secret"}, file_handle)

            transport = GmailApiTransport({
                "oauth_client_secret_path": client_path,
                "oauth_refresh_token_path": token_path,
            })
            responses = [
                FakeResponse({"access_token": "access-token", "expires_in": 3600}),
                FakeResponse({"id": "gmail-message-id"}),
            ]
            with patch(
                "connectors.gmail_api_transport.urllib.request.urlopen",
                side_effect=responses,
            ) as urlopen:
                result = transport.send_raw(b"Subject: Test\r\n\r\nBody")

            self.assertEqual(result["id"], "gmail-message-id")
            token_request = urlopen.call_args_list[0].args[0]
            token_form = dict(
                urllib_pair.split("=", 1)
                for urllib_pair in token_request.data.decode("ascii").split("&")
            )
            self.assertEqual(token_form["refresh_token"], "refresh-secret")
            gmail_request = urlopen.call_args_list[1].args[0]
            self.assertEqual(
                gmail_request.get_header("Authorization"),
                "Bearer access-token",
            )

    def test_fetch_unread_paginates_and_decodes_raw_messages(self):
        transport = GmailApiTransport({})
        raw_a = b"Subject: A\r\n\r\nOne"
        raw_b = b"Subject: B\r\n\r\nTwo"
        transport._request = Mock(side_effect=[
            {"messages": [{"id": "a"}], "nextPageToken": "next"},
            {"messages": [{"id": "b"}]},
            {"raw": transport._b64encode(raw_a)},
            {"raw": transport._b64encode(raw_b)},
        ])

        self.assertEqual(
            transport.fetch_unread_raw("INBOX"),
            [("a", raw_a), ("b", raw_b)],
        )


class EmailClientRoutingTests(unittest.TestCase):
    def setUp(self):
        self.base_credentials = {
            "sender_name": "ExampleCo Test",
            "sender_email": "infosk@example-firm.com",
            "sender_password": "password",
            "smtp_host": "smtp.example.test",
            "smtp_port": 465,
        }

    def test_password_mode_remains_default(self):
        client = EmailClient(credentials=self.base_credentials)
        with patch("connectors.email_client.smtplib.SMTP_SSL") as smtp:
            client.send_plain("test@example.com", "Subject", "Body")
        server = smtp.return_value.__enter__.return_value
        server.login.assert_called_once_with("infosk@example-firm.com", "password")
        server.sendmail.assert_called_once()

    def test_gmail_mode_routes_raw_message_and_archive_label(self):
        client = EmailClient(credentials={
            **self.base_credentials,
            "auth_mode": "gmail_api",
        })
        client._gmail.send_raw = Mock(return_value={"id": "gmail-id"})
        client._gmail.add_label = Mock()

        client.send_plain(
            "test@example.com",
            "Subject",
            "Body",
            archive_folder="ExampleCo Reports",
        )

        client._gmail.send_raw.assert_called_once()
        client._gmail.add_label.assert_called_once_with(
            "gmail-id", "ExampleCo Reports"
        )


if __name__ == "__main__":
    unittest.main()
