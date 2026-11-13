"""
Entry point for the NocoDB webhook server.

Usage (from _lib directory):
    python -m server.run_server

Or directly:
    python server\\run_server.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)
