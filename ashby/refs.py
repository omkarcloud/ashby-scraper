"""Ashby reference parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrIdField convention — never a sibling `url`/`id` pair).

  board    ramp (the job-board slug: the last path segment of the hosted
           board, https://jobs.ashbyhq.com/ramp)
           | https://jobs.ashbyhq.com/ramp
           | https://jobs.ashbyhq.com/ramp/34413f8d-...  (any page of the board)
           | https://api.ashbyhq.com/posting-api/job-board/ramp
  boards   comma-separated slugs and/or links (max MAX_BOARDS)
  job      34413f8d-26bf-4bbc-8ade-eb309a0e2245 (the job posting id)
           | https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245
           | https://jobs.ashbyhq.com/ramp/34413f8d-.../application
           A bare id carries no board, and Ashby's job endpoints need both
           (the id is only looked up inside its board), so the schema asks
           for `board` next to a bare id.

Slugs are matched case-insensitively upstream ("RAMP" resolves to the
"ramp" board), so they are lower-cased here for stable cache keys.
"""
import re
from urllib.parse import urlparse

SITE = "https://jobs.ashbyhq.com"
POSTING_API = "https://api.ashbyhq.com/posting-api/job-board"
MAX_BOARDS = 20

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# board pages that are not a job board: the SPA's own routes
_RESERVED = {"api", "b", "meeting", "assets", "static"}


def _is_url(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//", "jobs.ashbyhq.com/", "api.ashbyhq.com/",
                           "www.ashbyhq.com/", "ashbyhq.com/"))


def _parse(value):
    """An ashbyhq.com link -> (host, [path segments]). Raises ValueError for
    another site."""
    url = value
    if url.startswith("//"):
        url = "https:" + url
    elif not url.lower().startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not (host == "ashbyhq.com" or host.endswith(".ashbyhq.com")):
        raise ValueError("not an ashbyhq.com link")
    return host, [s for s in parsed.path.split("/") if s]


def _slug_from_link(value, what):
    host, segments = _parse(value)
    if host.startswith("api."):
        lowered = [s.lower() for s in segments]
        if "job-board" in lowered and lowered.index("job-board") + 1 < len(segments):
            return segments[lowered.index("job-board") + 1]
        raise ValueError(f"{what} link must look like {POSTING_API}/ramp")
    if segments and segments[0].lower() not in _RESERVED:
        return segments[0]
    raise ValueError(f"{what} link must look like {SITE}/ramp")


# ---- links ------------------------------------------------------------------------

def board_link(slug):
    return f"{SITE}/{slug}" if slug else None


def job_link(slug, job_id):
    return f"{SITE}/{slug}/{job_id}" if slug and job_id else None


def apply_link(slug, job_id):
    return f"{SITE}/{slug}/{job_id}/application" if slug and job_id else None


def posting_api_link(slug):
    return f"{POSTING_API}/{slug}" if slug else None


# ---- resolvers ----------------------------------------------------------------------

def resolve_board(value):
    """Slug or an ashbyhq.com link -> the board slug (lower-cased)."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("board is required")
    if _is_url(value):
        try:
            value = _slug_from_link(value, "board")
        except ValueError as e:
            if "not an ashbyhq.com link" in str(e):
                raise ValueError("board must be an Ashby job-board slug (ramp) or a jobs.ashbyhq.com link")
            raise
    slug = value.split("?")[0].split("#")[0].strip().lower()
    if not _SLUG_RE.match(slug):
        raise ValueError("board must be an Ashby job-board slug (ramp, the last part of "
                         "https://jobs.ashbyhq.com/ramp) or a jobs.ashbyhq.com link")
    return slug


def resolve_boards(value):
    """Comma-separated slugs / links -> [slug, ...] (deduped, order kept, at
    most MAX_BOARDS)."""
    out = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        slug = resolve_board(raw)
        if slug not in out:
            out.append(slug)
    if not out:
        raise ValueError("boards is required")
    if len(out) > MAX_BOARDS:
        raise ValueError(f"at most {MAX_BOARDS} boards per call")
    return out


def resolve_job(value):
    """Job posting id or a jobs.ashbyhq.com job link -> {"id": uuid,
    "board": slug | None}."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("job is required")
    board = None
    if _is_url(value):
        try:
            host, segments = _parse(value)
        except ValueError:
            raise ValueError("job must be an Ashby job posting id or a jobs.ashbyhq.com job link")
        if host.startswith("api.") or len(segments) < 2 or segments[0].lower() in _RESERVED:
            raise ValueError(f"job link must look like {SITE}/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245")
        board = segments[0].lower()
        value = segments[1]
    job_id = value.split("?")[0].split("#")[0].strip().lower()
    if not _UUID_RE.match(job_id):
        raise ValueError("job must be an Ashby job posting id (34413f8d-26bf-4bbc-8ade-eb309a0e2245) "
                         "or a jobs.ashbyhq.com job link")
    return {"id": job_id, "board": board}


def company_slug_candidates(company, limit=6):
    """A company name, domain or website -> the board slugs worth trying
    ("Ramp Financial" -> ramp-financial, rampfinancial, ramp; "ramp.com" ->
    ramp). Order = most likely first."""
    value = str(company or "").strip()
    if not value:
        return []
    low = value.lower()
    if _is_url(low) or re.match(r"^(https?://|www\.)", low) or re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/|$)", low):
        url = low if low.startswith(("http://", "https://")) else "https://" + low
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        labels = [l for l in host.split(".") if l]
        # the registrable label: drop the TLD(s) — the longest non-TLD label wins ties
        if len(labels) >= 2:
            candidates = [l for l in labels[:-1] if l not in ("co", "com", "net", "org", "jobs", "careers")]
            low = max(candidates, key=len) if candidates else labels[0]
        else:
            low = host
    words = [w for w in re.split(r"[^a-z0-9]+", low) if w]
    if not words:
        return []
    out = []
    for cand in ("-".join(words), "".join(words), "_".join(words), words[0], ".".join(words)):
        if cand and _SLUG_RE.match(cand) and cand not in out:
            out.append(cand)
    return out[:limit]
