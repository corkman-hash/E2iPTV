"""
IE2iPTVv3 Backend

Public entry point for the IPTV backend.
The UI should import Backend from this package rather than importing
individual backend modules.
"""

from .backend import Backend

__all__ = ["Backend"]
