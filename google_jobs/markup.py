"""Low-level extraction for Google Jobs responses: the page-level JS data
cards point at, the streamed "prog" chunks the async callbacks answer, and
the filter blob every results page embeds.

Three response shapes (verified 2026-09-24/26):

  * RESULTS PAGE   www.google.com/search?udm=8 — HTML; each job card's
    `jsdata="NAME;_;KEY …"` attributes reference entries of a JS object
    literal elsewhere in the page: `"KEY":[…]`.
  * CHUNK          /async/callback:550 (next 10 jobs) and /async/callback:8166
    (the job viewer) — `)]}'` then typed frames `<hex len>;<payload>`:
    `c;[2,null,"0"]` HTML, `c;[4,…]` scripts, `c;[5,…]` the JS data as
    `[[["KEY","<json text>"],…]]`, `c;[9,…]` end. The same KEY references
    appear in the HTML, so one resolver serves both shapes.
  * STUB           `)]}'` + a 59-byte header whose status block is
    [3,0,1,1,0] — the callback refused the caller (no identity cookies).
    A served chunk carries [7,0,1,1,0].

Frame lengths are not trusted (they count UTF-16 units); JSON values are
decoded with raw_decode from their start instead.
"""
import json
import re

_DEC = json.JSONDecoder()
_DATA_FRAME_RE = re.compile(r'c;\[5,null,"0"\][0-9a-f]+;')
_HEADER_RE = re.compile(r"^\)\]\}'\s*[0-9a-f]+;(\[[^\n]*?\])(?=c;|$)")
_FILTER_PAIR_RE = re.compile(r'\["((?:[^"\\]|\\.){1,80})","(/search\?(?:[^"\\]|\\.)*?uds\\u003d(?:[^"\\]|\\.)+)"')


def is_chunk(text):
    return (text or "").lstrip().startswith(")]}'")


def chunk_status(text):
    """The status block of a callback answer ([7,0,1,1,0] served,
    [3,0,1,1,0] refused), or None when the text is not a chunk."""
    m = _HEADER_RE.match((text or "").lstrip())
    if not m:
        return None
    try:
        header = json.loads(m.group(1))
    except ValueError:
        return None
    return header[3] if isinstance(header, list) and len(header) > 3 and isinstance(header[3], list) else None


def is_refused(text):
    """True for the stub a callback answers a caller without identity
    cookies (status [3,…], no body)."""
    status = chunk_status(text)
    return bool(status) and status[0] == 3


def jsdata_refs(el):
    """An element's jsdata attribute -> {name: key}. Forms seen:
    "ZSBYQe;_;KEY", "rj9Cic;0xb5387dafd5cf729f#17,dt_fav_jobs;KEY"."""
    out = {}
    for part in (el.get("jsdata") or "").split():
        bits = part.split(";")
        if len(bits) >= 3 and bits[-1]:
            out[bits[0]] = bits[-1]
    return out


class JsData:
    """Lazy KEY -> value resolver over one response (page or chunk)."""

    def __init__(self, text):
        self.text = text or ""
        self._pairs = None
        self._values = {}

    def _load_pairs(self):
        pairs = {}
        for m in _DATA_FRAME_RE.finditer(self.text):
            try:
                value, _ = _DEC.raw_decode(self.text, m.end())
            except ValueError:
                continue
            for group in value if isinstance(value, list) else []:
                for pair in group if isinstance(group, list) else []:
                    if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and isinstance(pair[1], str):
                        pairs[pair[0]] = pair[1]
        self._pairs = pairs

    def get(self, key):
        if not key:
            return None
        if key in self._values:
            return self._values[key]
        if self._pairs is None:
            self._load_pairs()
        value = None
        if key in self._pairs:
            try:
                value = json.loads(self._pairs[key])
            except ValueError:
                value = None
        else:
            idx = self.text.find(f'"{key}":')
            if idx >= 0:
                try:
                    value, _ = _DEC.raw_decode(self.text, idx + len(key) + 3)
                except ValueError:
                    value = None
        self._values[key] = value
        return value

    def ref(self, el, name):
        """The value an element's jsdata points at under `name`."""
        if el is None:
            return None
        return self.get(jsdata_refs(el).get(name))


def filter_links(text):
    """Every filter option a results page embeds, in page order:
    [(label, "/search?…&q=<query + suffix>&uds=<token>")]. Each group's
    options come first and its own entry (link back to the unfiltered
    query) closes it: Yesterday … Last month, "Date posted", Full time …
    Internship, "Job type" (hl=en, 2026-09-26)."""
    out = []
    for m in _FILTER_PAIR_RE.finditer(text or ""):
        try:
            label = json.loads('"' + m.group(1) + '"')
            link = json.loads('"' + m.group(2) + '"')
        except ValueError:
            continue
        out.append((label, link))
    return out
