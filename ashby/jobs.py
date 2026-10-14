"""/ashby/jobs/* — every open role on a board with filters and paging, the
same across many boards, one job in full, and a job's application form.

A board is read whole (shared.board_bundle: the posting API document + the
GraphQL team tree, memoised) and filtered / sorted / paged in memory, so
paging through a large board costs no upstream requests."""
from ashby import parsers as P
from ashby import refs
from ashby import shared
from ashby.fetch import AshbyNotFound, job_posting, run_parallel, soft

FILTER_KEYS = ("query", "department", "team", "location", "country", "workplace_type", "employment_type",
               "remote", "posted_after", "min_salary", "currency")


def _filters(kwargs):
    return {k: kwargs.get(k) for k in FILTER_KEYS if kwargs.get(k) is not None}


def _listing(jobs, filters, sort, page, per_page, description):
    matched = shared.sort_jobs(shared.filter_jobs(jobs, filters), sort)
    rows = [P.with_description(j, description) for j in shared.page_of(matched, page, per_page)]
    return matched, rows


def listing(board, sort="newest", page=1, per_page=50, description="none", **filters):
    bundle = shared.board_bundle(board)
    active = _filters(filters)
    matched, rows = _listing(bundle["jobs"], active, sort, page, per_page, description)
    return {
        "board": bundle["summary"],
        "filters": active or None,
        "total_job_count": len(bundle["jobs"]),
        "pagination": P.pagination(page, per_page, len(matched)),
        "jobs": rows,
    }


def search(boards, sort="newest", page=1, per_page=50, description="none", **filters):
    """The same listing across up to 20 boards in one call. A board that
    does not exist is reported under `not_found` and never fails the rest."""
    bundles = run_parallel([soft(lambda s=s: shared.board_bundle(s), default=None) for s in boards])
    active = _filters(filters)
    found, missing, jobs = [], [], []
    for slug, bundle in zip(boards, bundles):
        if bundle is None:
            missing.append(slug)
            continue
        summary = dict(bundle["summary"])
        summary["job_count"] = len(bundle["jobs"])
        found.append(summary)
        jobs.extend(bundle["jobs"])
    if not found:
        raise AshbyNotFound(f"none of the boards exist: {', '.join(missing)}")
    matched, rows = _listing(jobs, active, sort, page, per_page, description)
    return {
        "boards": found,
        "not_found": missing,
        "filters": active or None,
        "total_job_count": len(jobs),
        "pagination": P.pagination(page, per_page, len(matched)),
        "jobs": rows,
    }


def _slug_of(job, board):
    slug = job.get("board") or board
    if not slug:
        raise ValueError("board is required when job is a bare posting id")
    return slug


def details(job, board=None):
    slug = _slug_of(job, board)
    gql, bundle = run_parallel([
        lambda: job_posting(slug, job["id"]),
        soft(lambda: shared.board_bundle(slug), default=None),
    ])
    row = bundle["rows"].get(job["id"]) if bundle else None
    brief = bundle["briefs"].get(job["id"]) if bundle else None
    path = P.team_path(bundle["teams_index"], P.text(brief.get("teamId"))) if (bundle and brief) else None
    summary = bundle["summary"] if bundle else P.board_summary(None, slug)
    return {"job": P.job_details(gql, row, path, summary)}


def application_form(job, board=None):
    """The questions an applicant must answer: sections of typed fields
    with required flags and options (plus any post-application survey)."""
    slug = _slug_of(job, board)
    gql = job_posting(slug, job["id"])
    form = P.application_form(gql.get("applicationForm"), gql.get("surveyForms"))
    if form is None:
        raise AshbyNotFound(f"job '{job['id']}' has no application form")
    return {
        "job": {
            "id": P.text(gql.get("id")),
            "title": P.text(gql.get("title")),
            "link": refs.job_link(slug, job["id"]),
            "apply_link": refs.apply_link(slug, job["id"]),
            "board": {"slug": slug},
        },
        "form": form,
    }
