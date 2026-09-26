"""Failure taxonomy (route_glue maps these to HTTP):

  LensBadRequest       the caller's image / params are unusable (bad link,
                       not an image, too big, private host)       -> 400
                       A ValueError, so route_call reports its message as is.
  GoogleBadRequest     Google rejected well-formed params            -> 400
  GoogleNotFound                                                     -> 404
  GoogleBlocked        wall / captcha on every identity              -> 502, retry
  GoogleUpstreamError  transport failure / 5xx / unparseable body    -> 502, retry
"""
from google_search.fetch import GoogleBadRequest, GoogleBlocked, GoogleNotFound, GoogleUpstreamError  # noqa: F401


class LensBadRequest(ValueError):
    """The request's image or params cannot be used."""
