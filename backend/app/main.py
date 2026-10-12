"""Entry point kept for `uvicorn app.main:app`: the K Electronics system's app."""

from .systems.k_electronics.app import app

__all__ = ["app"]
