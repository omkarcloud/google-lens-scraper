"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. See README.md for every endpoint.
"""
import json
import os

from google_lens import refs
from google_lens.search import products, search
from google_lens.text import text

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # what the picture is, visual matches with prices, web results and videos
    save("search_golden_gate_bridge.json", search(refs.resolve_image(
        "https://upload.wikimedia.org/wikipedia/commons/8/8d/Golden_Gate_Bridge%2C_North_view.jpg")))

    # shop the picture: offers with price, store, stock and rating
    save("products_airpods_pro.json", products(refs.resolve_image(
        "https://upload.wikimedia.org/wikipedia/commons/2/2f/AirPods_Pro_%282nd_generation%29.jpg")))

    # the text in a picture, with positions (plain HTTP, no browser)
    save("text_textbook_page.json", text(refs.resolve_image(
        "https://s3-us-west-2.amazonaws.com/courses-images/wp-content/uploads/sites/1844/2017/06/"
        "15212914/images-textwrap-square.png")))
