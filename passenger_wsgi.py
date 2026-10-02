# Entry point for Phusion Passenger (cPanel "Setup Python App").
# Startup file: passenger_wsgi.py   Entry point: application
#
# Passenger speaks WSGI only, so the ASGI app is wrapped with a2wsgi. HTTP endpoints
# (/api/chat, /api/chat/stream) work; a real WebSocket (/ws) cannot be served here.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["WEBSOCKET_ENABLED"] = "0"  # reported by GET / so the docs page can say so

from a2wsgi import ASGIMiddleware  # noqa: E402

from app import app  # noqa: E402

application = ASGIMiddleware(app)
