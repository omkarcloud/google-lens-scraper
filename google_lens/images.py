"""The picture a Lens call is about: downloading it (the `image` link) or
reading an upload, and preparing the bytes Lens is sent.

Downloading (verified 2026-09-26):
  * browser-impersonated TLS with an IMAGE Accept header first. The Accept
    matters twice: imgur serves its HTML viewer page to a document Accept,
    and Nike's CDN answers AVIF when the Accept allows it (so avif is left
    out of ours);
  * then a plain non-browser client with a descriptive User-Agent:
    upload.wikimedia.org answers 403 to a browser-impersonated client and
    429 to a generic bot UA, and serves the file to a named client;
  * then the same through a residential exit (config.GOOGLE_LENS_IMAGE_PROXY_COUNTRY)
    for hosts that refuse datacenter egress.
A web PAGE link (text/html) is followed to its og:image / twitter:image,
so a product or post link works as well as the picture's own link.
Every hop is resolved and refused when it points into a private,
loopback, link-local or reserved network (the fetch runs inside the
cluster, next to the metadata server and internal services); redirects are
followed by hand so each hop is checked.

Preparing: Google's own "Search by image" page re-encodes every upload
client-side as a JPEG at most 1024 px on the long side before POSTing it
(captured 2026-09-26: a 1728x1728 WebP left the browser as a 1024x1024
JPEG), and Lens answers that size best — the same 2900x5367 photo sent at
full size got "No exact matches found" and at 1024 px a full exact-match
list. OCR / translation go to the Chromium Lens API at its own limits
(lens_features.cc: downscale above 1.5 MP when a side exceeds 1600 px).
"""
import base64
import hashlib
import io
import ipaddress
import re
import socket
import threading
from urllib.parse import urljoin, urlparse

import config
from google_lens.errors import LensBadRequest

IMAGE_ACCEPT = "image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8,*/*;q=0.5"
BROWSER_HEADERS = {
    "accept": IMAGE_ACCEPT,
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "image",
    "sec-fetch-mode": "no-cors",
    "sec-fetch-site": "cross-site",
}
# A descriptive client name (Wikimedia's User-Agent policy): upload.wikimedia.org
# answered this one 200 while a browser TLS got 403 and a generic
# "compatible; ImageFetcher" UA 429 from the same IP (2026-09-26).
PLAIN_HEADERS = {
    "accept": IMAGE_ACCEPT,
    "user-agent": "GoogleLensScraper/1.0 (https://www.omkar.cloud/tools/google-lens-scraper; happy.to.help@omkar.cloud)",
}
PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
}
MAX_REDIRECTS = 5
SEARCH_MAX_SIDE = 1024          # what google.com's upload widget sends
SEARCH_JPEG_QUALITY = 85
OCR_MAX_AREA = 1_500_000        # Chromium kLensOverlayImageMaxArea
OCR_MAX_SIDE = 1600             # Chromium kLensOverlayImageMaxWidth / Height
OCR_JPEG_QUALITY = 90
MIN_SIDE = 10

_META_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url|:url)?|twitter:image(?::src)?)["\'][^>]*>', re.I)
_CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)
_LINK_IMAGE_RE = re.compile(r'<link[^>]+rel=["\']image_src["\'][^>]*href=["\']([^"\']+)["\']', re.I)
_ITEMPROP_IMAGE_RE = re.compile(r'<(?:meta|link|img)[^>]+itemprop=["\']image["\'][^>]*(?:content|href|src)=["\']([^"\']+)["\']', re.I)
_LD_JSON_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.I | re.S)


class Picture:
    """A decoded image plus where it came from."""

    __slots__ = ("image", "link", "format", "size_bytes")

    def __init__(self, image, link, fmt, size_bytes):
        self.image, self.link, self.format, self.size_bytes = image, link, fmt, size_bytes

    @property
    def width(self):
        return self.image.width

    @property
    def height(self):
        return self.image.height

    def info(self):
        """The `image` block of a response."""
        return {"link": self.link, "width": self.width, "height": self.height,
                "format": (self.format or "").lower() or None, "size_bytes": self.size_bytes}


# ---- network guard ------------------------------------------------------------------------

def _check_host(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise LensBadRequest(f"image must be an http(s) link, got {parsed.scheme or 'no'} scheme")
    host = (parsed.hostname or "").strip("[]")
    if not host:
        raise LensBadRequest("image link has no host")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise LensBadRequest(f"image host {host} does not resolve")
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
                or addr.is_multicast or addr.is_unspecified):
            raise LensBadRequest(f"image host {host} points into a private network")


# ---- download -----------------------------------------------------------------------------

def _session(mode):
    from curl_cffi import requests as curl_requests
    if mode == "plain":
        return curl_requests.Session()
    sess = curl_requests.Session(impersonate="chrome")
    if mode == "proxy":
        proxy = config.google_lens_proxy(config.GOOGLE_LENS_IMAGE_PROXY_COUNTRY)
        sess.proxies = {"http": proxy, "https": proxy}
    return sess


def _download(url, mode, headers):
    """GET with manual redirects (each hop host-checked) and a size cap ->
    (status, content_type, body, final_url)."""
    limit = config.GOOGLE_LENS_MAX_IMAGE_BYTES
    sess = _session(mode)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(url)
            resp = sess.get(url, headers=headers, timeout=config.GOOGLE_LENS_IMAGE_TIMEOUT,
                            allow_redirects=False, stream=True)
            try:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                chunks, size = [], 0
                for chunk in resp.iter_content():
                    size += len(chunk)
                    if size > limit:
                        raise LensBadRequest(f"image is larger than {limit // (1024 * 1024)} MB")
                    chunks.append(chunk)
                return resp.status_code, (resp.headers.get("content-type") or "").lower(), b"".join(chunks), url
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
        raise LensBadRequest("image link redirects too many times")
    finally:
        try:
            sess.close()
        except Exception:
            pass


def _ld_image(html):
    """The `image` of a JSON-LD Product / Article / ImageObject block."""
    import json
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                image = node.get("image") or (node.get("contentUrl") if node.get("@type") == "ImageObject" else None)
                if isinstance(image, list):
                    image = image[0] if image else None
                if isinstance(image, dict):
                    image = image.get("url") or image.get("contentUrl")
                if isinstance(image, str) and image.strip():
                    return image.strip()
                stack.extend(v for v in node.values() if isinstance(v, (list, dict)))
    return None


def _page_image(html, base):
    """og:image / twitter:image, then image_src, JSON-LD image and
    itemprop=image of a web page -> absolute link."""
    for tag in _META_IMAGE_RE.findall(html):
        found = _CONTENT_RE.search(tag)
        if found and found.group(1).strip():
            return urljoin(base, found.group(1).strip().replace("&amp;", "&"))
    for candidate in ((_LINK_IMAGE_RE.search(html) or [None, None])[1], _ld_image(html),
                      (_ITEMPROP_IMAGE_RE.search(html) or [None, None])[1]):
        if candidate:
            return urljoin(base, candidate.strip().replace("&amp;", "&"))
    return None


def _looks_like_image(body, content_type):
    if content_type.startswith("image/"):
        return True
    head = body[:16]
    return (head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"BM", b"II*\x00", b"MM\x00*"))
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or head[4:12] in (b"ftypavif", b"ftypheic"))


def fetch(url):
    """The picture at `url` (or the og:image of the page at `url`) -> Picture."""
    last = None
    followed_page = False
    target = url
    for mode, headers in (("browser", BROWSER_HEADERS), ("plain", PLAIN_HEADERS), ("proxy", BROWSER_HEADERS)):
        if mode == "proxy" and not config.GOOGLE_LENS_IMAGE_PROXY_COUNTRY:
            break
        try:
            status, ctype, body, final = _download(target, mode, headers)
        except LensBadRequest:
            raise
        except Exception as e:
            last = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
            continue
        if status == 200 and _looks_like_image(body, ctype):
            return decode(body, link=url)
        if status == 200 and "html" in ctype and not followed_page:
            page_image = _page_image(body.decode("utf-8", "replace"), final)
            if page_image:
                followed_page = True
                target = page_image
                try:
                    status, ctype, body, _ = _download(target, mode, BROWSER_HEADERS if mode != "plain" else PLAIN_HEADERS)
                except LensBadRequest:
                    raise
                except Exception as e:
                    last = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
                    continue
                if status == 200 and _looks_like_image(body, ctype):
                    return decode(body, link=url)
            last = "the link is a web page that names no picture (og:image / JSON-LD) — pass the picture's own link"
            continue
        if status == 404:
            raise LensBadRequest("the image link answered HTTP 404")
        last = f"HTTP {status} ({ctype or 'no content type'})"
    raise LensBadRequest(f"could not download the image: {last}")


# ---- decode / prepare -----------------------------------------------------------------------

def decode(raw, link=None):
    """Bytes -> Picture (EXIF rotation applied, first frame of animations)."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not raw:
        raise LensBadRequest("the image is empty")
    if len(raw) > config.GOOGLE_LENS_MAX_IMAGE_BYTES:
        raise LensBadRequest(f"image is larger than {config.GOOGLE_LENS_MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    try:
        image = Image.open(io.BytesIO(raw))
        fmt = image.format
        image.seek(0)
        image = ImageOps.exif_transpose(image)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as e:
        raise LensBadRequest(f"not a readable image (JPEG, PNG, WebP, GIF, BMP, TIFF): {type(e).__name__}")
    if min(image.size) < MIN_SIDE:
        raise LensBadRequest(f"image is too small ({image.width}x{image.height} px)")
    return Picture(image, link, fmt, len(raw))


def _rgb(image):
    from PIL import Image
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        return background
    return image.convert("RGB") if image.mode != "RGB" else image


def crop(picture, region):
    """`region` (refs.resolve_region: x, y, width, height as fractions or
    pixels, top-left origin) -> the cropped PIL image; None -> the whole picture."""
    image = picture.image
    if not region:
        return image
    if region.get("unit") == "pixel":
        left, top = region["x"], region["y"]
        right, bottom = min(image.width, left + region["width"]), min(image.height, top + region["height"])
    else:
        left, top = region["x"] * image.width, region["y"] * image.height
        right = min(1.0, region["x"] + region["width"]) * image.width
        bottom = min(1.0, region["y"] + region["height"]) * image.height
    left, top, right, bottom = (int(round(v)) for v in (left, top, right, bottom))
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        raise LensBadRequest(f"region is too small or outside this {image.width}x{image.height} image")
    return image.crop((left, top, right, bottom))


def _jpeg(image, quality):
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def for_search(picture, region=None):
    """JPEG <= 1024 px (what google.com uploads) -> (bytes, width, height)."""
    image = _rgb(crop(picture, region))
    if max(image.size) > SEARCH_MAX_SIDE:
        image = image.copy()
        image.thumbnail((SEARCH_MAX_SIDE, SEARCH_MAX_SIDE))
    return _jpeg(image, SEARCH_JPEG_QUALITY), image.width, image.height


# ---- the `image` param -----------------------------------------------------------------------

_uploads = {}
_uploads_lock = threading.Lock()


def register_upload(raw):
    """Keep an uploaded file for the duration of one request -> its ref
    {"upload": sha256} (so uploads share response-cache rows by content)."""
    digest = hashlib.sha256(raw).hexdigest()
    with _uploads_lock:
        _uploads[digest] = raw
    return {"upload": digest}


def release_upload(ref):
    with _uploads_lock:
        _uploads.pop((ref or {}).get("upload"), None)


def load(ref):
    """refs.resolve_image output (or an upload ref) -> Picture."""
    if not isinstance(ref, dict):
        raise LensBadRequest("image is required")
    if ref.get("upload"):
        with _uploads_lock:
            raw = _uploads.get(ref["upload"])
        if raw is None:
            raise LensBadRequest("the uploaded image is no longer available; send it again")
        return decode(raw, link=None)
    if ref.get("data"):
        try:
            raw = base64.b64decode(ref["data"].split(",", 1)[1], validate=False)
        except (ValueError, IndexError):
            raise LensBadRequest("image data URI is not valid base64")
        return decode(raw, link=None)
    if ref.get("link"):
        return fetch(ref["link"])
    raise LensBadRequest("image is required")


def for_ocr(picture, region=None):
    """JPEG within the Chromium Lens overlay limits -> (bytes, width, height)."""
    image = _rgb(crop(picture, region))
    w, h = image.size
    if w * h > OCR_MAX_AREA and (w > OCR_MAX_SIDE or h > OCR_MAX_SIDE):
        scale = min(OCR_MAX_SIDE / w, OCR_MAX_SIDE / h, (OCR_MAX_AREA / (w * h)) ** 0.5)
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    return _jpeg(image, OCR_JPEG_QUALITY), image.width, image.height
