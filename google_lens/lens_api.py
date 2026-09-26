"""The Chromium Lens API: what Chrome's "Search with Google Lens" overlay
calls to read an image before any results page exists.

    POST https://lensfrontend-pa.googleapis.com/v1/crupload
         content-type: application/x-protobuf
         x-goog-api-key: <Chromium's public Lens key>
         body: LensOverlayServerRequest { objects_request }

Plain curl_cffi, no cookies, no identity (verified 2026-09-26 from the
office egress and a residential exit: 1-2 s per image, no captcha, no
wall). The answer (LensOverlayServerResponse.objects_response) carries:

  text          the full OCR text layout: paragraphs -> lines -> words,
                each with a rotated bounding box, the paragraph language,
                the writing direction and LaTeX for words Lens reads as
                formulas
  deep_gleams   with the TRANSLATE filter: the translation of each
                paragraph (source / target language, per-line spans)
  overlay_objects  the regions Lens would let a user tap (SalientRegion-*,
                DetectedObject-*): box + segmentation polygon, no label
  cluster_info  the server / search session ids and routing

Requests mirror LensOverlayQueryController::CreateClientContext: platform
WEB (3), surface CHROMIUM (4), rendering env LENS_OVERLAY (14), exactly one
filter (AUTO_FILTER 7, or TRANSLATE 2 which REPLACES it).
"""
import random
import time

import config
from google_lens import proto
from google_lens.errors import GoogleBadRequest, GoogleBlocked, GoogleUpstreamError

ENDPOINT = "https://lensfrontend-pa.googleapis.com/v1/crupload"
# Chromium's Lens overlay key (public: shipped in every Chrome build).
API_KEY = "AIzaSyDr2UxVnv_U85AbhhY8XSHSIavUW0DC-sY"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")
TIMEOUT = 40

PLATFORM_WEB = 3
SURFACE_CHROMIUM = 4
RENDERING_ENV_LENS_OVERLAY = 14
FILTER_TRANSLATE = 2
FILTER_AUTO = 7

# Country -> an IANA zone the Lens client context can carry (the server
# only uses it for date-sensitive answers; the region drives locale).
_TIME_ZONES = {"US": "America/New_York", "GB": "Europe/London", "CA": "America/Toronto", "AU": "Australia/Sydney",
               "IN": "Asia/Kolkata", "DE": "Europe/Berlin", "FR": "Europe/Paris", "ES": "Europe/Madrid",
               "IT": "Europe/Rome", "BR": "America/Sao_Paulo", "MX": "America/Mexico_City", "JP": "Asia/Tokyo",
               "KR": "Asia/Seoul", "CN": "Asia/Shanghai", "RU": "Europe/Moscow", "NL": "Europe/Amsterdam"}


def _request(jpeg, width, height, *, language, country, target_language=None, source_language=None):
    uuid = random.randint(0, (1 << 63) - 1)
    if target_language:
        applied = {1: FILTER_TRANSLATE, 3: {1: target_language, 2: source_language}}
    else:
        applied = {1: FILTER_AUTO}
    client_context = {
        1: PLATFORM_WEB,
        2: SURFACE_CHROMIUM,
        4: {1: language or "", 2: country or "US", 3: _TIME_ZONES.get((country or "US").upper(), "America/New_York")},
        17: {1: applied},
        20: {2: RENDERING_ENV_LENS_OVERLAY},
    }
    request_context = {3: {1: uuid, 2: 1, 3: 1}, 4: client_context}
    image_data = {1: {1: jpeg}, 3: {1: width, 2: height}}
    return proto.encode({1: {1: request_context, 3: image_data}})


_local_sessions = {}


def _session(proxied):
    import threading
    key = (threading.get_ident(), proxied)
    sess = _local_sessions.get(key)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate="chrome")
        if proxied:
            proxy = config.google_lens_proxy(config.GOOGLE_LENS_API_PROXY_COUNTRY)
            sess.proxies = {"http": proxy, "https": proxy}
        _local_sessions[key] = sess
    return sess


def _drop(proxied):
    import threading
    sess = _local_sessions.pop((threading.get_ident(), proxied), None)
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def call(jpeg, width, height, *, language="", country="US", target_language=None, source_language=None):
    """One crupload round trip -> proto.Msg of LensOverlayObjectsResponse.
    Direct first; a 429 / 5xx / transport failure retries through a
    residential exit (config.GOOGLE_LENS_API_PROXY_COUNTRY)."""
    body = _request(jpeg, width, height, language=language, country=country,
                    target_language=target_language, source_language=source_language)
    headers = {"content-type": "application/x-protobuf", "x-goog-api-key": API_KEY, "user-agent": USER_AGENT,
               "accept": "*/*", "accept-encoding": "gzip, deflate, br"}
    attempts = [False, False] + ([True, True] if config.GOOGLE_LENS_API_PROXY_COUNTRY else [])
    last = None
    for i, proxied in enumerate(attempts):
        try:
            resp = _session(proxied).post(ENDPOINT, data=body, headers=headers, timeout=TIMEOUT)
        except Exception as e:
            last = GoogleUpstreamError(f"lens api: request failed: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            _drop(proxied)
            continue
        if resp.status_code == 200:
            reply = proto.message(resp.content)
            error = reply.msg(1)
            if error is not None and (error.int(1) or 0) != 0:
                last = GoogleUpstreamError(f"lens api: server error type {error.int(1)}")
                continue
            objects = reply.msg(2)
            if objects is None:
                raise GoogleUpstreamError("lens api: the answer carries no objects response")
            return objects
        if resp.status_code == 400:
            raise GoogleBadRequest(f"Lens rejected the image (HTTP 400: {resp.text[:160]})")
        if resp.status_code in (403, 429):
            last = GoogleBlocked(f"lens api: HTTP {resp.status_code}")
        else:
            last = GoogleUpstreamError(f"lens api: HTTP {resp.status_code}")
        _drop(proxied)
        time.sleep(0.5 * (i + 1))
    raise last
