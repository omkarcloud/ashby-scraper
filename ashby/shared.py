"""Lookups shared by the /ashby/* endpoint modules: one board's bundle (the
organization + the team tree from GraphQL joined with the posting API's
rows), memoised in-process for BOARD_TTL seconds so paging and filtering a
board costs no upstream request, plus the in-memory filter / sort the list
and search endpoints apply.

The memo keeps parsed jobs WITH their description fields (an OpenAI-sized
board is ~15 MB); MAX_BOARDS bounds the memory, oldest board out first.
"""
import threading
import time
from datetime import date

from ashby import parsers as P
from ashby.fetch import AshbyNotFound, board_with_teams, organization, posting_board, run_parallel, soft

BOARD_TTL = 600
MAX_BOARDS = 32

_lock = threading.Lock()
_memo = {}


def memo(key, ttl, build):
    now = time.time()
    with _lock:
        hit = _memo.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = build()
    with _lock:
        _memo[key] = (now + ttl, value)
        while len(_memo) > MAX_BOARDS:
            oldest = min(_memo, key=lambda k: _memo[k][0])
            del _memo[oldest]
    return value


def clear():
    with _lock:
        _memo.clear()


# ---- one board ------------------------------------------------------------------

def _build_bundle(slug):
    doc, board, org = run_parallel([
        lambda: posting_board(slug),
        soft(lambda: board_with_teams(slug), default=None),
        soft(lambda: organization(slug), default=None),
    ])
    board = board or {}
    index = P.teams_index(board.get("teams"))
    briefs = {}
    for brief in P.as_list(board.get("jobPostings")):
        if isinstance(brief, dict) and P.text(brief.get("id")):
            briefs[P.text(brief.get("id"))] = brief
    summary = P.board_summary(org, slug)
    jobs, team_counts = [], {}
    for row in P.as_list(doc.get("jobs")):
        if not isinstance(row, dict):
            continue
        brief = briefs.get(P.text(row.get("id")))
        team_id = P.text(brief.get("teamId")) if brief else None
        jobs.append(P.job(row, brief, P.team_path(index, team_id), summary))
        if team_id:
            team_counts[team_id] = team_counts.get(team_id, 0) + 1
    jobs.sort(key=lambda j: (j.get("published_at") or ""), reverse=True)
    return {
        "slug": slug,
        "organization": org,
        "summary": summary,
        "teams_index": index,
        "team_counts": team_counts,
        "briefs": briefs,
        "rows": {P.text(r.get("id")): r for r in P.as_list(doc.get("jobs")) if isinstance(r, dict)},
        "jobs": jobs,
    }


def board_bundle(slug):
    """{"organization", "summary", "teams_index", "team_counts", "briefs",
    "rows", "jobs"} for one board. AshbyNotFound for an unknown slug."""
    return memo(f"board:{slug}", BOARD_TTL, lambda: _build_bundle(slug))


# ---- filtering / sorting ----------------------------------------------------------

def _has(haystack, needle):
    return bool(haystack) and needle in haystack.lower()


def _location_blobs(job):
    locs = [job.get("location")] + list(job.get("secondary_locations") or [])
    for loc in locs:
        if loc:
            yield loc


def _matches(job, f):
    if f.get("query"):
        q = f["query"].lower()
        blob = " ".join(filter(None, [job.get("title"), job.get("department"), " ".join(job.get("team_path") or []),
                                      " ".join(l.get("name") or "" for l in _location_blobs(job))]))
        if q not in blob.lower():
            return False
    if f.get("department") and not _has(job.get("department"), f["department"].lower()):
        return False
    if f.get("team"):
        t = f["team"].lower()
        if not any(_has(name, t) for name in job.get("team_path") or []):
            return False
    if f.get("location"):
        l = f["location"].lower()
        if not any(_has(" ".join(filter(None, [x.get("name"), x.get("city"), x.get("region"), x.get("country")])), l)
                   for x in _location_blobs(job)):
            return False
    if f.get("country"):
        c = f["country"].lower()
        if not any(_has(x.get("country"), c) or _has(x.get("name"), c) for x in _location_blobs(job)):
            return False
    if f.get("workplace_type") and job.get("workplace_type") != f["workplace_type"]:
        return False
    if f.get("employment_type") and job.get("employment_type") != f["employment_type"]:
        return False
    if f.get("remote") is not None and bool(job.get("is_remote")) != f["remote"]:
        return False
    if f.get("posted_after"):
        published = (job.get("published_at") or "")[:10]
        if not published or published < f["posted_after"]:
            return False
    comp = job.get("compensation") or {}
    salary = comp.get("salary") if comp else None
    if f.get("currency"):
        if not salary or salary.get("currency") != f["currency"]:
            return False
    if f.get("min_salary") is not None:
        top = (salary or {}).get("max") or (salary or {}).get("min")
        if top is None or top < f["min_salary"]:
            return False
    return True


def filter_jobs(jobs, filters):
    return [j for j in jobs if _matches(j, filters)]


def sort_jobs(jobs, sort):
    if sort == "oldest":
        return sorted(jobs, key=lambda j: (j.get("published_at") is None, j.get("published_at") or ""))
    if sort == "title":
        return sorted(jobs, key=lambda j: (j.get("title") or "").lower())
    return sorted(jobs, key=lambda j: (j.get("published_at") or ""), reverse=True)


def page_of(jobs, page, per_page):
    start = (page - 1) * per_page
    return jobs[start:start + per_page]


def today():
    return date.today().isoformat()
