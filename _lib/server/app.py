"""
server/app.py -- Flask application factory for webhook listeners.

Usage:
    from server.app import create_app
    app = create_app()
    app.run(host="0.0.0.0", port=5000)
"""

import logging

from flask import Flask, jsonify

from server.bot_routes import bot_bp
from server.webhooks import engagement_bp


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    app.register_blueprint(engagement_bp)
    app.register_blueprint(bot_bp)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    return app
