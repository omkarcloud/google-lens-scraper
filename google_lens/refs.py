"""Reference parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrLinkField convention — never a sibling `url` / `file`
/ `base64` trio). Resolvers return plain JSON so the validated params stay
usable as a response-cache key.

  image   https://cdn.example.com/photo.jpg        a picture link
          | https://shop.example.com/product/123   a web page (its og:image)
          | lens.google.com/uploadbyurl?url=<pic>  a Google Lens link (and
            v3/upload?url=, google.com/searchbyimage?image_url=,
            google.com/imgres?imgurl= — the picture they wrap)
          | data:image/jpeg;base64,/9j/…            inline bytes
          -> {"link": "<picture or page link>"} | {"data": "<data URI>"}
          (an upload — POST multipart `image` — becomes {"upload": "<sha256>"})
  region  "0.1,0.2,0.5,0.5"   x, y, width, height as fractions of the picture
          | "120,40,300,300"  the same in pixels (any value above 1)
          -> {"x", "y", "width", "height", "unit": "fraction" | "pixel"}
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

_DATA_URI_RE = re.compile(r"^data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+$", re.I)
_WRAPPERS = (
    ("lens.google.com", ("url",)),
    ("google.com/searchbyimage", ("image_url", "url")),
    ("google.com/imgres", ("imgurl",)),
    ("google.com/url", ("url", "q")),
)


def _unwrap(link):
    """The picture inside a Google Lens / Images link, else the link itself."""
    parsed = urlparse(link)
    host_path = (parsed.netloc + parsed.path).lower().replace("www.", "", 1)
    for marker, keys in _WRAPPERS:
        if host_path.startswith(marker) or ("." + marker) in "." + host_path:
            params = parse_qs(parsed.query)
            for key in keys:
                inner = (params.get(key) or [None])[0]
                if inner and inner.startswith(("http://", "https://")):
                    return unquote(inner) if "%3A" in inner[:12].upper() else inner
    return link


_UPLOAD_RE = re.compile(r"^upload:([0-9a-f]{64})$")


def upload_param(ref):
    """The query value the POST routes put in `image` for a registered upload."""
    return f"upload:{ref['upload']}"


def resolve_image(value):
    value = (value or "").strip()
    if not value:
        raise ValueError("image is required: a picture link, a web page link or a data: URI")
    upload = _UPLOAD_RE.match(value)
    if upload:                       # set by the POST routes (images.register_upload), not by callers
        return {"upload": upload.group(1)}
    if value.lower().startswith("data:"):
        if not _DATA_URI_RE.match(value):
            raise ValueError("image data URI must look like data:image/<type>;base64,<bytes>")
        return {"data": value}
    link = value if "://" in value else ("https://" + value.lstrip("/") if "." in value.split("/")[0] else None)
    if not link:
        raise ValueError("image must be a picture link (https://…), a web page link or a data: URI")
    parsed = urlparse(link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc.split(":")[0]:
        raise ValueError("image must be an http(s) link with a public host name")
    return {"link": _unwrap(link)}


def resolve_region(value):
    parts = [p for p in re.split(r"[\s,;]+", (value or "").strip()) if p]
    if len(parts) != 4:
        raise ValueError("region must be four numbers: x,y,width,height (fractions 0-1, or pixels)")
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError:
        raise ValueError("region must be four numbers: x,y,width,height")
    if min(x, y) < 0 or min(w, h) <= 0:
        raise ValueError("region x / y must be >= 0 and width / height > 0")
    unit = "pixel" if max(x, y, w, h) > 1 else "fraction"
    if unit == "fraction" and (x >= 1 or y >= 1):
        raise ValueError("region x / y must be below 1 when given as fractions")
    return {"x": x, "y": y, "width": w, "height": h, "unit": unit}
