"""/google-lens/* endpoints backed by the Lens results pages (identity
flows, see google_lens/fetch.py): search, visual matches, exact matches,
products, AI overview.

Every call: the picture is downloaded / decoded (images.load), cropped to
`region`, re-encoded as the <= 1024 px JPEG google.com uploads, then ONE
identity flow runs the upload and the tabs it needs. /goto links (web
results, exact matches) are resolved to publisher URLs afterwards
(google_search.serp.resolve_links, cached per token).
"""
from google_lens import fetch, images, parsers
from google_lens.errors import GoogleBadRequest, GoogleUpstreamError
from google_search import identity, serp

identity.start_keeper()   # the SERP identity pool is shared with /google-search/* (GOOGLE_SEARCH_MIN_IDENTITIES)


def _prepare(image, region):
    picture = images.load(image)
    jpeg, _, _ = images.for_search(picture, region)
    return picture, jpeg


def _optional(step):
    """A secondary request (qfmetadata, AI overview) must not sink the
    search: an upstream error there just leaves its part empty. Walls and
    captchas still propagate (fetch.flow reruns the whole flow)."""
    try:
        return step()
    except (GoogleUpstreamError, GoogleBadRequest):
        return None


def _resolve(result):
    """/goto wrappers -> publisher URLs (the wrapper itself is dropped)."""
    serp.resolve_links(result)
    _drop_key(result, "google_link")
    return result


def _drop_key(obj, key):
    if isinstance(obj, dict):
        obj.pop(key, None)
        for value in obj.values():
            _drop_key(value, key)
    elif isinstance(obj, list):
        for value in obj:
            _drop_key(value, key)


def _renumber(items):
    for i, item in enumerate(items, 1):
        item["position"] = i
    return items


def _merge_matches(*lists):
    """Visual-match lists of several tabs -> one list, first seen wins."""
    out, seen = [], set()
    for items in lists:
        for item in items or []:
            if item["link"] in seen:
                continue
            seen.add(item["link"])
            out.append(dict(item))
    return _renumber(out)


# ---- endpoints -------------------------------------------------------------------------------

def search(image, region=None, country="US", language="en", include_ai_overview=False):
    """The All tab: recognized entities, visual matches (with offers),
    web results, short videos, detected regions [+ the AI overview]."""
    picture, jpeg = _prepare(image, region)

    def work(session):
        page, _ = session.upload(jpeg, language=language, country=country)
        qf = _optional(session.qfmetadata)
        ai = _optional(session.ai_overview) if include_ai_overview else None
        return page, qf, ai

    page, qf, ai = fetch.flow(work, "google-lens search")
    parsed = parsers.all_tab(page, country)
    parsers.enrich_prices(parsed["visual_matches"], parsers.qf_matches(qf))
    result = {
        "entities": parsed["entities"],
        "visual_matches": parsed["visual_matches"],
        "web_results": parsed["web_results"],
        "short_videos": parsed["short_videos"],
        "detected_objects": parsers.detected_objects(qf),
    }
    if include_ai_overview:
        result["ai_overview"] = parsers.ai_overview(ai) if ai else None
    result["image"] = picture.info()
    return _resolve(result)


def visual_matches(image, region=None, country="US", language="en"):
    """Every visual match Lens shows: the Visual matches tab, then the All
    tab's cards it does not repeat (deduplicated on the page link)."""
    picture, jpeg = _prepare(image, region)

    def work(session):
        page, _ = session.upload(jpeg, language=language, country=country)
        tab = session.tab("visual_matches")
        qf = _optional(session.qfmetadata)
        return page, tab, qf

    page, tab, qf = fetch.flow(work, "google-lens visual-matches")
    all_cards = parsers.visual_matches(page, country)
    tab_cards = parsers.visual_matches(tab, country) if tab else []
    matches = parsers.enrich_prices(_merge_matches(tab_cards, all_cards), parsers.qf_matches(qf))
    return {
        "entities": parsers.page_entities(page),
        "visual_matches": matches,
        "image": picture.info(),
    }


def exact_matches(image, region=None, country="US", language="en"):
    """Pages that carry this exact picture (reverse image search)."""
    picture, jpeg = _prepare(image, region)

    def work(session):
        session.upload(jpeg, language=language, country=country)
        return session.tab("exact_matches")

    tab = fetch.flow(work, "google-lens exact-matches")
    matches = parsers.exact_matches(tab) if tab else []
    result = _resolve({"exact_matches": matches, "image": picture.info()})
    for item in matches:
        link = item.get("link") or ""
        item["domain"] = parsers.host(link) if "/goto?" not in link else None
    return result


def products(image, region=None, country="US", language="en"):
    """Offers for what the picture shows: price, currency, merchant,
    stock, product rating and review count, image."""
    picture, jpeg = _prepare(image, region)

    def work(session):
        page, _ = session.upload(jpeg, language=language, country=country)
        qf = _optional(session.qfmetadata)
        tab = _optional(lambda: session.tab("visual_matches"))
        return page, tab, qf

    page, tab, qf = fetch.flow(work, "google-lens products")
    cards = _merge_matches(parsers.visual_matches(tab, country) if tab else [], parsers.visual_matches(page, country))
    items = parsers.products(cards, parsers.qf_matches(qf), country)
    return {
        "entities": parsers.page_entities(page),
        "products": _renumber(items),
        "image": picture.info(),
    }


def ai_overview(image, question=None, region=None, country="US", language="en"):
    """Google's AI answer about the picture — or about `question` asked of
    it (Lens multisearch: picture + text) — with its cited sources."""
    picture, jpeg = _prepare(image, region)

    def work(session):
        session.upload(jpeg, language=language, country=country, query=question)
        return session.ai_overview()

    streamed = fetch.flow(work, "google-lens ai-overview")
    overview = parsers.ai_overview(streamed) if streamed else None
    result = {
        "question": question,
        "is_available": overview is not None,
        "text": overview["text"] if overview else None,
        "sources": overview["sources"] if overview else [],
        "image": picture.info(),
    }
    return _resolve(result)

