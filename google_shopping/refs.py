"""Reference parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrLinkField convention — never a sibling `url`/`id`
pair). Resolvers return plain JSON values so the validated params stay
usable as a response-cache key.

  query     "wireless earbuds"
            | a Google Shopping results link (google.com/search?q=…&udm=28
              [&shoprs=…]) — e.g. any `filters[].options[].link` of a
              response; its shoprs filter token rides along
            -> {"text": "wireless earbuds", "filter": "<shoprs>" | None}
  product   2710314607678403180                (catalog id)
            | productid:8548731719060507456    (offer-only listings)
            | catalogid:…,productid:…,gpcid:…,pvt:a   (a prds string)
            | google.com/search?…ibp=oshop&prds=…     (product viewer link)
            | google.com/shopping/product/<id>        (legacy link)
            -> {"prds": "catalogid:…" | "headlineOfferDocid:…,productid:…", "query": q | None}
  store     bestbuy.com | https://www.bestbuy.com/site/… |
            google.com/storepages?q=bestbuy.com… |
            google.com/shopping/ratings/account/metrics?q=bestbuy.com…
            -> "bestbuy.com"
  category  529370 | Electronics | electronics -> 529370
"""
import re
from urllib.parse import parse_qs, unquote, urlparse

from google_search.refs import is_google_link, params_of

_DIGITS = re.compile(r"^\d{5,25}$")
_PRDS_PART = re.compile(r"(catalogid|productid|headlineOfferDocid|gpcid|imageDocid|mid):([^,&\s]*)")
_DOMAIN = re.compile(r"^(?=.{3,253}$)([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

# Google Shopping's top-level departments (Google product taxonomy ids, as
# linked from the shopping home page's "Browse categories" rail, 2026-09-24).
DEPARTMENTS = [
    (529373, "Apparel"), (533692, "House and garden"), (529370, "Electronics"), (532160, "Health and beauty"),
    (529367, "Home improvement"), (529366, "Toys and games"), (529365, "Sport and outdoors"),
    (534003, "Arts, crafts and party supplies"), (530438, "Pet supplies"), (544557, "Business and industrial"),
    (533691, "Office and school supplies"), (530546, "Travel, luggage and bags"), (539102, "Musical instruments"),
    (533815, "Household supplies"), (529372, "Baby and children"),
]
DEPARTMENT_NAMES = {cid: name for cid, name in DEPARTMENTS}
_DEPARTMENT_BY_NAME = {re.sub(r"[^a-z]", "", name.lower()): cid for cid, name in DEPARTMENTS}


def _is_link(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//", "www.")) or "/" in value


def _parsed(value):
    return urlparse(value if "://" in value else "https://" + value.lstrip("/"))


# ---- query ------------------------------------------------------------------------------------

def resolve_query(value):
    value = (value or "").strip()
    if is_google_link(value):
        params = params_of(value)
        q = (params.get("q") or "").strip()
        if not q:
            raise ValueError("a Google Shopping link must carry a q= query")
        return {"text": q, "filter": params.get("shoprs") or None}
    if value.lower().startswith(("http://", "https://")):
        raise ValueError("query must be a search term or a google.com/search?…udm=28 Google Shopping link")
    if not value:
        raise ValueError("query must not be empty")
    return {"text": value, "filter": None}


# ---- product ----------------------------------------------------------------------------------

def _minimal_prds(prds):
    """A prds string -> the smallest form the product viewer renders fully
    (verified 2026-09-26, US + GB): catalogid:<id> for catalog products;
    headlineOfferDocid:<offer>,productid:<id> for offer-only listings
    (productid alone renders only on the US storefront; for offer-only
    listings the two ids are the same number)."""
    parts = {k: v for k, v in _PRDS_PART.findall(prds or "") if v}
    if parts.get("catalogid"):
        return f"catalogid:{parts['catalogid']}"
    pid = parts.get("productid") or parts.get("headlineOfferDocid")
    if pid:
        return f"headlineOfferDocid:{parts.get('headlineOfferDocid') or pid},productid:{pid}"
    return None


def resolve_product(value):
    value = (value or "").strip()
    if _DIGITS.match(value):
        return {"prds": f"catalogid:{value}", "query": None}
    if is_google_link(value):
        parsed = _parsed(value)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
        prds = _minimal_prds(unquote(params.get("prds") or ""))
        if prds:
            return {"prds": prds, "query": (params.get("q") or "").strip() or None}
        m = re.search(r"/shopping/product/(\d{5,25})", parsed.path)
        if m:
            return {"prds": f"catalogid:{m.group(1)}", "query": (params.get("q") or "").strip() or None}
        raise ValueError("product link must be a Google Shopping product link (…ibp=oshop&prds=…)")
    if _PRDS_PART.search(value):
        prds = _minimal_prds(value)
        if prds:
            return {"prds": prds, "query": None}
    raise ValueError("product must be a Google Shopping product id (e.g. 2710314607678403180 or "
                     "productid:8548731719060507456) or a Google Shopping product link")


# ---- store ------------------------------------------------------------------------------------

def _clean_domain(host):
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def resolve_store(value):
    value = (value or "").strip()
    if not value:
        raise ValueError("store must not be empty")
    if is_google_link(value):
        q = params_of(value).get("q")
        if not q:
            raise ValueError("a Google store link must carry the store domain in q= "
                             "(google.com/storepages?q=bestbuy.com)")
        value = q.strip()
    if _is_link(value) and "." in value:
        value = _parsed(value).hostname or ""
    domain = _clean_domain(value)
    if not _DOMAIN.match(domain):
        raise ValueError("store must be a store domain (bestbuy.com), its website link, or a "
                         "google.com/storepages link")
    return domain


# ---- category ---------------------------------------------------------------------------------

def resolve_category(value):
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    cid = _DEPARTMENT_BY_NAME.get(re.sub(r"[^a-z]", "", value.lower()))
    if cid is None:
        raise ValueError("category must be a Google product category id (e.g. 529370) or one of: "
                         + ", ".join(name for _, name in DEPARTMENTS))
    return cid
