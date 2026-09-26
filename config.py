"""Configuration for the Google Lens Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT                 port the API listens on (default 8000)

    CAPSOLVER_API_KEY    optional. Google sometimes answers a fresh browser with
                         an "unusual traffic" captcha instead of results. The
                         scraper first tries without solving anything (about
                         half of fresh sessions get results straight away).
                         When every attempt hits the captcha, set a CapSolver
                         key and it is solved automatically (one captcha per
                         ~30 searches, at CapSolver's normal price):
                         https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm

    GOOGLE_LENS_PROXY    optional proxy, e.g. http://user:pass@host:port.
                         Without it your own IP is used, which Google pauses
                         after a few dozen Lens searches. For volume use a
                         STICKY residential proxy (each browser session must
                         keep one IP):
                           - put `{session}` where your provider takes a session
                             id, e.g. http://user-session-{session}:pass@host:port
                             (a fresh random id per session), and optionally
                           - put `{country}` where it takes a country code, or
                           - list several sticky URLs, comma-separated (one is
                             picked at random per session).
                         It is also the last resort for picture downloads that
                         your own connection is refused.

Image to Text (OCR) and Translate talk to Google's open Lens API over plain
HTTP and need none of this.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os
import random
import string

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every plain-HTTP request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

# --- captcha solving --------------------------------------------------------
CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "").strip()
# Our CapSolver developer app id: CapSolver pays us a small commission on the
# captchas you solve, at no extra cost to you (you pay CapSolver's normal price).
CAPSOLVER_APP_ID = "DC601421-43D5-45E4-9FDB-B3BAF7A2C3FD"

# --- proxies ----------------------------------------------------------------
GOOGLE_LENS_PROXY = os.environ.get("GOOGLE_LENS_PROXY", "").strip()


def _pick_proxy(country="us"):
    """One proxy URL from GOOGLE_LENS_PROXY ({session} / {country} filled in), or None."""
    choices = [p.strip() for p in GOOGLE_LENS_PROXY.split(",") if p.strip()]
    if not choices:
        return None
    session = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return random.choice(choices).replace("{session}", session).replace("{country}", (country or "us").lower())


def google_lens_proxy(country=None):
    """Exit for a picture download (or a Lens API call) your own connection
    was refused for. None = no proxy configured."""
    return _pick_proxy(country or "us")


def google_search_mint_proxy():
    """The exit one Google browser session (identity) runs on; its Lens
    result pages go through the same exit. None = your own connection."""
    return _pick_proxy("us")


def google_search_proxy():
    """Exit for plain-HTTP Google requests. None = direct, which works."""
    return None


def google_search_fallback_proxy():
    """Where a plain-HTTP thread moves after Google refuses the direct
    connection (403 / 429). None = keep retrying direct."""
    return _pick_proxy("us")


GOOGLE_SEARCH_FALLBACK_PROXY_COUNTRY = None
GOOGLE_SEARCH_DIRECT_COOLDOWN = 900           # seconds a refused thread stays on the fallback proxy

# --- Google Lens -------------------------------------------------------------
GOOGLE_LENS_MAX_IMAGE_BYTES = 20 * 1024 * 1024    # download / upload size cap
GOOGLE_LENS_IMAGE_TIMEOUT = 25                   # seconds per picture download hop
# Retry refused picture downloads / Lens API calls through GOOGLE_LENS_PROXY
# (only when it is set).
GOOGLE_LENS_IMAGE_PROXY_COUNTRY = "us" if GOOGLE_LENS_PROXY else None
GOOGLE_LENS_API_PROXY_COUNTRY = "us" if GOOGLE_LENS_PROXY else None

# --- Google browser sessions ("identities") ---------------------------------
# A headed Camoufox browser opens google.com once, the scraper keeps its
# cookies and replays Lens uploads and result pages over plain HTTP through
# the same exit until Google stops answering (~20-40 pages), then opens a new one.
GOOGLE_SEARCH_IDENTITY_QUOTA = None           # pages per session; None = until Google walls it
GOOGLE_SEARCH_SPARE_AFTER = 8                 # uses after which a spare session is opened in the background
GOOGLE_SEARCH_MAX_IDENTITIES = 4              # live sessions at most (each serves one request at a time)
GOOGLE_SEARCH_MIN_IDENTITIES = 0              # sessions kept warm while idle (1 = no slow first call, but a browser opens every ~25 min)
GOOGLE_SEARCH_PARALLEL_MINTS = 2              # browsers opened at once (~0.5-1 GB RAM each)
GOOGLE_SEARCH_MIN_INTERVAL = 0.3              # seconds between two requests on one session (+0-0.6 s jitter)
GOOGLE_SEARCH_ACQUIRE_TIMEOUT = 420           # seconds a request waits for a free / new session
GOOGLE_SEARCH_IDENTITY_TTL = 1500             # seconds a session is kept at most
GOOGLE_SEARCH_MINT_ATTEMPTS = 4               # exits tried per new session
GOOGLE_SEARCH_MINT_COOLDOWN = 120             # seconds to wait after every exit failed
GOOGLE_SEARCH_SOLVE_TIMEOUT = 180             # seconds to wait for CapSolver to clear a captcha
GOOGLE_SEARCH_SORRY_SOLVES = 2                # captchas solved per session before it is replaced
