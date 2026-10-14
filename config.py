"""Configuration for the Ashby Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT         port the API listens on (default 8000)
    ASHBY_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
                 (default: none — direct). Ashby's public job board APIs have
                 no bot protection and never blocked us, so you very likely
                 don't need this. Set it only if you start seeing failures at
                 high volume.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

ASHBY_PROXY = os.environ.get("ASHBY_PROXY") or None
ASHBY_BLOCK_COOLDOWN = 900


def ashby_proxy():
    """Proxy for every request (None = direct)."""
    return os.environ.get("ASHBY_PROXY") or None


def ashby_fallback_proxy():
    """Proxy to switch to after a block (None = keep the current route)."""
    return None
