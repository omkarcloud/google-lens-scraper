"""Google Lens results pages: how they are fetched.

The results live on www.google.com/search (udm=26 All, udm=44 Visual
matches, udm=48 Exact matches, udm=50 AI Mode) and answer plain curl_cffi
with the botguard "enable JavaScript" wall, whatever the exit — like every
/search page. They go through the SAME minted Google identities as
/google-search/* (google_search/identity.py: headed Camoufox + CapSolver on
a sticky residential exit, curl replay of its cookies THROUGH THAT EXIT).

One search = one flow on ONE identity (verified live 2026-09-26):

  1. POST https://lens.google.com/v3/upload?ep=gsbubb&st=<ms>&authuser=0&hl&vpw&vph
     multipart `encoded_image` (a JPEG <= 1024 px, see images.for_search)
     -> 303 to www.google.com/search?vsrid=…&gsessionid=…&lsessionid=…&udm=26
     The redirect is followed BY HAND: curl_cffi scopes per-request cookies
     to the request host, so an automatic hop from lens.google.com to
     www.google.com goes out cookieless and lands on the wall (that one
     detail made every early trial look blocked).
  2. GET that /search URL -> the All tab: visual matches, related searches,
     web results, short videos; tab links (udm=44 / 48 / 50) carrying the
     same session; an `/async/folsrch?…` URL for the AI overview.
  3. Optional, same identity and session:
       GET /search?…udm=48   exact matches (the page itself, no XHR)
       GET /search?…udm=44   visual matches tab
       GET lens.google.com/qfmetadata?vsrid&gsessionid&s=26
           ")]}'" JSON: detected regions + per region up to ~120 structured
           matches (title, page, image, thumbnail, price micros + currency,
           merchant) — filled only after step 2 ran for the session
       GET /async/folsrch?…   the AI overview, streamed chunks

URL mode (`lens.google.com/uploadbyurl?url=` or `v3/upload?url=`) is NOT
used: Google then downloads the picture itself and answers 403 whenever
that fails (faces, hosts that refuse Google's fetcher — imgur, Nike and
Wikimedia all 403'd during testing); the scraper downloads the picture
(images.fetch) and always uploads bytes.

A wall / /sorry anywhere in the flow reruns the WHOLE flow on another
identity (the /sorry one revived in the background when allowed).
"""
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from google_search import identity
from google_search.fetch import dump_debug
from google_lens.errors import GoogleBadRequest, GoogleBlocked, GoogleUpstreamError

SITE = "https://www.google.com"
LENS_SITE = "https://lens.google.com"
UPLOAD_PATH = "/v3/upload"
VIEWPORT = (1168, 677)          # what the capture's browser reported (vpw / vph)
DOC_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
XHR_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
}
TAB_UDM = {"all": "26", "visual_matches": "44", "exact_matches": "48", "ai_mode": "50"}

_FOLSRCH_RE = re.compile(r"url:'(/async/folsrch\?[^']+)'")
_TAB_RE = re.compile(r'<a[^>]+href="(/search\?[^"]*?udm=(\d+)[^"]*)"')
_DEAD_EXIT = ("CONNECT tunnel failed", "Proxy CONNECT aborted", "Could not resolve proxy", "Connection refused")


class _Refused(Exception):
    def __init__(self, verdict, text, url):
        super().__init__(verdict)
        self.verdict, self.text, self.url = verdict, text, url


class _Transport(Exception):
    """The identity's exit failed the request (timeout, dead proxy tunnel)."""


class LensRefused(GoogleUpstreamError):
    """Lens answered 403 for this image (not an identity block)."""


def _unescape_js(text):
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text).replace("\\/", "/")


def _html_unescape(text):
    return text.replace("&amp;", "&")


class Session:
    """One identity's view of one Lens search (created by `flow`)."""

    def __init__(self, ident, label):
        self.ident = ident
        self.label = label
        self.calls = 0
        self.page_url = None
        self.page_html = None

    # -- transport --------------------------------------------------------------------------
    def _count(self):
        if self.calls:                      # checkout() counted the first request
            with self.ident.lock:
                self.ident.uses += 1
        self.calls += 1

    def _send(self, method, url, *, headers, what, **kwargs):
        self._count()
        try:
            return identity._session_for(self.ident).request(
                method, url, headers={**headers, "user-agent": self.ident.ua}, cookies=self.ident.cookies,
                timeout=identity.REPLAY_TIMEOUT, **kwargs)
        except Exception as e:
            raise _Transport(f"{self.label}: {what} failed: {type(e).__name__}: {str(e).splitlines()[0][:200]}")

    def _check(self, resp, what):
        text = resp.text
        verdict = identity.classify(text, str(resp.url))
        if verdict != "ok":
            dump_debug(f"{verdict}_{self.label}_{what}", text)
            raise _Refused(verdict, text, str(resp.url))
        if resp.status_code == 403:
            dump_debug(f"lens403_{self.label}_{what}", text)
            raise LensRefused(f"Google Lens refused to process this image ({what}: HTTP 403)")
        if resp.status_code >= 500:
            raise GoogleUpstreamError(f"{self.label}: {what}: HTTP {resp.status_code}")
        if resp.status_code == 400:
            raise GoogleBadRequest(f"{self.label}: Google rejected the {what} request")
        return text

    # -- steps ------------------------------------------------------------------------------
    def upload(self, jpeg, *, language, country, query=None):
        """POST the picture; follow the 303 by hand -> (html, url) of the All tab."""
        from curl_cffi import CurlMime
        mime = CurlMime()
        mime.addpart(name="encoded_image", filename="image.jpg", content_type="image/jpeg", data=jpeg)
        params = {"ep": "gsbubb", "st": int(time.time() * 1000), "authuser": 0, "hl": language,
                  "vpw": VIEWPORT[0], "vph": VIEWPORT[1]}
        resp = self._send("POST", f"{LENS_SITE}{UPLOAD_PATH}?{urlencode(params)}", what="upload", multipart=mime,
                          allow_redirects=False,
                          headers={**DOC_HEADERS, "origin": SITE, "referer": SITE + "/", "sec-fetch-site": "same-site"})
        try:
            mime.close()
        except Exception:
            pass
        location = resp.headers.get("location")
        if resp.status_code not in (302, 303) or not location:
            self._check(resp, "upload")
            raise GoogleUpstreamError(f"{self.label}: the upload answered HTTP {resp.status_code} without a redirect")
        target = urljoin(SITE, location)
        extra = {"gl": country}
        if query:
            extra["q"] = query
        target = _with_params(target, extra)
        return self.page(target, what="results", referer=SITE + "/", site="same-site")

    def page(self, url, *, what, referer=None, site="same-origin"):
        resp = self._send("GET", url, what=what, allow_redirects=True,
                          headers={**DOC_HEADERS, "referer": referer or self.page_url or SITE + "/",
                                   "sec-fetch-site": site})
        html = self._check(resp, what)
        if what == "results":
            self.page_url, self.page_html = str(resp.url), html
        return html, str(resp.url)

    def tab(self, name):
        """html of another tab of this search (udm 44 / 48 / 50)."""
        link = tab_links(self.page_html or "").get(TAB_UDM[name])
        if not link:
            return None
        return self.page(link, what=name)[0]

    def xhr(self, url, *, what, site="same-origin"):
        resp = self._send("GET", url, what=what, allow_redirects=True,
                          headers={**XHR_HEADERS, "referer": self.page_url or SITE + "/", "sec-fetch-site": site,
                                   **({"origin": SITE} if site == "same-site" else {})})
        return self._check(resp, what)

    def qfmetadata(self):
        """The structured matches JSON for this search's session, or None."""
        params = parse_qs(urlparse(self.page_url or "").query)
        vsrid, gsession = (params.get("vsrid") or [None])[0], (params.get("gsessionid") or [None])[0]
        if not vsrid or not gsession:
            return None
        url = f"{LENS_SITE}/qfmetadata?{urlencode({'vsrid': vsrid, 'gsessionid': gsession, 's': 26})}"
        return self.xhr(url, what="qfmetadata", site="same-site")

    def ai_overview(self):
        """The folsrch stream (AI overview) for this search, or None."""
        found = _FOLSRCH_RE.search(self.page_html or "")
        if not found:
            return None
        return self.xhr(SITE + _unescape_js(found.group(1)), what="ai_overview")


def _with_params(url, extra):
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in extra.items():
        if value:
            query[key] = [value]
    return parsed._replace(query=urlencode({k: v[0] for k, v in query.items()})).geturl()


def tab_links(html):
    """{udm: absolute link} of the tab strip."""
    links = {}
    for href, udm in _TAB_RE.findall(html or ""):
        links.setdefault(udm, SITE + _html_unescape(href))
    return links


def flow(work, label):
    """Run work(Session) on ONE checked-out identity of the shared Google
    pool; a wall / /sorry anywhere reruns the whole flow on another one."""
    last, failure = "no identity", None
    for _ in range(identity.MAX_ATTEMPTS):
        ident = identity.checkout()
        try:
            result = work(Session(ident, label))
        except _Refused as refused:
            last = refused.verdict
            identity._drop_session(ident)
            if not (refused.verdict == "sorry" and identity._start_revive(ident, refused.text, refused.url)):
                identity.retire(ident, f"{refused.verdict} page")
            continue
        except _Transport as e:
            failure = str(e)
            identity._drop_session(ident)
            if any(mark in failure for mark in _DEAD_EXIT):
                identity.retire(ident, "exit unreachable")
            else:
                identity.checkin(ident)
            continue
        except GoogleUpstreamError:
            identity.checkin(ident)
            identity._drop_session(ident)
            raise
        except BaseException:
            identity.checkin(ident)
            raise
        identity.checkin(ident)
        return result
    if failure and last == "no identity":
        raise GoogleUpstreamError(failure)
    raise GoogleBlocked(f"{label}: Google served the {last} page for every identity")
