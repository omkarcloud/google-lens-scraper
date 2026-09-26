"""Cache TTL per /google-lens/* endpoint (cache.py, keyed on the validated
params — the image link, data URI or upload hash, region, country,
language — so a re-sent picture link or re-uploaded file reuses the row).

Results pages cost an identity flow (a minted identity serves ~20-40
page loads) and Lens answers for a picture barely move within a day;
offers move within hours. OCR / translation are deterministic."""
from datetime import timedelta

SEARCH_CACHE = timedelta(hours=12)
VISUAL_MATCHES_CACHE = timedelta(hours=12)
EXACT_MATCHES_CACHE = timedelta(hours=24)
PRODUCTS_CACHE = timedelta(hours=3)
AI_OVERVIEW_CACHE = timedelta(hours=12)
TEXT_CACHE = timedelta(days=7)
TRANSLATE_CACHE = timedelta(days=7)
