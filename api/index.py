"""Vercel entry point: exposes the Flask `app` (WSGI)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as application

app = application # noqa: E402,F401
