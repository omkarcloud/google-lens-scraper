"""/google-lens/text and /google-lens/translate: the Chromium Lens API
(google_lens/lens_api.py) — plain curl_cffi, no identity, ~1 s."""
from google_lens import images, lens_api, parsers


def text(image, region=None, language=None, level="lines"):
    """OCR: the full text, its language, and paragraphs -> lines [-> words]
    with bounding boxes (fractions of the picture, top-left origin)."""
    picture = images.load(image)
    jpeg, width, height = images.for_ocr(picture, region)
    objects = lens_api.call(jpeg, width, height, language=language or "")
    result = parsers.text_layout(objects, (width, height), level)
    return {
        "text": result["text"],
        "language": result["language"],
        "paragraphs": result["paragraphs"],
        "image": picture.info(),
    }


def translate(image, target_language, source_language=None, region=None):
    """The picture's text translated paragraph by paragraph (Lens's
    Translate filter), next to the original text and its position."""
    picture = images.load(image)
    jpeg, width, height = images.for_ocr(picture, region)
    objects = lens_api.call(jpeg, width, height, target_language=target_language, source_language=source_language)
    result = parsers.translation(objects, (width, height))
    return {
        "source_language": result["source_language"],
        "target_language": result["target_language"] or target_language,
        "status": result["status"],
        "translated_text": result["translated_text"],
        "original_text": result["original_text"],
        "paragraphs": result["paragraphs"],
        "image": picture.info(),
    }
