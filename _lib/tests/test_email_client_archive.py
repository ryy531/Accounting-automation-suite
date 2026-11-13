"""Tests for custom IMAP report-folder archiving."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.email_client import EmailClient


class FakeImap:
    def __init__(self):
        self.created = []
        self.subscribed = []
        self.selected = []
        self.copied = []
        self.stored = []

    def select(self, folder):
        self.selected.append(folder)
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.search_criteria = criteria
        return "OK", [b"42"]

    def copy(self, sequence_id, folder):
        self.copied.append((sequence_id, folder))
        return "OK", [b"copied"]

    def store(self, sequence_id, operation, flags):
        self.stored.append((sequence_id, operation, flags))
        return "OK", [b"stored"]

    def expunge(self):
        return "OK", [b"42"]

    def list(self):
        return "OK", [b'(\\HasChildren) "." INBOX']

    def create(self, folder):
        self.created.append(folder)
        return "OK", [b"created"]

    def subscribe(self, folder):
        self.subscribed.append(folder)
        return "OK", [b"subscribed"]


def test_move_inbox_message_creates_folder_then_moves_by_message_id():
    client = EmailClient(credentials={"sender_email": "x@example.com"})
    client._imap = FakeImap()

    assert client.move_inbox_message(
        "<daily-report@example.com>",
        "ExampleCo Daily Reports",
        attempts=1,
    )
    assert client._imap.created == ["INBOX.ExampleCo Daily Reports"]
    assert client._imap.subscribed == ["INBOX.ExampleCo Daily Reports"]
    assert client._imap.selected == ["INBOX"]
    assert client._imap.search_criteria == (
        "HEADER",
        "Message-ID",
        "<daily-report@example.com>",
    )
    assert client._imap.copied == [
        (b"42", "INBOX.ExampleCo Daily Reports")
    ]
    assert client._imap.stored == [
        (b"42", "+FLAGS.SILENT", "(\\Deleted)")
    ]
