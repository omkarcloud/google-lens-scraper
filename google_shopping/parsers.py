"""Parsers for Google Shopping (udm=28) pages as a replayed identity receives
them (desktop layout, hl=en). Verified 2026-09-26 against live pages.

Results pages (search / deals / department browse / the shopping home)
----------------------------------------------------------------------
Product cards are DOM-only (no JSON): `div[data-cid][data-oid]` carrying
five ids (data-cid catalogid, data-pid productid, data-gid gpcid, data-oid
headline offer docid, data-iid image docid; offer-only listings have an
empty data-cid). No card carries a link — the product viewer is opened by
JS; parsers build the viewer link from the ids. Text hooks (obfuscated
class names of that day): .gkQHve title, .lmQWe price, .DoCHT compare-at
price ("Was $190" / "$190") or typical price ("Usually $35"), .NkyFue
discount badge, .YOpUbb other badges ("LOW PRICE"), .WJMUdc seller,
.Ludoze "& more", .ybnj7e delivery, .l9Ycjb returns, .W0uRhb condition,
.yi40Hd rating, .RDApEe review count. Thumbnails: the first few are inline
data URIs (`var s='data:…';var ii=[…]`), the rest https URLs in
`google.ldi={id: url}`.

The filter rail is a list of groups (heading `span[jsname=r4nke]`) of chip
links `/search?q=<rewritten>&udm=28&shoprs=<token>`; see tokens.py.

Product viewer (/search?…&ibp=oshop&prds=…)
--------------------------------------------
The whole product state is ONE jsdata map `(function(){var m={…}` whose
largest entry is [ …, [6] = ProductDetailsResult ] — the same positional
message the offers XHR (/async/oapv) answers. Positions used (P = that
message):
  P[0] title  P[2] brand  P[11] ids [imageDocid, catalogid, productid,
  headlineOfferDocid, _, gpcid, mid, …, {"43": oapvfc, "44": xsrf}]
  P[50] / P[68] related shelves ("More options" / "Lower-priced options"),
        items [.., [12] = card message]
  P[61][0]{…}[5] full-size images [[url, w, h], …]
  P[70][0][0] discussions & forums threads
  P[71] variants [[name, [[label, selected, available, _, token, _, [thumb…]]…]]…]
  P[81][0] offers block: [0] offers, [4] total offer count,
        [19] price insights: [0] ["$109", "$208", axis_lo, typical_lo,
        typical_hi, axis_hi, …] micros, [6][0] base64 price history
        (one series per store), [6][1] currency, [6][9] base64 events
  P[93][0][1][0] description
Offer (P[81][0][0][i], 88 positions): [0] docid, [1] [merchant, merchant id],
[2][0] merchant URL, [8] favicon block, [24] merchant rating block,
[26][0] offer details ([9] price micros, [10] currency, [27] strike price,
[28] title, [47][0] attributes), [48] price texts, [49]/[51] availability /
delivery / returns, [60][0] payment methods, [61] discount, [82] badge.

The product's own star rating is not in the model: it is read from the
`data-attrid="product_rating"` aria-label when Google renders it (most
product viewers no longer do, 2026-09-26).

Store pages (/storepages?q=<domain>&c=<CC>&v=19)
------------------------------------------------
AF_initDataCallback blocks: ds:2 return policy, ds:4 badge + third-party
ratings, ds:6 rating / name / socials / site / logo, ds:7 star
histogram, ds:9 trending products, ds:10 topic insights, ds:11 highlighted
reviews, ds:12 the store's social videos, ds:13 video reviews, ds:14
Google-sourced reviews ([1] next cursor, [2] reviews), ds:15 AI summary,
ds:17 web mentions (Reddit…) ([1] cursor, [2] items). Review pages come
from the IJk5Wc batchexecute RPC ([1] next cursor, [2] items).
"""
import json
import re
import struct
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlparse, urlunparse

from google_search.serp_parsers import (count_of, deferred_images, first, js_unescape,  # noqa: F401
                                        parse_document, text)
from google_search.shared import at, clean_text, to_float, to_int
from google_shopping import refs, tokens

SITE = "https://www.google.com"

# ---- money -------------------------------------------------------------------------------------

COUNTRY_CURRENCY = {
    "US": "USD", "CA": "CAD", "AU": "AUD", "NZ": "NZD", "GB": "GBP", "IE": "EUR", "DE": "EUR", "FR": "EUR",
    "ES": "EUR", "IT": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR", "PT": "EUR", "FI": "EUR", "GR": "EUR",
    "LU": "EUR", "SK": "EUR", "SI": "EUR", "EE": "EUR", "LV": "EUR", "LT": "EUR", "IN": "INR", "JP": "JPY",
    "KR": "KRW", "BR": "BRL", "MX": "MXN", "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN",
    "TR": "TRY", "ZA": "ZAR", "SG": "SGD", "HK": "HKD", "MY": "MYR", "ID": "IDR", "PH": "PHP", "TH": "THB",
    "VN": "VND", "AE": "AED", "SA": "SAR", "IL": "ILS", "CZ": "CZK", "HU": "HUF", "RO": "RON", "AR": "ARS",
    "CL": "CLP", "CO": "COP", "PE": "PEN", "TW": "TWD", "UA": "UAH", "EG": "EGP", "NG": "NGN", "PK": "PKR",
}
_SYMBOLS = [("US$", "USD"), ("CA$", "CAD"), ("C$", "CAD"), ("AU$", "AUD"), ("A$", "AUD"), ("NZ$", "NZD"),
            ("HK$", "HKD"), ("S$", "SGD"), ("R$", "BRL"), ("MX$", "MXN"), ("NT$", "TWD"), ("£", "GBP"),
            ("€", "EUR"), ("¥", "JPY"), ("₹", "INR"), ("Rs", "INR"), ("₩", "KRW"), ("₽", "RUB"), ("₺", "TRY"),
            ("₱", "PHP"), ("₫", "VND"), ("฿", "THB"), ("₪", "ILS"), ("zł", "PLN"), ("Kč", "CZK"),
            ("Ft", "HUF"), ("lei", "RON"), ("CHF", "CHF"), ("RM", "MYR"), ("Rp", "IDR"), ("AED", "AED"),
            ("SAR", "SAR"), ("kr", None), ("$", None)]
_ISO_RE = re.compile(r"\b([A-Z]{3})\b")
_NUM_RE = re.compile(r"\d[\d.,   ]*")


def parse_amount(value):
    """'$1,234.56' | '1.234,56 €' | '₹ 12,499' | 'Free' -> float (Free -> 0.0)."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value)
    if s.strip().lower() == "free":
        return 0.0
    m = _NUM_RE.search(s)
    if not m:
        return None
    num = re.sub(r"[   ]", "", m.group(0)).rstrip(".,")
    if "," in num and "." in num:
        dec = "," if num.rfind(",") > num.rfind(".") else "."
        num = num.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in num:
        tail = num.rsplit(",", 1)[1]
        num = num.replace(",", ".") if len(tail) in (1, 2) and num.count(",") == 1 else num.replace(",", "")
    elif num.count(".") > 1 or (num.count(".") == 1 and len(num.rsplit(".", 1)[1]) == 3 and s.strip()[-1:] in "€ "):
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def currency_of(value, country=None):
    """ISO currency of a price string; a bare "$" / "kr" follows `country`."""
    s = str(value or "")
    iso = _ISO_RE.search(s)
    if iso and iso.group(1) not in ("OFF", "USA"):
        return iso.group(1)
    for symbol, code in _SYMBOLS:
        if symbol in s:
            return code or COUNTRY_CURRENCY.get((country or "US").upper())
    return None


def money(value, country=None):
    """Price text -> (amount, currency)."""
    return parse_amount(value), currency_of(value, country) if value else None


def micros(value):
    """Google price micros (104970000) -> 104.97."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value / 1e6, 2)


def percent(value):
    """'44% OFF' | '44% off' -> 44."""
    m = re.search(r"(\d{1,3})\s*%", str(value or ""))
    return int(m.group(1)) if m else None


def _discount(price, original):
    if price is None or not original or original <= price:
        return None
    return int(round((original - price) / original * 100))


def pricing(price, original=None, currency=None, typical=None, discount=None):
    return {
        "price": price,
        "original_price": original if original and (price is None or original > price) else None,
        "discount_percent": discount if discount is not None else _discount(price, original),
        "typical_price": typical,
        "currency": currency,
    }


# ---- links ----------------------------------------------------------------------------------

def product_id(catalog_id=None, product_id=None, offer_id=None):
    """The id /products/* take: the catalog id, or productid:<id> for an
    offer-only listing (its offer docid appended when it differs)."""
    if catalog_id:
        return str(catalog_id)
    product_id = product_id or offer_id
    if product_id:
        if offer_id and offer_id != product_id:
            return f"productid:{product_id},headlineOfferDocid:{offer_id}"
        return f"productid:{product_id}"
    return None


def product_link(pid, query=None, country="US", language="en"):
    """The product viewer link for an id from product_id()."""
    if not pid:
        return None
    prds = refs.resolve_product(pid)["prds"]
    params = {"q": query or "shopping", "udm": 28, "ibp": "oshop", "prds": f"{prds},pvt:a",
              "hl": language or "en", "gl": (country or "us").lower()}
    return SITE + "/search?" + urlencode(params, safe=":,")


def results_link(query, token=None, country="US", language="en", extra=None):
    params = {"q": query, "udm": 28, "hl": language or "en", "gl": (country or "us").lower()}
    if token:
        params["shoprs"] = token
    params.update(extra or {})
    return SITE + "/search?" + urlencode(params)


def strip_tracking(url):
    """Merchant links carry Google's click-tracking `srsltid` param: drop it."""
    if not url or "srsltid" not in url:
        return url
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qs(parsed.query, keep_blank_values=True).items() if k != "srsltid"]
    return urlunparse(parsed._replace(query=urlencode([(k, x) for k, vs in query for x in vs])))


def _clean_link(href):
    """A Google-relative filter link -> absolute, without ved / sa / ei noise."""
    if not href:
        return None
    parsed = urlparse(urljoin(SITE, href))
    keep = [(k, v) for k, vs in parse_qs(parsed.query, keep_blank_values=True).items()
            for v in vs if k not in ("ved", "sa", "ei", "sca_esv", "sxsrf", "fbs", "biw", "bih", "dpr")]
    return urlunparse(parsed._replace(query=urlencode(keep)))


def _ts_iso(seconds):
    try:
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# ---- results pages: cards ---------------------------------------------------------------------

_LDI_RE = re.compile(r"google\.ldi=(\{.*?\});", re.S)


def _ldi(html):
    """id -> https thumbnail URL (google.ldi)."""
    out = {}
    for m in _LDI_RE.finditer(html or ""):
        try:
            out.update(json.loads(m.group(1)))
        except ValueError:
            continue
    return out


def _thumb(card, ldi, dmap):
    for img in card.cssselect('img[id^="dimg_"], img[id^="dimg"]'):
        iid = img.get("id")
        src = ldi.get(iid) or dmap.get(iid) or img.get("data-src")
        if src:
            return src
        src = img.get("src") or ""
        if src.startswith("http"):
            return src
    for img in card.cssselect("img"):        # the home page inlines https thumbnails without ids
        src = img.get("src") or img.get("data-src") or ""
        if "/shopping?q=tbn:" in src or "/images?q=tbn:" in src:
            return src
    return None


def _section_of(card):
    """The shelf heading a card sits under ("All products", "Deals on …")."""
    for anc in card.iterancestors():
        heads = anc.xpath('.//*[@role="heading" and @aria-level="2"]')
        if len(heads) == 1:
            return text(heads[0])
        if len(heads) > 1:
            return None
    return None


def parse_card(card, country="US", language="en", query=None, ldi=None, dmap=None):
    """One product card of a results / home page."""
    cid = card.get("data-cid") or None
    pid = card.get("data-pid") or None
    oid = card.get("data-oid") or None
    ident = product_id(cid, pid, oid)
    entry = first(card, '[aria-label$="Go to product viewer for this item."]')
    title = text(first(card, ".gkQHve"))
    if not title:
        img = first(card, '[role="img"][title]')
        title = img.get("title") if img is not None else None
    if not title and entry is not None:
        title = (entry.get("aria-label") or "").split(". ")[0] or None
    price_text = text(first(card, ".lmQWe"))
    price, currency = money(price_text, country)
    original = typical = None
    for el in card.cssselect(".DoCHT"):
        t = text(el) or ""
        if t.lower().startswith("usually"):
            typical = parse_amount(t)
        else:
            original = parse_amount(t)
            currency = currency or currency_of(t, country)
    discount = percent(text(first(card, ".NkyFue")))
    badges = [t for t in (text(x) for x in card.cssselect(".YOpUbb:not(.NkyFue), .CcOsDc")) if t]
    rating = to_float(text(first(card, ".yi40Hd")))
    reviews = count_of((text(first(card, ".RDApEe")) or "").strip("()") or None)
    delivery = [t for t in (text(x) for x in card.cssselect(".ybnj7e")) if t]
    returns = text(first(card, ".l9Ycjb"))
    return {
        "id": ident,
        "title": title,
        "link": product_link(ident, query, country, language),
        "thumbnail": _thumb(card, ldi or {}, dmap or {}),
        "seller": text(first(card, ".WJMUdc")),
        "has_other_sellers": first(card, ".Ludoze") is not None,
        "condition": text(first(card, ".W0uRhb")),
        "delivery": delivery[0] if delivery else None,
        "returns": returns,
        "badges": badges,
        "rating": rating,
        "review_count": reviews,
        "pricing": pricing(price, original, currency, typical, discount),
        "ids": {"catalog_id": cid, "product_id": pid, "offer_id": oid, "gpc_id": card.get("data-gid") or None,
                "image_id": card.get("data-iid") or None},
    }


def parse_cards(doc, html, country="US", language="en", query=None, scope="#rso "):
    """Unique product cards in page order, each with its shelf title."""
    ldi, dmap = _ldi(html), deferred_images(html)
    cards = doc.cssselect(f"{scope}div[data-oid][data-cid]") if scope else []
    if not cards:
        cards = doc.cssselect("div[data-oid][data-cid]")
    out, seen = [], set()
    for card in cards:
        key = (card.get("data-cid"), card.get("data-pid"), card.get("data-oid"))
        if key in seen:
            continue
        seen.add(key)
        item = parse_card(card, country, language, query, ldi, dmap)
        if not item["title"]:
            continue
        item = {"position": len(out) + 1, **item, "section": _section_of(card)}
        out.append(item)
    return out


# ---- results pages: filter rail ----------------------------------------------------------------

def _chip_label(a):
    div = first(a, "[title]")
    return (div.get("title") if div is not None else None) or text(a)


def _is_selected(a):
    label = " ".join(filter(None, [a.get("aria-label")] + [e.get("aria-label") or "" for e in a.cssselect("[aria-label]")]))
    return "Selected." in label and "Not selected" not in label


def parse_filters(doc):
    """The "Refine results" rail -> [{name, type, options: [{label, is_selected, link}]}].
    Each option's `link` is a Google Shopping results link that can be
    passed back as `query` to apply that filter."""
    groups, index = [], {}
    for a in doc.xpath('//a[contains(@href, "shoprs=")]'):
        href = a.get("href") or ""
        if "shopmd=" in href or "start=" in href or any(anc.get("aria-label") == "Refinement"
                                                        for anc in a.iterancestors()):
            continue                      # Nearby / Deals tabs, "More results", the duplicate chip carousel
        label = _chip_label(a)
        if not label or label in ("More", "See more", "Fewer"):
            continue
        heading = None
        for anc in a.iterancestors():
            head = anc.xpath('.//span[@jsname="r4nke"]')
            if head:
                heading = text(head[0]) if len(head) == 1 else None
                break
            if anc.tag in ("ul",) and anc.get("class") and "xvRDEb" in anc.get("class"):
                break
        token = (parse_qs(urlparse(href).query).get("shoprs") or [None])[0]
        kind = tokens.token_kind(token) if token else None
        type_ = tokens.KIND_NAMES.get(kind, "attribute")
        name = heading or {"on_sale": "On sale", "nearby": "Nearby", "delivery": "Delivery",
                           "condition": "Condition", "free_shipping": "Free shipping",
                           "small_business": "Small business", "rating": "Product rating"}.get(type_, "Other")
        key = name
        if key not in index:
            index[key] = {"name": name, "type": type_, "options": []}
            groups.append(index[key])
        group = index[key]
        if any(o["label"] == label for o in group["options"]):
            continue
        group["options"].append({"label": label, "is_selected": _is_selected(a), "link": _clean_link(href)})
    return groups


def selected_filters(doc):
    """Labels of the applied filters (aria-label "… filter. Selected.")."""
    out = []
    for el in doc.xpath('//*[contains(@aria-label, "filter. Selected.")]'):
        t = (el.get("aria-label") or "").split(" filter. Selected.")[0].strip()
        t = re.sub(r"^Remove\s+", "", t)
        if t and t not in out:
            out.append(t)
    return out


def parse_results(html, country="US", language="en", query=None):
    doc = parse_document(html)
    return {
        "products": parse_cards(doc, html, country, language, query),
        "filters": parse_filters(doc),
        "applied_filters": selected_filters(doc),
    }


# ---- product viewer -----------------------------------------------------------------------------

_MODEL_MARK = "(function(){var m="


def product_model(html):
    """The ProductDetailsResult message of a product viewer page (None when
    the page carries no product, e.g. an unknown id)."""
    i = (html or "").find(_MODEL_MARK)
    if i < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html[i + len(_MODEL_MARK):])
    except ValueError:
        return None
    best = None
    for value in data.values():
        cand = at(value, 6)
        if isinstance(cand, list) and len(cand) > 80 and isinstance(at(cand, 0), str):
            size = len(json.dumps(cand))
            if best is None or size > best[0]:
                best = (size, cand)
    return best[1] if best else None


def xhr_model(text):
    """The ProductDetailsResult of an /async/oapv answer."""
    body = (text or "").lstrip()
    if body.startswith(")]}'"):
        body = body[4:]
    try:
        data = json.loads(body.strip())
    except ValueError:
        return None
    model = data.get("ProductDetailsResult") if isinstance(data, dict) else None
    return model or None


def page_ei(html):
    m = re.search(r"kEI:'([^']+)'", html or "")
    return m.group(1) if m else None


def model_tokens(model):
    """The {"43": oapvfc, "44": xsrf} dict the offers XHR needs."""
    return next((x for x in (at(model, 11) or []) if isinstance(x, dict)), {})


def model_ids(model):
    """Product ids from P[11], completed from the headline offer ([26][0]:
    [0] offer docid, [1] productid, [3] gpcid, [33] catalogid) — a viewer
    opened with a minimal prds carries only the id it was opened with."""
    ids = at(model, 11) or []
    head = at(offers_of(model), 0, 26, 0) or []

    def get(seq, i):
        v = at(seq, i)
        return v if isinstance(v, str) and v else None
    return {"catalog_id": get(ids, 1) or get(head, 33), "product_id": get(ids, 2) or get(head, 1),
            "offer_id": get(ids, 3) or get(head, 0), "gpc_id": get(ids, 5) or get(head, 3),
            "image_id": get(ids, 0)}


def _images(model):
    out = []
    for block in (at(model, 61) or []):
        if not isinstance(block, dict):
            continue
        for value in block.values():
            for img in (at(value, 5) or []):
                url = at(img, 0)
                if isinstance(url, str) and url.startswith("http") and url not in [i["link"] for i in out]:
                    out.append({"link": url, "width": at(img, 1), "height": at(img, 2)})
    if not out:
        for img in (at(model, 34) or []):
            url = at(img, 0)
            if isinstance(url, str) and url.startswith("http"):
                out.append({"link": url, "width": at(img, 3), "height": at(img, 4)})
    return out


def _variants(model):
    out = []
    for group in (at(model, 71) or []):
        name = at(group, 0)
        if not isinstance(name, str):
            continue
        options = []
        for opt in (at(group, 1) or []):
            label = at(opt, 0)
            if not isinstance(label, str) or (label.startswith("Any ") and not at(opt, 4)):
                continue
            thumb = at(opt, 6, 0)
            options.append({"label": label, "is_selected": at(opt, 1) == 1, "is_available": at(opt, 2) != 0,
                            "thumbnail": thumb if isinstance(thumb, str) and thumb.startswith("http") else None})
        if options:
            out.append({"name": name, "options": options})
    return out


def _specifications(offers):
    """Attribute list of the first offer carrying one ([26][0][47][0])."""
    for offer in offers:
        attrs = at(offer, 26, 0, 47, 0)
        if not isinstance(attrs, list) or not attrs:
            continue
        specs, index = [], {}
        for attr in attrs:
            value = at(attr, 2, 1)
            if not isinstance(value, str) or not value:
                continue
            name = at(attr, 1)
            name = name[:1].upper() + name[1:] if isinstance(name, str) and name else "Feature"
            if name in index:
                if value not in index[name]["values"]:
                    index[name]["values"].append(value)
                continue
            index[name] = {"name": name, "values": [value]}
            specs.append(index[name])
        return [{"name": s["name"], "value": ", ".join(s["values"])} for s in specs]
    return []


def _store_rating(offer):
    block = at(offer, 24)
    return {
        "rating": to_float(at(block, 1, 1, 0)),
        "review_count": to_int(at(block, 1, 1, 2)),
        "domain": at(block, 1, 0, 0) if isinstance(at(block, 1, 0, 0), str) else None,
    }


def _first_text(*values):
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def parse_offer(offer, position=None):
    """One UnifiedOffers entry."""
    details = at(offer, 26, 0) or []
    currency = at(details, 10) if isinstance(at(details, 10), str) else None
    price = micros(at(details, 9))
    original = micros(at(details, 27, 0, 2, 0, 6))
    texts = at(offer, 48) or []
    if price is None:
        price, cur = money(at(texts, 0))
        currency = currency or cur
    if original is None and isinstance(at(texts, 2), str):
        original = parse_amount(at(texts, 2))
    rating = _store_rating(offer)
    # [49][0]: typed rows — code None availability, 1 delivery, 7 returns,
    # 16 store rating; [51] holds the same texts at fixed slots (2 / 4 / 6)
    rows = {}
    for row in (at(offer, 49, 0) or []):
        txt = at(row, 1, 0, 2, 0)
        if isinstance(txt, str) and txt and at(row, 0) not in rows:
            rows[at(row, 0)] = txt
    return_days = at(offer, 51, 6, 0, 0, 5, 2, 3)
    shipping_text = at(texts, 6) if isinstance(at(texts, 6), str) else None
    payments = at(offer, 60, 0)
    payment_methods = None
    if isinstance(payments, str) and payments:
        payment_methods = [p.strip() for p in re.split(r",|\band\b", re.sub(r"\s+accepted$", "", payments)) if p.strip()]
    store = {
        "id": at(offer, 1, 1),
        "name": at(offer, 1, 0),
        "domain": rating["domain"],
        "favicon": at(offer, 8, 2) if isinstance(at(offer, 8, 2), str) else None,
        "rating": rating["rating"],
        "review_count": rating["review_count"],
    }
    out = {
        "id": at(offer, 0),
        "title": _first_text(at(details, 28), at(offer, 44, 0)),
        "link": strip_tracking(_first_text(at(offer, 2, 0), at(details, 29))),
        "badge": _first_text(at(offer, 82, 0, 0, 0)),
        "availability": _first_text(rows.get(None), at(offer, 51, 4, 0, 0, 0)),
        "delivery": _first_text(rows.get(1), at(offer, 51, 2, 0, 0, 0)),
        "shipping_cost": parse_amount(shipping_text) if shipping_text else None,
        "returns": _first_text(rows.get(7), at(offer, 51, 6, 0, 0, 0)),
        "return_window_days": to_int(return_days) if isinstance(return_days, int) else None,
        "payment_methods": payment_methods,
        "is_tax_extra": at(texts, 13) == "+tax",
        "pricing": pricing(price, original, currency,
                           discount=percent(at(offer, 61, 1, 0, 0, 2, 0))),
        "store": store,
    }
    if position is not None:
        out = {"position": position, **out}
    return out


def offers_of(model):
    return [o for o in (at(model, 81, 0, 0) or []) if isinstance(o, list) and o and isinstance(o[0], str)]


def offer_count(model):
    return to_int(at(model, 81, 0, 4))


# ---- price insights / history --------------------------------------------------------------------

def _decode_date(raw):
    parts = {n: v for n, w, v in tokens.fields(raw) if w == 0}
    try:
        return f"{parts[1]:04d}-{parts[2]:02d}-{parts[3]:02d}"
    except (KeyError, TypeError, ValueError):
        return None


def decode_price_history(blob):
    """base64 price history -> [{"store", "points": [{"date", "price"}]}]."""
    out = []
    try:
        series = tokens.fields(tokens.b64decode(blob))
    except Exception:
        return out
    for num, wire, raw in series:
        if num != 1 or wire != 2:
            continue
        points, store = [], None
        try:
            items = tokens.fields(raw)
        except Exception:
            continue
        for n2, w2, v2 in items:
            if n2 == 1 and w2 == 2:
                try:
                    point = {n: (v, w) for n, w, v in tokens.fields(v2)}
                except Exception:
                    continue
                date = _decode_date(point[1][0]) if 1 in point and point[1][1] == 2 else None
                price = struct.unpack("<d", point[2][0])[0] if 2 in point and point[2][1] == 1 else None
                if date and price is not None:
                    points.append({"date": date, "price": round(price, 2)})
            elif w2 == 2 and store is None:
                try:
                    store = v2.decode("utf-8")
                except UnicodeDecodeError:
                    store = None
        if points:
            out.append({"store": store, "points": points})
    return out


def decode_events(blob):
    """base64 holiday markers -> [{"date", "name"}]."""
    out = []
    try:
        items = tokens.fields(tokens.b64decode(blob))
    except Exception:
        return out
    for num, wire, raw in items:
        if wire != 2:
            continue
        try:
            parts = {n: v for n, w, v in tokens.fields(raw)}
            out.append({"date": _decode_date(parts[1]), "name": parts[2].decode("utf-8")})
        except Exception:
            continue
    return out


def price_insights(model):
    block = at(model, 81, 0, 19) or []
    insight = at(block, 0) or []
    history = at(block, 6) or []
    currency = at(history, 1) if isinstance(at(history, 1), str) else None
    low, high = micros(at(insight, 3)), micros(at(insight, 4))
    return {
        "typical_price_range": {"min": low, "max": high, "currency": currency} if low or high else None,
        "price_history": decode_price_history(at(history, 0)) if isinstance(at(history, 0), str) else [],
        "events": decode_events(at(history, 9)) if isinstance(at(history, 9), str) else [],
        "currency": currency,
    }


# ---- related shelves / discussions -------------------------------------------------------------------

def _related_item(item, country, language, query):
    card = at(item, 12) or []
    ids = at(card, 11) or []
    cid = at(ids, 1) if isinstance(at(ids, 1), str) and at(ids, 1) else None
    pid = at(ids, 2) if isinstance(at(ids, 2), str) and at(ids, 2) else None
    ident = product_id(cid, pid, at(ids, 3) if isinstance(at(ids, 3), str) else None)
    currency = at(card, 52, 1, 1) if isinstance(at(card, 52, 1, 1), str) else None
    price = micros(at(card, 52, 1, 2))
    original = micros(at(card, 52, 3, 0, 2, 0, 2))
    if price is None:
        price, cur = money(at(card, 8), country)
        currency = currency or cur
    if original is None and isinstance(at(card, 40), str):
        original = parse_amount(at(card, 40))
    seller = at(card, 24) if isinstance(at(card, 24), str) else at(item, 19, 1, 3, 4)
    return {
        "id": ident,
        "title": at(card, 0) if isinstance(at(card, 0), str) else None,
        "link": product_link(ident, query, country, language),
        "thumbnail": at(card, 2, 0) if isinstance(at(card, 2, 0), str) else None,
        "seller": seller if isinstance(seller, str) else None,
        "rating": to_float(at(card, 4)),
        "review_count": to_int(at(card, 69)) or to_int(at(card, 3)),
        "pricing": pricing(price, original, currency),
    }


def related_sections(model, country="US", language="en", query=None):
    out = []
    for pos in (50, 68):
        shelf = at(model, pos) or []
        title = at(shelf, 23) if isinstance(at(shelf, 23), str) else None
        items = [_related_item(it, country, language, query) for it in (at(shelf, 0) or []) if isinstance(it, list)]
        items = [i for i in items if i["title"]]
        if items:
            out.append({"title": title, "products": items})
    return out


def discussions(model):
    out = []
    threads = at(model, 70, 0, 0)
    if not isinstance(at(threads, 0, 2, 0), str):     # tolerate one level less nesting
        threads = at(model, 70, 0)
    for thread in (threads or []):
        title, link = at(thread, 2, 0), at(thread, 2, 1)
        if not isinstance(title, str):
            continue
        comments = []
        for c in (at(thread, 8) or []):
            body = at(c, 1, 0)
            if isinstance(body, str) and body:
                comments.append({"text": body.strip(), "link": at(c, 1, 1), "votes": to_int(at(c, 2)),
                                 "is_top_answer": at(c, 0) == 1})
        out.append({
            "title": title,
            "link": link,
            "source": {"name": at(thread, 3, 0), "domain": at(thread, 3, 1)},
            "community": {"name": at(thread, 5, 0), "link": at(thread, 5, 1)} if at(thread, 5, 0) else None,
            "age": at(thread, 9) if isinstance(at(thread, 9), str) else None,
            "votes": to_int(at(thread, 6)),
            "comment_count": to_int(at(thread, 7)),
            "top_comments": comments,
        })
    return out


def product_rating(html):
    """(rating, review_count) from the viewer's product_rating header, when rendered."""
    m = re.search(r'data-attrid="product_rating"[^>]*aria-label="Rated ([\d.]+) out of 5, ([\d.,KkM]+) user reviews"',
                  html or "") or re.search(r'aria-label="Rated ([\d.]+) out of 5, ([\d.,KkM]+) user reviews"[^>]*'
                                           r'data-attrid="product_rating"', html or "")
    if not m:
        return None, None
    return to_float(m.group(1)), count_of(m.group(2))


def parse_product(html, model, country="US", language="en", query=None):
    offers = offers_of(model)
    ids = model_ids(model)
    ident = product_id(ids["catalog_id"], ids["product_id"], ids["offer_id"])
    rating, reviews = product_rating(html)
    insights = price_insights(model)
    price_range = _price_range(offers)
    if insights["typical_price_range"] and not insights["typical_price_range"]["currency"] and price_range:
        insights["typical_price_range"]["currency"] = price_range["currency"]
    description = at(model, 93, 0, 1, 0)
    return {
        "id": ident,
        "title": at(model, 0),
        "brand": at(model, 2) if isinstance(at(model, 2), str) else None,
        "link": product_link(ident, query, country, language),
        "description": clean_text(description) if isinstance(description, str) else None,
        "images": _images(model),
        "rating": rating,
        "review_count": reviews,
        "offer_count": offer_count(model),
        "price_range": price_range,
        "typical_price_range": insights["typical_price_range"],
        "variants": _variants(model),
        "specifications": _specifications(offers),
        "offers": [parse_offer(o, i) for i, o in enumerate(offers, 1)],
        "price_history": insights["price_history"],
        "discussions": discussions(model),
        "related_sections": related_sections(model, country, language, query),
        "ids": ids,
    }


def _price_range(offers):
    prices = [(micros(at(o, 26, 0, 9)), at(o, 26, 0, 10)) for o in offers]
    prices = [(p, c) for p, c in prices if p is not None]
    if not prices:
        return None
    return {"min": min(p for p, _ in prices), "max": max(p for p, _ in prices), "currency": prices[0][1]}


# ---- store pages ----------------------------------------------------------------------------------

def _review(item, kind):
    """A store review / web mention row."""
    ts = at(item, 2, 0)
    author = at(item, 3) or []
    source = at(item, 5) or []
    rating = at(item, 1)
    return {
        "id": at(item, 8, 1) if isinstance(at(item, 8, 1), str) else None,
        "text": clean_text(at(item, 0)) if isinstance(at(item, 0), str) else None,
        "rating": rating if isinstance(rating, int) and not isinstance(rating, bool) and 1 <= rating <= 5 else None,
        "date": _ts_iso(ts) if isinstance(ts, int) else None,
        "author": {"name": at(author, 0) if isinstance(at(author, 0), str) else None,
                   "photo": at(author, 1) if isinstance(at(author, 1), str) else None},
        "source": {"name": at(source, 0) if isinstance(at(source, 0), str) else None,
                   "icon": at(source, 1) if isinstance(at(source, 1), str) and at(source, 1).startswith("http") else None},
        "link": at(item, 6) if isinstance(at(item, 6), str) else None,
        "type": kind,
    }


_WRB_RE = re.compile(r'\["wrb\.fr","IJk5Wc",("(?:[^"\\]|\\.)*"|null)')


def review_rpc_payload(text):
    """An IJk5Wc batchexecute answer -> [null, next_cursor, rows] ([] when
    Google answered an empty page, None when it rejected the call)."""
    m = _WRB_RE.search(text or "")
    if not m or m.group(1) == "null":
        return None
    try:
        return json.loads(json.loads(m.group(1)))
    except ValueError:
        return None


def reviews_block(block, kind):
    """ds:14 / ds:17 / an IJk5Wc answer -> (reviews, next_cursor)."""
    items = [r for r in (at(block, 2) or []) if isinstance(r, list)]
    reviews = [_review(r, kind) for r in items]
    reviews = [r for r in reviews if r["text"]]
    cursor = at(block, 1) if isinstance(at(block, 1), str) and at(block, 1) else None
    return reviews, cursor


def _videos(block):
    out = []
    for v in (at(block, 0) or []):
        link = at(v, 0)
        if not isinstance(link, str):
            continue
        ts = at(v, 6, 1)
        out.append({
            "title": at(v, 2), "link": link, "thumbnail": at(v, 3),
            "platform": at(v, 5, 0), "view_count": to_int(at(v, 4)),
            "author": at(v, 6, 0), "date": _ts_iso(ts / 1000) if isinstance(ts, (int, float)) else None,
        })
    return out


_SOCIAL = {1: "instagram", 2: "youtube", 3: "facebook", 4: "x", 5: "tiktok", 6: "pinterest", 7: "linkedin"}
_THIRD_PARTY = {1: "ScamAdviser", 2: "Trustpilot"}


def parse_store(blocks, domain, country):
    """AF_initDataCallback blocks of a store page -> the store profile."""
    main = at(blocks.get("ds:6"), 0) or []
    info = at(main, 1) or []
    name = at(info, 0)
    histogram = [{"stars": to_int(at(r, 0)), "count": to_int(at(r, 1)),
                  "percent": round(at(r, 2) * 100) if isinstance(at(r, 2), (int, float)) else None}
                 for r in (at(blocks.get("ds:7"), 0, 0) or []) if isinstance(r, list)]
    badge_block = at(blocks.get("ds:4"), 0) or []
    badges = [at(badge_block, 1)] if isinstance(at(badge_block, 1), str) else []
    third_party = []
    for row in (at(badge_block, 2, 2, 0) or []):
        score, kind, link = at(row, 0), at(row, 1), at(row, 2)
        if score is None or not isinstance(link, str):
            continue
        third_party.append({"source": _THIRD_PARTY.get(kind) or urlparse(link).netloc or None, "score": score,
                            "link": link})
    insights = []
    for row in (at(blocks.get("ds:10"), 0) or []):
        topic = at(row, 1)
        if not isinstance(topic, str):
            continue
        insights.append({
            "topic": topic,
            "mention_count": to_int(at(row, 2)),
            "positive": {"label": at(row, 4, 0), "percent": to_int(at(row, 4, 1))},
            "negative": {"label": at(row, 5, 0), "percent": to_int(at(row, 5, 1))},
        })
    policy = at(blocks.get("ds:2"), 1, 1) or []
    socials = [{"platform": _SOCIAL.get(at(s, 0), "other"), "link": at(s, 1)}
               for s in (at(info, 3) or []) if isinstance(at(s, 1), str)]
    trending = []
    for p in (at(blocks.get("ds:9"), 0) or []):
        link = at(p, 2)
        if not isinstance(link, str):
            continue
        trending.append({
            "id": at(p, 8) if isinstance(at(p, 8), str) else None,
            "title": at(p, 3),
            "link": strip_tracking(link),
            "thumbnail": at(p, 5),
            "pricing": pricing(to_float(at(p, 4, 1)), currency=at(p, 4, 0)),
        })
    highlighted = []
    for row in (at(blocks.get("ds:11"), 0) or []):
        review = at(row, 0)
        if not isinstance(review, list):
            continue
        item = _review(review, "review")
        item["summary"] = clean_text(at(row, 2, 2, 0)) if isinstance(at(row, 2, 2, 0), str) else None
        item["helpful_count"] = to_int(at(row, 1, 1))
        highlighted.append(item)
    summary = at(blocks.get("ds:15"), 0, 0)
    google_reviews, _ = reviews_block(blocks.get("ds:14"), "review")
    mentions, _ = reviews_block(blocks.get("ds:17"), "web_mention")
    return {
        "domain": domain,
        "name": name if isinstance(name, str) else None,
        "website": at(info, 5) if isinstance(at(info, 5), str) else None,
        "logo": at(info, 8) if isinstance(at(info, 8), str) else None,
        "country": country,
        "rating": to_float(at(main, 0, 0)),
        "review_count": to_int(at(main, 0, 1)),
        "rating_distribution": histogram,
        "badges": badges,
        "review_summary": clean_text(summary) if isinstance(summary, str) else None,
        "insights": insights,
        "return_policy": {"link": at(policy, 0), "window_days": to_int(at(policy, 1))} if at(policy, 0) else None,
        "third_party_ratings": third_party,
        "social_profiles": socials,
        "highlighted_reviews": highlighted,
        "recent_reviews": google_reviews,
        "web_mentions": mentions,
        "trending_products": trending,
        "videos": _videos(blocks.get("ds:12")),
        "video_reviews": _videos(blocks.get("ds:13")),
    }


# ---- home -------------------------------------------------------------------------------------------

def parse_home(html, country="US", language="en"):
    doc = parse_document(html)
    cards = parse_cards(doc, html, country, language, None, scope=None)
    sections, index = [], {}
    for card in cards:
        title = card.pop("section", None) or "Products"
        if title not in index:
            index[title] = {"title": title, "products": []}
            sections.append(index[title])
        card["position"] = len(index[title]["products"]) + 1
        index[title]["products"].append(card)
    return {"sections": sections}


def autocomplete_rows(data):
    """gws-wiz-modeless-shopping rows -> suggestion texts (markup stripped)."""
    rows = at(data, 0) or []
    out = []
    for row in rows:
        t = clean_text(at(row, 0)) if isinstance(at(row, 0), str) else None
        if t and t not in out:
            out.append(t)
    return out


def encode_query(q):
    return quote_plus(q or "")
