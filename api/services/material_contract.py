"""Shared limits and public source links for platform material snapshots."""

from urllib.parse import urlsplit, urlunsplit

MAX_SECTION_CHARS = 256000

def public_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
