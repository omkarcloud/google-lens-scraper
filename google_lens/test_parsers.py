"""Offline tests for google_lens: page / side-channel parsers, the folsrch
stream, the Lens API protobuf, references and schemas against captured
responses (google_lens/fixtures/*.gz, 2026-09-26, hl=en gl=US unless noted):

  nike_all            All tab (udm=26) for a Nike Dunk High photo (product)
  nike_visual         its Visual matches tab (udm=44)
  nike_qf             its lens.google.com/qfmetadata JSON
  nike_de_all         the same photo, hl=de gl=DE
  sunflower_all       All tab, sunflower photo (3 Knowledge Graph entities)
  eiffel_exact        Exact matches tab (udm=48), Eiffel Tower photo
  text_exact          Exact matches tab with no match (a textbook page)
  eiffel_ai / danny_ai  /async/folsrch AI overview streams
  nike_q_ai           folsrch for a question (multisearch "what is the retail
                      price of these"): bulleted answer + an offer grid
  crupload_ocr        Lens API answer (AUTO filter) for the textbook page
  crupload_translate_es  the same with the TRANSLATE filter, target es

    cd scrapers && python -m pytest google_lens/test_parsers.py -q
"""
import gzip
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_lens import parsers as P, proto, refs, schemas, stream  # noqa: E402
from google_lens.images import _page_image  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
OCR_SIZE = (522, 325)        # the JPEG the crupload fixtures were made from


def load(name):
    with gzip.open(os.path.join(FX, name + ".gz"), "rt", encoding="utf-8", errors="replace") as f:
        return f.read()


def load_bytes(name):
    with gzip.open(os.path.join(FX, name + ".gz"), "rb") as f:
        return f.read()


def _in_unit(b):
    return b and all(0 <= b[k] <= 1.0001 for k in ("x", "y", "width", "height"))


# ---- visual matches / entities ------------------------------------------------------------------

def test_all_tab_visual_matches():
    cards = P.visual_matches(load("nike_all"), "US")
    assert len(cards) >= 50
    assert all(c["link"].startswith("http") and c["title"] and c["domain"] and c["image"]["link"] for c in cards)
    assert [c["position"] for c in cards] == list(range(1, len(cards) + 1))
    first = cards[0]
    assert first["thumbnail"].startswith("https://encrypted-tbn") and first["image"]["width"] == 1872
    assert "nike.com" in first["link"] and first["price"] == {"amount": 160.0, "currency": "USD"}
    assert first["is_in_stock"] is True
    priced = [c for c in cards if c["price"]]
    assert len(priced) >= 15 and all(c["price"]["amount"] > 0 and c["price"]["currency"] for c in priced)
    assert any(c["is_in_stock"] is False for c in cards)
    rated = [c for c in cards if c["rating"]]
    assert rated and all(0 < c["rating"] <= 5 and c["review_count"] for c in rated)
    assert all(not c["link"].startswith("https://www.google.com/goto") for c in cards)


def test_visual_tab_and_locale_prices():
    assert len(P.visual_matches(load("nike_visual"), "US")) >= 40
    de = P.visual_matches(load("nike_de_all"), "DE")
    euros = [c for c in de if c["price"]]
    assert euros and all(c["price"]["currency"] in ("EUR", "USD", "GBP", "CHF") for c in euros)
    assert any(c["price"]["currency"] == "EUR" for c in euros)


def test_no_price_from_titles():
    for name in ("sunflower_all", "nike_all"):
        for card in P.visual_matches(load(name), "US"):
            if card["price"]:
                assert card["price"]["amount"] < 100000, card


def test_entities():
    sun = P.page_entities(load("sunflower_all"))
    assert [e["knowledge_graph_id"] for e in sun] == ["/m/0ftb8", "/m/0g0y_", "/m/01h3n"]
    assert sun[0]["name"] == "Common sunflower"
    assert sun[0]["search_link"].startswith("https://www.google.com/search?") and "kgmid=" in sun[0]["search_link"]
    nike = P.page_entities(load("nike_all"))
    assert nike and nike[0]["knowledge_graph_id"] == "/g/11lcs9v4k3"


def test_all_tab_web_results():
    parsed = P.all_tab(load("nike_all"), "US")
    assert len(parsed["web_results"]) >= 5
    web = parsed["web_results"][0]
    assert web["title"] and web["link"] and web["source"] and web["position"] == 1
    assert set(web) == {"position", "title", "link", "source", "domain", "snippet", "date", "sitelinks", "more_results"}


# ---- exact matches ----------------------------------------------------------------------------

def test_exact_matches():
    items = P.exact_matches(load("eiffel_exact"))
    assert len(items) >= 30
    assert all(i["title"] and i["source"] and i["link"].startswith("https://www.google.com/goto?") for i in items)
    dated = [i for i in items if i["date"]]
    assert dated and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", i["date"]) for i in dated)
    sized = [i for i in items if i["image"]]
    assert sized and all(isinstance(i["image"]["width"], int) and i["image"]["width"] > 0 for i in sized)
    assert items[0]["title"].startswith("Finding similarity percentage")


def test_exact_matches_empty():
    assert P.exact_matches(load("text_exact")) == []


# ---- qfmetadata / products ----------------------------------------------------------------------

def test_qf_matches_and_products():
    qm = P.qf_matches(load("nike_qf"))
    assert len(qm) >= 40
    priced = [m for m in qm if m["price"]]
    assert len(priced) >= 15
    assert all(m["price"]["currency"] and re.fullmatch(r"[A-Z]{3}", m["price"]["currency"]) for m in priced)
    nike = next(m for m in qm if "nike.com/u/custom-nike-dunk-high" in m["link"])
    assert nike["price"] == {"amount": 160.0, "currency": "USD"} and nike["source"] == "Nike"
    assert nike["store_country"] == "US" and nike["page_language"] == "en"
    assert nike["thumbnail"].startswith("https://encrypted-tbn")
    assert any(m["is_in_stock"] is False for m in qm) and any(m["rating"] and m["review_count"] for m in qm)
    cards = P.visual_matches(load("nike_visual"), "US")
    products = P.products(cards, qm, "US")
    assert len(products) >= len(priced)
    assert all(p["price"] for p in products)
    assert [p["position"] for p in products] == list(range(1, len(products) + 1))


def test_detected_objects():
    objects = P.detected_objects(load("nike_qf"))
    kinds = [o["type"] for o in objects]
    assert "salient_region" in kinds and "whole_image" in kinds
    assert all(_in_unit(o["bounding_box"]) for o in objects)
    assert P.detected_objects("not json") == [] and P.qf_matches(None) == []


# ---- AI overview ------------------------------------------------------------------------------

def test_stream_frames_cover_the_body():
    text = load("eiffel_ai")
    frames = stream.frames(text)
    assert len(frames) > 50
    kinds = {(t, n) for t, n, _ in frames}
    assert ("0", "") in kinds and ("b", "placeholder") in kinds and ("18", "write_to_target_document") in kinds
    assert "AI Mode replied" in stream.assemble(text)


def test_ai_overview_plain():
    ai = P.ai_overview(load("eiffel_ai"))
    assert ai["text"].startswith("The selected region appears to show the iconic Eiffel Tower")
    assert "Height: Stands at 330 meters tall." in ai["text"]
    assert not re.search(r"AI Mode|Shared|0 files|AI can make mistakes|Â", ai["text"])
    links = [s["link"] for s in ai["sources"]]
    assert "https://en.wikipedia.org/wiki/Eiffel_Tower" in links
    assert all(s["title"] and s["source"] for s in ai["sources"])
    danny = P.ai_overview(load("danny_ai"))
    assert "Danny DeVito" in danny["text"] and danny["sources"]


def test_ai_overview_question():
    ai = P.ai_overview(load("nike_q_ai"))
    text = ai["text"]
    assert text.startswith("The selected region features the Nike Dunk High By You")
    assert "Silhouette:" in text and "Customization Origin:" in text          # bulleted answer kept
    assert "\n$160\n" not in text and "In stock" not in text                   # offer grid dropped
    assert "AI Mode reply for" not in text
    assert len(ai["sources"]) >= 3


# ---- Lens API (OCR / translation) ----------------------------------------------------------------

def test_ocr_layout():
    objects = proto.message(load_bytes("crupload_ocr")).msg(2)
    out = P.text_layout(objects, OCR_SIZE, level="words")
    assert out["language"] == "en"
    assert out["text"].startswith("The simplest model of communication relies on three distinct parts")
    assert len(out["paragraphs"]) >= 4
    para = out["paragraphs"][0]
    assert _in_unit(para["bounding_box"]) and para["lines"] and para["lines"][0]["words"]
    word = para["lines"][0]["words"][0]
    assert word["text"] == "The" and _in_unit(word["bounding_box"]) and word["is_formula"] is False
    lines = P.text_layout(objects, OCR_SIZE, level="lines")["paragraphs"][0]
    assert "words" not in lines["lines"][0]
    assert "lines" not in P.text_layout(objects, OCR_SIZE, level="paragraphs")["paragraphs"][0]


def test_translation():
    objects = proto.message(load_bytes("crupload_translate_es")).msg(2)
    out = P.translation(objects, OCR_SIZE)
    assert out["status"] == "success" and out["source_language"] == "en" and out["target_language"] == "es"
    assert "comunicación" in out["translated_text"]
    assert out["paragraphs"] and out["paragraphs"][0]["original_text"].startswith("The simplest model")
    assert out["paragraphs"][0]["translated_text"].startswith("El modelo de comunicación")


# ---- helpers ------------------------------------------------------------------------------------

@pytest.mark.parametrize("seg,expected", [
    ("$160*", {"amount": 160.0, "currency": "USD"}),
    ("¥5,200*", {"amount": 5200.0, "currency": "JPY"}),
    ("1.234,56 €", {"amount": 1234.56, "currency": "EUR"}),
    ("£236*", {"amount": 236.0, "currency": "GBP"}),
    ("Keule2010 | Flickr", None),
    ("2010", None),
    ("Nike", None),
])
def test_price_segments(seg, expected):
    assert P._price(seg, "US") == expected


def test_iso_date():
    assert P.iso_date("Jun 23, 2020") == "2020-06-23"
    assert P.iso_date("23.06.2020") == "2020-06-23"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", P.iso_date("3 days ago"))
    assert P.iso_date("480x640") is None and P.iso_date(None) is None


def test_box():
    assert P.box(0.5, 0.5, 1, 1) == {"x": 0.0, "y": 0.0, "width": 1, "height": 1}
    assert P.box(None, 0.5, 1, 1) is None


def test_page_image():
    og = '<meta property="og:image" content="https://cdn.x.com/a.jpg">'
    assert _page_image(og, "https://x.com/p") == "https://cdn.x.com/a.jpg"
    empty_og_ld = ('<meta property="og:image" content=""/><script type="application/ld+json">'
                   '{"@type":"Product","image":["/img/p.webp"]}</script>')
    assert _page_image(empty_og_ld, "https://shop.x.com/p/1") == "https://shop.x.com/img/p.webp"
    assert _page_image("<html></html>", "https://x.com") is None


# ---- refs / schemas -----------------------------------------------------------------------------

def test_resolve_image():
    assert refs.resolve_image("https://cdn.x.com/a.jpg") == {"link": "https://cdn.x.com/a.jpg"}
    assert refs.resolve_image("cdn.x.com/a.jpg") == {"link": "https://cdn.x.com/a.jpg"}
    wrapped = "https://lens.google.com/uploadbyurl?url=https%3A%2F%2Fi.imgur.com%2FHBrB8p0.png"
    assert refs.resolve_image(wrapped) == {"link": "https://i.imgur.com/HBrB8p0.png"}
    imgres = "https://www.google.com/imgres?imgurl=https://cdn.x.com/b.png&imgrefurl=https://x.com"
    assert refs.resolve_image(imgres) == {"link": "https://cdn.x.com/b.png"}
    assert refs.resolve_image("data:image/png;base64,iVBORw0KGgo=") == {"data": "data:image/png;base64,iVBORw0KGgo="}
    assert refs.resolve_image("upload:" + "a" * 64) == {"upload": "a" * 64}
    for bad in ("", "not a link", "ftp://x.com/a.jpg", "data:text/plain;base64,aGk="):
        with pytest.raises(ValueError):
            refs.resolve_image(bad)


def test_resolve_region():
    assert refs.resolve_region("0.1,0.2,0.5,0.5") == {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.5, "unit": "fraction"}
    assert refs.resolve_region("10 20 300 200")["unit"] == "pixel"
    for bad in ("0.1,0.2,0.5", "a,b,c,d", "0.1,0.2,0,0.5", "1.5,0.2,0.3,0.3,1"):
        with pytest.raises(ValueError):
            refs.resolve_region(bad)


def test_schemas():
    data = schemas.SearchSchema().load({"image": "https://x.com/a.jpg"})
    assert data == {"image": {"link": "https://x.com/a.jpg"}, "region": None, "country": "US", "language": "en",
                    "include_ai_overview": False}
    assert schemas.TextSchema().load({"image": "https://x.com/a.jpg"})["level"] == "lines"
    assert schemas.TranslateSchema().load({"image": "https://x.com/a.jpg", "target_language": "ES"})["target_language"] == "es"
    from marshmallow import ValidationError
    with pytest.raises(ValidationError):
        schemas.TranslateSchema().load({"image": "https://x.com/a.jpg"})
    with pytest.raises(ValidationError):
        schemas.SearchSchema().load({"image": "https://x.com/a.jpg", "url": "https://x.com/a.jpg"})
