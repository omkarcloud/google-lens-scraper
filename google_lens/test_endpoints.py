"""Live endpoint smoke tests: one call per /google-lens/* route against a
running service, with example values proven to return data (2026-09-26).
The listing tooling reads each route's FIRST call from this file's AST as
its working example, so those calls use literal values (not the constants).

Skipped unless GOOGLE_LENS_BASE points at a running service:

    SCRAPERS_PORT=6071 ONLY_SCRAPER=google-lens python run.py
    GOOGLE_LENS_BASE=http://127.0.0.1:6071 python -m pytest google_lens/test_endpoints.py -q

Results pages run on minted Google identities (headed Camoufox, 20-240 s
for the first one), so the first identity-backed call is slow; OCR and
translation are ~1-3 s.
"""
import os

import pytest

BASE = os.environ.get("GOOGLE_LENS_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set GOOGLE_LENS_BASE to run live endpoint tests")

EIFFEL = "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg"
SNEAKER_PAGE = "https://www.dtlr.com/products/nike-dunk-high-dd1399-102"
TEXTBOOK = ("https://s3-us-west-2.amazonaws.com/courses-images/wp-content/uploads/sites/1844/2017/06/"
            "15212914/images-textwrap-square.png")


def call(path, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=600)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def upload(path, content, filename="image.jpg", **params):
    from curl_cffi import requests, CurlMime
    mime = CurlMime()
    mime.addpart(name="image", filename=filename, content_type="image/jpeg", data=content)
    resp = requests.post(BASE + path, params=params, multipart=mime, timeout=600)
    mime.close()
    return resp


def status(path, **params):
    from curl_cffi import requests
    return requests.get(BASE + path, params=params, timeout=600).status_code


def _picture(link):
    from curl_cffi import requests
    return requests.get(link, timeout=60, headers={"user-agent": "Mozilla/5.0 (compatible; test)"}).content


def test_search():
    body = call("/google-lens/search", image="https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg")
    assert body["entities"][0]["name"] == "Eiffel Tower" and body["entities"][0]["knowledge_graph_id"] == "/m/02j81"
    assert len(body["visual_matches"]) >= 30
    match = body["visual_matches"][0]
    assert match["link"].startswith("http") and match["title"] and match["image"]["link"] and match["thumbnail"]
    assert body["web_results"] and not any("/goto?" in w["link"] for w in body["web_results"])
    assert body["detected_objects"] and body["image"]["width"] == 2900
    body = call("/google-lens/search", image=SNEAKER_PAGE, include_ai_overview="true", country="GB")
    assert body["ai_overview"] and body["ai_overview"]["text"]
    assert any(m["price"] and m["price"]["currency"] == "GBP" for m in body["visual_matches"])


def test_visual_matches():
    body = call("/google-lens/visual-matches", image="https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg")
    assert len(body["visual_matches"]) >= 60
    assert len({m["link"] for m in body["visual_matches"]}) == len(body["visual_matches"])


def test_exact_matches():
    body = call("/google-lens/exact-matches", image="https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg")
    items = body["exact_matches"]
    assert len(items) >= 30 and not any("/goto?" in i["link"] for i in items)
    assert all(i["domain"] for i in items) and any(i["date"] for i in items)


def test_products():
    body = call("/google-lens/products", image="https://www.dtlr.com/products/nike-dunk-high-dd1399-102")
    products = body["products"]
    assert len(products) >= 10
    assert all(p["price"]["amount"] > 0 and p["price"]["currency"] for p in products)
    assert any(p["is_in_stock"] is not None for p in products)


def test_ai_overview():
    body = call("/google-lens/ai-overview", image="https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg")
    assert body["is_available"] and "Eiffel" in body["text"] and body["sources"]
    assert not any("/goto?" in s["link"] for s in body["sources"])
    # some questions get an empty answer from Google (is_available false); this one answers
    body = call("/google-lens/ai-overview", image=SNEAKER_PAGE, question="what is the retail price of these")
    assert body["question"] == "what is the retail price of these" and body["is_available"] and body["text"]


def test_text():
    body = call("/google-lens/text", image="https://s3-us-west-2.amazonaws.com/courses-images/wp-content/uploads/sites/1844/2017/06/15212914/images-textwrap-square.png")
    assert body["language"] == "en" and body["text"].startswith("The simplest model of communication")
    assert body["paragraphs"][0]["lines"][0]["bounding_box"]
    words = call("/google-lens/text", image=TEXTBOOK, level="words", region="0,0,1,0.3")
    assert words["paragraphs"][0]["lines"][0]["words"][0]["text"]


def test_translate():
    body = call("/google-lens/translate", image="https://s3-us-west-2.amazonaws.com/courses-images/wp-content/uploads/sites/1844/2017/06/15212914/images-textwrap-square.png",
                target_language="es")
    assert body["status"] == "success" and body["source_language"] == "en" and body["target_language"] == "es"
    assert "comunicación" in body["translated_text"] and body["paragraphs"][0]["original_text"]


def test_post_upload():
    picture = _picture(TEXTBOOK)          # upload.wikimedia.org rate-limits repeated test downloads
    resp = upload("/google-lens/text", picture, filename="page.png")
    body = resp.json()
    assert resp.status_code == 200 and body["image"]["link"] is None
    assert body["text"].startswith("The simplest model of communication")
    resp = upload("/google-lens/search", picture, filename="page.png", country="US")
    assert resp.status_code == 200 and resp.json()["entities"] and resp.json()["visual_matches"]


def test_bad_requests():
    assert status("/google-lens/search") == 400
    assert status("/google-lens/search", image="http://127.0.0.1/x.jpg") == 400
    assert status("/google-lens/search", image="https://example.com/nope.jpg") == 400
    assert status("/google-lens/text", image=TEXTBOOK, region="0.9,0.9,0.001,0.001") == 400
    assert status("/google-lens/translate", image=TEXTBOOK) == 400
    assert upload("/google-lens/text", b"not an image").status_code == 400
