"""Parsers for the Lens results pages and their side channels.

Shapes (verified live 2026-09-26, hl=en/de, gl=US/GB/DE):

  VISUAL MATCH CARDS (All tab udm=26, Visual matches tab udm=44)
    Each card's thumbnail element carries jsdata "XZxcdf;<docid>;<KEY>";
    the page's `var m={…}` map holds KEY -> [0, tbnid, [thumb, w, h],
    [image, w, h], null, 0, {"2003": [null, docid, page link, title, …×8,
    source name (12), …×4, host (17)]}]. Everything but the offer is there;
    price ("$160*"), rating ("5.0"), review count ("(6)") and stock ("In
    stock") are only the card's visible text.
  ENTITY CHIPS ("Related search(es)")
    a[href*="kgmid="]: Lens's identification of the subject — a Knowledge
    Graph entity (Eiffel Tower /m/02j81, Danny DeVito /m/0q9kd, Common
    sunflower /m/0ftb8) with a Google search link.
  EXACT MATCH CARDS (udm=48)
    a.ngTNl[href^="/goto"] in #rso; text segments: title, [date], "·",
    [W x H of the page's copy], source; the thumbnail is a deferred data URI.
  WEB RESULTS / SHORT VIDEOS (All tab)
    the regular SERP blocks: google_search.serp_parsers.parse_web.
  QFMETADATA (lens.google.com/qfmetadata, ")]}'" JSON)
    [0][1] regions [[name, [[cx, cy, w, h, rot, coord]], …]]; [0][3] =
    [[region name], [[box], "<base64 protobuf>"]] whose field 5.1[*].2 are
    up to ~120 shopping-grade matches (see qf_matches).
  AI OVERVIEW (/async/folsrch) — google_lens.stream, then the answer block
    after the "AI Mode replied:" heading; source cards ride in HTML comments
    `TgQPHd|||[[group, [title, snippet, favicon, site, [name], goto…]]]`.
"""
import html as htmllib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse

from google_jobs.markup import JsData, jsdata_refs
from google_lens import proto, stream
from google_search import serp_parsers as sp
from google_search.shared import at, to_float
from google_shopping.parsers import currency_of, micros, parse_amount, strip_tracking

SITE = "https://www.google.com"
_RATING_RE = re.compile(r"^\d(?:[.,]\d)?$")
_REVIEWS_RE = re.compile(r"^\(([\d.,\s]+[KkMm]?)\)$")
_DIMS_RE = re.compile(r"^(\d[\d,.]*)\s*[x×]\s*(\d[\d,.]*)$")
_PRICE_RE = re.compile(r"\d")
_AGO_RE = re.compile(r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago$", re.I)
_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y",
                 "%d. %b. %Y", "%d. %B %Y", "%b %Y", "%B %Y")
_IN_STOCK = {"in stock", "auf lager", "en stock", "disponible", "in magazzino", "op voorraad", "em estoque", "en existencia"}
_OUT_OF_STOCK = {"out of stock", "nicht auf lager", "ausverkauft", "rupture de stock", "agotado", "esaurito",
                 "niet op voorraad", "esgotado", "sold out"}
_AIO_TAIL = ("Shared", "AI can make mistakes", "About this response", "Show more")


# ---- small helpers --------------------------------------------------------------------------

def iso_date(value):
    """'Jun 23, 2020' | '23.06.2020' | '3 days ago' -> 'YYYY-MM-DD' (else None)."""
    value = (value or "").strip().rstrip(".")
    if not value:
        return None
    ago = _AGO_RE.match(value)
    if ago:
        n, unit = int(ago.group(1)), ago.group(2).lower()
        days = {"minute": 0, "hour": 0, "day": n, "week": 7 * n, "month": 30 * n, "year": 365 * n}[unit]
        return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def count(value):
    return sp.count_of(value)


def box(cx, cy, w, h, rotation=None):
    """Lens center box (0-1) -> {x, y, width, height} with a top-left origin."""
    if None in (cx, cy, w, h):
        return None
    out = {"x": round(max(0.0, cx - w / 2), 5), "y": round(max(0.0, cy - h / 2), 5),
           "width": round(w, 5), "height": round(h, 5)}
    if rotation:
        out["rotation_degrees"] = round(rotation * 57.29577951308232, 2)
    return out


def host(link):
    try:
        return urlparse(link).netloc or None
    except ValueError:
        return None


def _search_link(href):
    """A Google /search link without click-tracking params."""
    if not href:
        return None
    parsed = urlparse(htmllib.unescape(href))
    params = parse_qs(parsed.query, keep_blank_values=True)
    keep = {k: v[0] for k, v in params.items() if k in ("q", "kgmid", "hl", "gl")}
    return f"{SITE}/search?{urlencode(keep)}" if keep.get("q") else None


def _price(segment, country):
    """'$160*' | '¥5,200*' | '1.234,56 €' -> {amount, currency}; None when
    the segment is not JUST a price (a title like "Keule2010 | Flickr" holds
    digits and "kr" but is not one)."""
    raw = (segment or "").rstrip("*").strip()
    if not raw or len(raw) > 24 or not _PRICE_RE.search(raw):
        return None
    if len(re.sub(r"[\d\s.,'  ]", "", raw)) > 5 or not currency_of(raw, country):
        return None
    amount = parse_amount(raw)
    if amount is None:
        return None
    return {"amount": amount, "currency": currency_of(raw, country)}


def _stock(segments):
    for seg in segments:
        low = seg.lower()
        if low in _IN_STOCK:
            return True
        if low in _OUT_OF_STOCK:
            return False
    return None


def _segments(el):
    return [s for s in (" ".join(t.split()) for t in el.itertext()) if s and s != "·"]


# ---- entity chips -------------------------------------------------------------------------

def page_entities(html):
    return entities(sp.parse_document(html), sp.deferred_images(html))


def entities(doc, dmap):
    """Knowledge Graph entities Lens recognized (the "Related search" chips)."""
    out, seen = [], set()
    for a in doc.xpath("//a[contains(@href,'kgmid=')]"):
        params = parse_qs(urlparse(htmllib.unescape(a.get("href") or "")).query)
        kgmid = (params.get("kgmid") or [None])[0]
        name = sp.text(a) or (params.get("q") or [None])[0]
        if not kgmid or kgmid in seen or not name:
            continue
        seen.add(kgmid)
        out.append({
            "name": name,
            "knowledge_graph_id": kgmid,
            "search_link": _search_link(a.get("href")),
            "thumbnail": sp.image_src(sp.first(a, "img"), dmap),
        })
    return out


# ---- visual matches -----------------------------------------------------------------------

def _card_of(el):
    """The card around a match's thumbnail: the nearest ancestor holding a
    /goto link, never one spanning a second match."""
    node, best = el, el
    while node is not None:
        if len(node.xpath(".//*[contains(@jsdata,'XZxcdf')]")) + ("XZxcdf" in (node.get("jsdata") or "")) > 1:
            break
        best = node
        if node.xpath(".//a[starts-with(@href,'/goto')]"):
            break
        node = node.getparent()
    return best


def visual_matches(html, country="US"):
    """Match cards of a Lens page (All or Visual matches tab), page order."""
    doc = sp.parse_document(html)
    jd = JsData(html)
    out, seen = [], set()
    for el in doc.xpath("//*[contains(@jsdata,'XZxcdf')]"):
        try:
            data = jd.get(jsdata_refs(el).get("XZxcdf"))
            if not isinstance(data, list):
                continue
            meta = at(data, 6, "2003") or []
            link = strip_tracking(at(meta, 2))
            if not link or link in seen:
                continue
            seen.add(link)
            card = _card_of(el)
            segments = _segments(card)
            price = next((p for p in (_price(s, country) for s in segments[:2]) if p), None)
            rating = next((to_float(s.replace(",", ".")) for s in segments if _RATING_RE.match(s)), None)
            reviews = next((count(m.group(1)) for m in (_REVIEWS_RE.match(s) for s in segments) if m), None)
            thumb, image = at(data, 2) or [], at(data, 3) or []
            out.append({
                "position": len(out) + 1,
                "title": at(meta, 3),
                "link": link,
                "source": at(meta, 12),
                "domain": at(meta, 17) or host(link),
                "price": price,
                "rating": rating if rating is not None and 0 < rating <= 5 else None,
                "review_count": reviews,
                "is_in_stock": _stock(segments),
                "image": {"link": at(image, 0), "width": at(image, 1), "height": at(image, 2)}
                if at(image, 0) else None,
                "thumbnail": at(thumb, 0),
            })
        except Exception:
            continue
    return out


# ---- exact matches ------------------------------------------------------------------------

def exact_matches(html):
    """Pages carrying the same picture (udm=48), page order; links are /goto
    wrappers (serp.resolve_links rewrites them)."""
    doc = sp.parse_document(html)
    dmap = sp.deferred_images(html)
    rso = sp.first(doc, "#rso")
    out = []
    for a in (rso if rso is not None else doc).cssselect('a[href^="/goto"]'):
        try:
            segments = _segments(a)
            if len(segments) < 2:
                continue
            title, source = segments[0], segments[-1]
            date, width, height, price = None, None, None, None
            for seg in segments[1:-1]:
                dims = _DIMS_RE.match(seg)
                if dims:
                    width, height = sp.count_of(dims.group(1)), sp.count_of(dims.group(2))
                elif date is None and iso_date(seg):
                    date = iso_date(seg)
                elif price is None and _price(seg, None):
                    price = _price(seg, None)
            out.append({
                "position": len(out) + 1,
                "title": title,
                "link": sp.absolute(a.get("href")),
                "source": source,
                "domain": None,
                "date": date,
                "price": price,
                "image": {"width": width, "height": height} if width and height else None,
                "thumbnail": sp.image_src(sp.first(a, 'img[id^="dimg_"]'), dmap),
            })
        except Exception:
            continue
    return out


# ---- web results / short videos -------------------------------------------------------------

def _web_item(item):
    return {
        "position": item.get("position"),
        "title": item.get("title"),
        "link": item.get("link"),
        "source": item.get("source"),
        "domain": item.get("domain"),
        "snippet": item.get("snippet"),
        "date": iso_date(item.get("date")) or None,
        "sitelinks": [{"title": s.get("title"), "link": s.get("link")} for s in item.get("sitelinks") or []],
        "more_results": [{"title": s.get("title"), "link": s.get("link")} for s in item.get("sub_results") or []],
    }


def _video_item(item):
    return {
        "position": item.get("position"),
        "title": item.get("title"),
        "link": item.get("link"),
        "platform": item.get("platform"),
        "channel": item.get("channel"),
        "date": iso_date(item.get("date")),
        "duration": item.get("duration"),
        "thumbnail": item.get("thumbnail"),
    }


def all_tab(html, country="US"):
    """Everything the All tab shows (visual matches, entities, web results,
    short videos). Web links are /goto wrappers until resolve_links runs."""
    doc = sp.parse_document(html)
    dmap = sp.deferred_images(html)
    web = sp.parse_web(html)
    return {
        "entities": entities(doc, dmap),
        "visual_matches": visual_matches(html, country),
        "web_results": [_web_item(i) for i in web.get("organic_results") or []],
        "short_videos": [_video_item(v) for v in web.get("short_videos") or []],
    }


# ---- qfmetadata -----------------------------------------------------------------------------

def _qf_payload(text):
    body = (text or "").lstrip()
    if body.startswith(")]}'"):
        body = body[4:]
    try:
        data = json.loads(body)
    except ValueError:
        return None
    for entry in data if isinstance(data, list) else []:
        if isinstance(entry, list) and entry and entry[0] == "fetch_query_formulation_metadata_response":
            return entry
    return None


_REGION_TYPES = (("SalientRegion", "salient_region"), ("DetectedObject-WholeImage", "whole_image"),
                 ("DetectedObject-RepresentsWholeImage", "main_subject"), ("DetectedObject", "object"), ("text:", "text"))


def detected_objects(qf_text):
    """Regions Lens found (whole image, main subject, objects, text blocks)."""
    entry = _qf_payload(qf_text)
    out = []
    for region in (at(entry, 1) or []):
        try:
            name = region[0]
            coords = at(region, 1, 0) or []
            kind = next((k for prefix, k in _REGION_TYPES if str(name).startswith(prefix)), "object")
            out.append({"type": kind, "bounding_box": box(*(coords[:4] + [None] * (4 - len(coords[:4]))))})
        except Exception:
            continue
    return out


_STOCK_CODES = {1: True, 3: False}


def _long_strings(value, depth=0):
    """Every base64-looking string (a region's match protobuf) under value."""
    if isinstance(value, str):
        if len(value) > 200 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", value):
            yield value
    elif isinstance(value, list) and depth < 6:
        for item in value:
            yield from _long_strings(item, depth + 1)


def qf_matches(qf_text):
    """Structured shopping-grade matches of every qfmetadata region.
    Match message (field numbers, 2026-09-26):
      2 title | 5 image {2 image url, 3 imgres link, 4 thumbnail, 5/6 thumb
      w/h, 9/10 image w/h, 11 page title, 12 host, 14 page link} | 6 offer
      {3 page link, 4 review count, 5 rating (float), 17 {1 host, 2 price
      micros, 3 currency, 4 link, 18 stock 1 in / 3 out, 28 merchant}} |
      23 page language | 24 store country"""
    entry = _qf_payload(qf_text)
    out, seen = [], set()
    for b64 in _long_strings(at(entry, 3)):
        try:
            root = proto.message(proto.unb64(b64))
        except Exception:
            continue
        if not root.has(5):
            continue
        for wrapper in root.msgs(5):
            for item in wrapper.msgs(1):
                m = item.msg(2)
                if m is None:
                    continue
                img, offer = m.msg(5), m.msg(6)
                shop = offer.msg(17) if offer else None
                link = strip_tracking((img.str(14) if img else None) or (offer.str(3) if offer else None))
                if not link or link in seen:
                    continue
                seen.add(link)
                price = micros(shop.int(2)) if shop and shop.int(2) is not None else None
                rating = offer.float(5) if offer else None
                out.append({
                    "title": (img.str(11) if img else None) or m.str(2),
                    "product_title": m.str(2),
                    "link": link,
                    "source": (shop.str(28) if shop else None) or None,
                    "domain": (img.str(12) if img else None) or (shop.str(1) if shop else None),
                    "price": {"amount": price, "currency": shop.str(3)} if price is not None and shop.str(3) else None,
                    "rating": round(rating, 2) if rating else None,
                    "review_count": (offer.int(4) if offer else None),
                    "is_in_stock": _STOCK_CODES.get(shop.int(18)) if shop else None,
                    "store_country": m.str(24),
                    "page_language": m.str(23),
                    "image": {"link": img.str(2), "width": img.int(9), "height": img.int(10)} if img and img.str(2) else None,
                    "thumbnail": img.str(4) if img else None,
                })
    return out


def products(visual, qf, country="US"):
    """Matches that are offers: every priced qfmetadata match plus every
    priced visual-match card, merged on the page link (the card's rating /
    review count / stock fill gaps in the structured record)."""
    by_link = {}
    for item in qf:
        if item.get("price"):
            by_link[item["link"]] = dict(item)
    for card in visual:
        if not card.get("price"):
            continue
        rec = by_link.get(card["link"])
        if rec is None:
            by_link[card["link"]] = {
                "title": card["title"], "product_title": None, "link": card["link"], "source": card["source"],
                "domain": card["domain"], "price": card["price"], "rating": card["rating"],
                "review_count": card["review_count"], "is_in_stock": card["is_in_stock"], "store_country": None,
                "page_language": None, "image": card["image"], "thumbnail": card["thumbnail"],
            }
        else:
            for key in ("rating", "review_count", "is_in_stock", "source", "image", "thumbnail"):
                if rec.get(key) is None and card.get(key) is not None:
                    rec[key] = card[key]
    out = []
    for rec in by_link.values():
        rec = {"position": len(out) + 1, **rec}
        out.append(rec)
    return out


def enrich_prices(visual, qf):
    """Replace a card's symbol-guessed currency with the structured one."""
    exact = {m["link"]: m for m in qf if m.get("price")}
    for card in visual:
        rec = exact.get(card["link"])
        if rec and card.get("price"):
            card["price"] = dict(rec["price"])
    return visual


# ---- AI overview ------------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--TgQPHd\|\|\|(.*?)-->", re.S)


def _comment_records(html):
    for raw in _COMMENT_RE.findall(html or ""):
        try:
            data = json.loads(htmllib.unescape(raw))
        except ValueError:
            continue
        for rec in data if isinstance(data, list) else []:
            if isinstance(rec, list):
                yield rec


def ai_sources(html):
    """Cited sources: the full cards (title, snippet, site, name, link),
    each /goto link replaced by the chip's direct link when the chip names it."""
    direct = {}
    cards, seen = [], set()
    for rec in _comment_records(html):
        if at(rec, 1) is None and isinstance(at(rec, 7), str) and at(rec, 5):
            direct[(rec[0], at(rec, 5))] = rec[7]
            continue
        body = at(rec, 1)
        if not (isinstance(body, list) and len(body) > 5 and isinstance(at(body, 0), str)
                and isinstance(at(body, 3), str)):
            continue
        name = at(body, 4, 0)
        goto = at(body, 5)
        key = (at(body, 0), at(body, 3))
        if key in seen:
            continue
        seen.add(key)
        thumb = at(body, 11)
        cards.append({
            "group": rec[0],
            "title": at(body, 0),
            "snippet": at(body, 1),
            "link": sp.absolute(goto) if isinstance(goto, str) else None,
            "source": name,
            "domain": host(at(body, 3)),
            "thumbnail": at(thumb, 0) if isinstance(thumb, list) and isinstance(at(thumb, 0), str) else None,
        })
    for card in cards:
        link = direct.get((card["group"], card["source"]))
        if link:
            card["link"] = link
    out = []
    for card in cards:
        card.pop("group", None)
        out.append({"position": len(out) + 1, **card})
    return out


def _answer_block(doc):
    """The answer container (the heading itself removed)."""
    # "AI Mode replied:" (plain Lens search) | "AI Mode reply for <question>" (multisearch)
    heading = next(iter(doc.xpath("//h3[starts-with(normalize-space(.), 'AI Mode repl')]")), None)
    if heading is None:
        return None
    block = heading.getparent()
    heading.drop_tree()
    return block


def _drop_card_lists(block):
    """Answers to shopping questions embed a grid of offer cards (price,
    store, title, stock — each with one /goto link); it is not answer text."""
    for el in list(block.iter()):
        if el is block or el.getparent() is None or not isinstance(el.tag, str):
            continue
        kids = [c for c in el if isinstance(c.tag, str)]
        if len(kids) < 3 or el.tag in ("ul", "ol") or any(c.tag == "li" for c in kids):
            continue                  # a bulleted answer cites one source per bullet: keep it
        cards = sum(1 for c in kids if len(c.xpath('.//a[starts-with(@href,"/goto")]')) == 1)
        if cards >= 3 and cards >= 0.75 * len(kids):
            el.drop_tree()


def ai_overview(stream_text):
    """The streamed AI overview -> {text, sources} (None when Google gave none)."""
    assembled = stream.assemble(stream_text)
    if not assembled:
        return None
    doc = sp.parse_document('<meta charset="utf-8"><div>' + assembled + "</div>")
    block = _answer_block(doc)
    if block is None:
        return None
    _drop_card_lists(block)
    block.set("id", "eKIzJc")
    parsed = sp.parse_ai_overview(doc) or {}
    lines = []
    for line in (parsed.get("text") or "").split("\n"):
        line = line.replace("\xa0", " ").strip()
        if not line:
            continue
        if line.startswith(_AIO_TAIL) or re.fullmatch(r"(Shared)?\s*\d+ files?", line):
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        return None
    return {"text": text, "sources": ai_sources(assembled)}


# ---- OCR / translation (lens_api objects response) ------------------------------------------------

_DIRECTIONS = {0: "left_to_right", 1: "right_to_left", 2: "top_to_bottom"}


def _geometry(msg, size):
    """Geometry{1 CenterRotatedBox{cx, cy, w, h, rotation_z, coordinate_type}}
    -> box; IMAGE coordinates (type 2) are normalized with the sent size."""
    g = msg.msg(1) if msg is not None else None
    if g is None:
        return None
    cx, cy, w, h = g.float(1), g.float(2), g.float(3), g.float(4)
    if None in (cx, cy, w, h):
        cx, cy, w, h = (cx or 0.0), (cy or 0.0), (w or 0.0), (h or 0.0)
    if g.int(6) == 2 and size and size[0] and size[1]:
        cx, w = cx / size[0], w / size[0]
        cy, h = cy / size[1], h / size[1]
    return box(cx, cy, w, h, g.float(5))


def _words_text(words):
    return "".join((w.str(2) or "") + (w.str(3) or "") for w in words).strip()


def text_layout(objects, size, level="lines"):
    """LensOverlayObjectsResponse -> {language, text, paragraphs}."""
    text_msg = objects.msg(3) if objects is not None else None
    layout = text_msg.msg(1) if text_msg is not None else None
    paragraphs, full = [], []
    for p in (layout.msgs(1) if layout is not None else []):
        lines = []
        for ln in p.msgs(2):
            words = ln.msgs(1)
            line = {"text": _words_text(words), "bounding_box": _geometry(ln.msg(2), size)}
            if level == "words":
                line["words"] = [{
                    "text": w.str(2),
                    "bounding_box": _geometry(w.msg(4), size),
                    "is_formula": w.int(5) == 1,
                    "latex": (w.msg(6).str(1) if w.msg(6) else None) or None,
                } for w in words]
            lines.append(line)
        ptext = "\n".join(l["text"] for l in lines if l["text"])
        full.append(ptext)
        para = {
            "position": len(paragraphs) + 1,
            "text": ptext,
            "language": p.str(5) or None,
            "writing_direction": _DIRECTIONS.get(p.int(4) or 0),
            "bounding_box": _geometry(p.msg(3), size),
        }
        if level in ("lines", "words"):
            para["lines"] = lines
        paragraphs.append(para)
    return {
        "language": (text_msg.str(2) if text_msg is not None else None) or None,
        "text": "\n\n".join(t for t in full if t) or None,
        "paragraphs": paragraphs,
    }


_TRANSLATION_STATUS = {1: "success", 2: "server_error", 3: "unsupported_language_pair", 4: "same_language",
                       5: "unknown_source_language", 6: "invalid_request", 7: "deadline_exceeded",
                       8: "empty_translation", 9: "no_op_translation"}


def translation(objects, size):
    """Per-paragraph translations (deep gleams) aligned with the OCR
    paragraphs they cover (DeepGleamData.visual_object_id "text:0:<id>")."""
    layout = text_layout(objects, size, level="paragraphs")
    originals = layout["paragraphs"]
    items, source_lang, target_lang, status = [], None, None, None
    for i, gleam in enumerate(objects.msgs(4) if objects is not None else []):
        t = gleam.msg(10)
        if t is None:
            continue
        code = t.msg(1).int(1) if t.msg(1) else None
        status = status or _TRANSLATION_STATUS.get(code)
        source_lang = source_lang or t.str(3)
        target_lang = target_lang or t.str(2)
        original = originals[i] if i < len(originals) else None
        items.append({
            "position": len(items) + 1,
            "original_text": original["text"] if original else None,
            "translated_text": t.str(4),
            "bounding_box": original["bounding_box"] if original else None,
        })
    return {
        "status": status or ("success" if items else None),
        "source_language": source_lang or layout["language"],
        "target_language": target_lang,
        "original_text": layout["text"],
        "translated_text": "\n\n".join(i["translated_text"] for i in items if i["translated_text"]) or None,
        "paragraphs": items,
    }

