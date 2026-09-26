"""The 7 Google Lens endpoints. Every path is served with and without the
`/google-lens` prefix, so code generated against the hosted API on RapidAPI
(paths like /search) runs unchanged against this server.

Every endpoint takes the picture two ways:
  GET   ?image=<picture link | web page link | data:image/...;base64,...>
  POST  the file as the multipart form field `image` (other params in the
        query string)

Params are validated by the marshmallow schemas in google_lens/schemas.py
(the same ones the hosted API uses); failures map to HTTP like this:
bad params -> 400, not found -> 404, Google blocked / upstream error -> 502,
anything else -> 500.
"""
import json

from bottle import request, response, route

import config
from google_lens import images, refs, schemas, search, text
from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

PREFIX = "/google-lens"

# (public path, schema, function) — in the order of the docs
ENDPOINTS = [
    ("/search", schemas.SearchSchema, search.search),
    ("/products", schemas.MatchesSchema, search.products),
    ("/exact-matches", schemas.MatchesSchema, search.exact_matches),
    ("/visual-matches", schemas.MatchesSchema, search.visual_matches),
    ("/ai-overview", schemas.AiOverviewSchema, search.ai_overview),
    ("/text", schemas.TextSchema, text.text),
    ("/translate", schemas.TranslateSchema, text.translate),
]


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Amélie" would arrive as "AmÃ©lie")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def handle(path, schema_cls, impl):
    label = f"google-lens {path.strip('/')}"
    data, error = load_query(schema_cls, query_dict())
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"google-lens rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"google-lens blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"{label} failed: {e}"}, 502)
    except Exception as e:
        return json_response({"error": f"{label} failed: {type(e).__name__}: {e}"}, 500)
    return json_response(result)


def handle_upload(path, schema_cls, impl):
    """POST: the picture as multipart `image` (or `file`); a form field
    `image` holding a link works too."""
    upload = request.files.get("image") or request.files.get("file")
    ref = None
    if upload is not None:
        raw = upload.file.read(config.GOOGLE_LENS_MAX_IMAGE_BYTES + 1)
        if not raw:
            return json_response({"error": "The uploaded image is empty"}, 400)
        if len(raw) > config.GOOGLE_LENS_MAX_IMAGE_BYTES:
            return json_response({"error": f"Image is larger than {config.GOOGLE_LENS_MAX_IMAGE_BYTES // (1024 * 1024)} MB"}, 400)
        ref = images.register_upload(raw)
        request.query.replace("image", refs.upload_param(ref))
    elif request.forms.getunicode("image"):
        request.query.replace("image", request.forms.getunicode("image"))
    else:
        return json_response({"error": "Missing image: send the picture as the multipart form field `image` "
                                       "(or use GET with ?image=<picture link>)"}, 400)
    try:
        return handle(path, schema_cls, impl)
    finally:
        if ref is not None:
            images.release_upload(ref)


def mount(path, schema_cls, impl):
    """Serve one endpoint at /path and /google-lens/path, GET and POST."""
    def get_handler():
        return handle(path, schema_cls, impl)

    def post_handler():
        return handle_upload(path, schema_cls, impl)

    name = "google_lens_" + path.strip("/").replace("/", "_").replace("-", "_")
    get_handler.__name__, post_handler.__name__ = name, name + "_upload"
    for p in (path, PREFIX + path):
        route(p, method="GET")(get_handler)
        route(p, method="POST")(post_handler)


for _path, _schema, _impl in ENDPOINTS:
    mount(_path, _schema, _impl)


@route("/")
@route("/health")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
