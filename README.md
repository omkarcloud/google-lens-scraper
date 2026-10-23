# 🔍 Google Lens Scraper

Google Lens Scraper is a **free and open-source** scraper that gets you **unlimited** Google Lens data for free.

## ✨ What Can I Get?

- 🔍 **Identify anything in a photo** — Knowledge Graph entity, 50+ visual matches, web results & videos in one call
- 🛍️ **Shop any photo** — 40+ offers with price, store, stock status & ratings
- 🖼️ **Reverse image search** — up to 400 pages using your exact picture, with dates & image sizes
- 📝 **Read & translate text in images** — OCR with word positions, translation from 100+ languages

## 🎥 Example: A Full Google Lens Search

```json
{
  "entities": [
    { "name": "Golden Gate Bridge", "knowledge_graph_id": "/m/035p3", "search_link": "https://www.google.com/search?hl=en&gl=US&q=Golden+Gate+Bridge&kgmid=%2Fm%2F035p3" }
  ],
  "visual_matches": [
    {
      "position": 7,
      "title": "Golden Gate Bridge, San Francisco - Book Tickets & Tours ...",
      "link": "https://www.getyourguide.com/golden-gate-bridge-l3624/",
      "source": "GetYourGuide",
      "rating": 4.6,
      "review_count": 41633,
      "image": { "link": "https://cdn.getyourguide.com/image/format=auto,fit=crop,gravity=auto,quality=60,width=400,height=265,dpr=2/tour_img/f3705c469448e6bcce5520fbcc1c31e8de323874dbc4e1e07aacc60a6a12cd0a.jpeg", "width": 530, "height": 800 }
    },
    {
      "position": 15,
      "title": "Marin Headlands Scenic Vistas - Golden Gate National ...",
      "link": "https://www.nps.gov/goga/planyourvisit/marin-headlands-scenic-vistas.htm?ref=trip101com",
      "source": "National Park Service",
      "image": { "link": "https://www.nps.gov/common/uploads/grid_builder/goga/crop1_1/C4984E35-9949-F226-AFA943A651030DA8.jpg?width=640&quality=80&mode=crop&format=webp", "width": 640, "height": 640 }
    }
  ],
  "web_results": [
    { "position": 7, "title": "Fog, Steel, Salt, Rust, and Paint - Exhibits Area 1", "link": "https://www.goldengate.org/exhibits/fog-steel-salt-rust-and-paint/", "source": "Golden Gate Bridge, Highway and Transportation District" }
  ],
  "detected_objects": [
    { "type": "salient_region", "bounding_box": { "x": 0.0, "y": 0.11713, "width": 0.92578, "height": 0.86969 } }
  ]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Google Lens Data — Get It in 60 Seconds

1️⃣ Clone and install (the last command downloads Camoufox, the hardened Firefox that opens Google for you):
```bash
git clone https://github.com/omkarcloud/google-lens-scraper
cd google-lens-scraper
python -m pip install -r requirements.txt
python -m camoufox fetch
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/search?image=https://upload.wikimedia.org/wikipedia/commons/8/8d/Golden_Gate_Bridge%2C_North_view.jpg"
```

```json
{
  "entities": [
    { "name": "Golden Gate Bridge", "knowledge_graph_id": "/m/035p3", "search_link": "https://www.google.com/search?hl=en&gl=US&q=Golden+Gate+Bridge&kgmid=%2Fm%2F035p3" },
    { "name": "Golden Gate", "knowledge_graph_id": "/m/011wgf", "search_link": "https://www.google.com/search?hl=en&gl=US&q=Golden+Gate&kgmid=%2Fm%2F011wgf" }
  ],
  "visual_matches": [
    {
      "position": 9,
      "title": "Golden Gate Bridge, San Francisco - Book Tickets & Tours ...",
      "link": "https://www.getyourguide.com/golden-gate-bridge-l3624/",
      "source": "GetYourGuide",
      "domain": "www.getyourguide.com",
      "rating": 4.6,
      "review_count": 41633,
      "thumbnail": "https://encrypted-tbn3.gstatic.com/images?q=tbn:ANd9GcS4dGiKo1hGtRHZhTRzLdcMJ_PhnfaFRec6VZWfYiWv9zHrChky"
    }
  ],
  "image": { "link": "https://upload.wikimedia.org/wikipedia/commons/8/8d/Golden_Gate_Bridge%2C_North_view.jpg", "width": 5272, "height": 3515, "format": "jpeg", "size_bytes": 8128165 }
}
```

All 7 endpoints are now live at `http://localhost:8000`.

The first search can take a minute while a browser opens Google and clears its checks; after that, each call takes a few seconds. Image to Text and Translate need no browser and answer in a second or two.

Got the picture on disk instead of a link? POST it — every endpoint takes the file as the form field `image`:

```bash
curl -F "image=@photo.jpg" "http://localhost:8000/products"
```

### 🔓 Google Showing a Captcha?

Google often answers a fresh browser with an "unusual traffic" captcha. The scraper first tries on its own — no key, no cost — and when Google insists, it tells you so. Then:

1. Get a [CapSolver](https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm) API key (one captcha is solved per ~30 calls).
2. Start the API with it:
   ```bash
   CAPSOLVER_API_KEY=CAP-... python run.py
   ```

For heavy use, add a sticky residential proxy too — Google pauses one IP after a few dozen Lens searches:

```bash
GOOGLE_LENS_PROXY="http://user-session-{session}:pass@host:port" CAPSOLVER_API_KEY=CAP-... python run.py
```

`{session}` gets a fresh id for every browser session. See [config.py](config.py) for every option.

Image to Text and Translate need none of this — they are plain HTTP and work straight away.

## 📚 Endpoints

7 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Search by Image | `/search` | What it is, visual matches, web results, videos & objects |
| Products | `/products` | Offers with price, store, stock status and ratings |
| Exact Matches | `/exact-matches` | Every page using your exact picture, with dates |
| Visual Matches | `/visual-matches` | 100+ similar images with page links and prices |
| AI Overview | `/ai-overview` | Google's AI answer about the picture, with sources |
| Image to Text (OCR) | `/text` | Full text with paragraphs, lines and word positions |
| Translate Image | `/translate` | The picture's text translated, paragraph by paragraph |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-google-lens-scraper-free-200-calls/pricing) — 200 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-google-lens-scraper-free-200-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-google-lens-scraper-free-200-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/products",
    params={"image": "https://upload.wikimedia.org/wikipedia/commons/2/2f/AirPods_Pro_%282nd_generation%29.jpg"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Google Lens Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Google%20Lens%20Scraper%20API.)

[![Ask Us by Email about Google Lens Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Google%20Lens%20Scraper%20API&body=I%20need%20help%20using%20the%20Google%20Lens%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/google-lens-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/google-lens-scraper)
