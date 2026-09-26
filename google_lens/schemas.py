"""Marshmallow request schemas for every /google-lens/* route.

Generic fields come from schema_fields.py (and the Google country /
language fields from google_search.schemas); each schema's load() output is
the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url` / `file` / `base64` trio):
  image   a picture link | a web page link (its og:image is used) | a
          Google Lens / Google Images link wrapping a picture | a data: URI.
          The POST variant of every route takes the file itself as the
          multipart field `image` instead (see routes.py).
  region  x,y,width,height of the part of the picture to search — fractions
          0-1 or pixels.
"""
import marshmallow
from marshmallow import validate

from google_lens import refs
from google_search.schemas import CountryField, LanguageField
from schema_fields import BaseSchema, ChoiceField, Flag, LanguageCodeField, QueryField, RefField

TEXT_LEVELS = ("paragraphs", "lines", "words")


class ImageField(RefField):
    """The picture: a direct image link, a web page link (its og:image is
    used), a Google Lens / Google Images link, or a data:image/...;base64 URI."""
    resolver = staticmethod(refs.resolve_image)

    def __init__(self, **kwargs):
        kwargs.setdefault("validate", validate.Length(max=12_000_000))
        super().__init__(**kwargs)


class RegionField(RefField):
    """Part of the picture to search: x,y,width,height as fractions 0-1
    (top-left origin) or in pixels."""
    resolver = staticmethod(refs.resolve_region)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


class _Image(BaseSchema):
    image = ImageField()
    region = RegionField()


class _Localized(_Image):
    country = CountryField()
    language = LanguageField()


class SearchSchema(_Localized):
    include_ai_overview = Flag(load_default=False)


class MatchesSchema(_Localized):
    """visual-matches / exact-matches / products."""


class QuestionField(QueryField):
    """A question about the picture (Lens multisearch), up to 500 characters."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(max_length=500, **kwargs)


class AiOverviewSchema(_Localized):
    question = QuestionField()


class TextSchema(_Image):
    language = LanguageCodeField(required=False, load_default=None)
    level = ChoiceField(TEXT_LEVELS, load_default="lines")


class TranslateSchema(_Image):
    target_language = LanguageCodeField(required=True, load_default=marshmallow.missing)
    source_language = LanguageCodeField(required=False, load_default=None)
