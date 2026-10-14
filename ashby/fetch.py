"""Ashby transport: plain curl_cffi with browser-impersonated TLS. No
browser, no login, no cookies, no token (validated 2026-10-14 from direct
Indian egress: ~60 calls across every surface, zero challenges, no WAF in
front of either host).

Ashby hosts every customer's careers board at jobs.ashbyhq.com/<slug>. The
data behind a board comes from two anonymous surfaces:

  * POSTING  https://api.ashbyhq.com/posting-api/job-board/<slug>?includeCompensation=true
    Ashby's documented public Job Postings API: every published job with
    its HTML + plain-text description, department, team, locations with
    postal addresses, publishedAt, employment / workplace type, apply and
    job links and the structured compensation block (salary, equity, bonus
    and commission components with min / max / currency / interval, per
    tier). One document per board (Ramp 162 jobs = 3 MB, OpenAI 807 jobs =
    14 MB, ~0.6 s either way, gzip on the wire). 404 for an unknown slug.

  * GRAPHQL  https://jobs.ashbyhq.com/api/non-user-graphql?op=<operation>
    The board SPA's own API. Introspection is disabled, but the operations
    the SPA sends (sniffed 2026-10-14 from the page's requests) answer as
    is: ApiOrganizationFromHostedJobsPageName (organization: name, website,
    logos, theme, privacy policy, timezone), ApiJobBoardWithTeams (the team
    tree + one brief per job with its teamId — the posting API has no team
    hierarchy) and ApiJobPosting (one job: public department / team names,
    confidentiality, application deadline, compensation philosophy, texting
    consent and the full application form — every section and field with
    its type, required flag and options). Unknown slug / id = `null` data.

The hosted board HTML (window.__appData) carries the same organization +
board data as the two GraphQL reads and is not used.

Blocks: none seen. If the pod's egress ever starts answering 403/429 (or
HTML where JSON is expected), a worker moves its session to a sticky
residential exit (config.ashby_fallback_proxy(), ASHBY_FALLBACK_PROXY_COUNTRY)
for config.ASHBY_BLOCK_COOLDOWN seconds, the same shape as the woolworths
transport.

FALLBACK HOOK: if direct AND the residential exit both keep failing with
AshbyBlocked, escalate to a browser the way aldi does — one headful
patchright load of the same posting-API / GraphQL URL on a fresh exit
(patchright_driver.FetchResponse). Not built now: dead code while plain
HTTP works cookie-free.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  AshbyUpstreamError  transport failure / 5xx / GraphQL error  — retryable
  AshbyBlocked        403 / 429 / HTML where JSON expected     — retryable on a new exit
  AshbyBadRequest     upstream 400                             — never retried
  AshbyNotFound       unknown board / job                      — never retried
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from ashby import refs

GRAPHQL = refs.SITE + "/api/non-user-graphql"
IMPERSONATE = "chrome"

TIMEOUT = 45            # GraphQL reads answer in < 1 s
POSTING_TIMEOUT = 90    # a 14 MB board document on a slow link
FANOUT_WORKERS = 8

API_HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
}
GRAPHQL_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": refs.SITE,
    "referer": refs.SITE + "/",
}

# ---- the SPA's GraphQL operations (sniffed 2026-10-14; __typename dropped) ----

ORGANIZATION_QUERY = """query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!) {
  organization: organizationFromHostedJobsPageName(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
    organizationId name publicWebsite customJobsPageUrl hostedJobsPageSlug allowJobPostIndexing isDemoOrg
    theme {
      colors showJobFilters showLocationAddress showTeams showAutofillApplicationsBox
      logoWordmarkImageUrl logoSquareImageUrl applicationSubmittedSuccessMessage
      jobBoardTopDescriptionHtml jobBoardBottomDescriptionHtml jobPostingBackUrl
    }
    recruitingPrivacyPolicyUrl timezone showCookieBanner
  }
}"""

BOARD_QUERY = """query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
    teams { id name externalName parentTeamId }
    jobPostings {
      id title teamId locationId locationName workplaceType employmentType compensationTierSummary
      secondaryLocations { locationId locationName }
    }
  }
}"""

JOB_POSTING_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title publishedDate departmentName departmentExternalName locationName locationAddress
    workplaceType employmentType descriptionHtml isListed isConfidential teamNames secondaryLocationNames
    compensationTierSummary compensationTiers { id title tierSummary } applicationDeadline
    compensationTierGuideUrl scrapeableCompensationSalarySummary compensationPhilosophyHtml
    applicationLimitCalloutHtml shouldAskForTextingConsent shouldAskForWhatsAppConsent
    candidateTextingPrivacyPolicyUrl candidateTextingTermsAndConditionsUrl legalEntityNameForTextingConsent
    automatedProcessingLegalNotice { automatedProcessingLegalNoticeRuleId automatedProcessingLegalNoticeHtml }
    applicationForm { ...FormRenderParts }
    surveyForms { ...FormRenderParts }
  }
}
fragment FormRenderParts on FormRender {
  id
  sections {
    title descriptionHtml isHidden
    fieldEntries { id field isRequired descriptionHtml isHidden }
  }
}"""


class AshbyUpstreamError(UpstreamError):
    """Transport failure, 5xx or a GraphQL-level error — retryable."""


class AshbyBlocked(AshbyUpstreamError, Blocked):
    """403 / 429 or an HTML body where JSON was expected — retryable on a new exit."""


class AshbyBadRequest(BadRequest):
    """Upstream 400 — the params were rejected. Never retried."""


class AshbyNotFound(NotFound):
    """Board / job does not exist — never retried."""


# ---- sessions ------------------------------------------------------------------
# One curl session per worker thread. Direct egress by default; after a block
# the thread's sessions go through a sticky residential exit for a cooldown.
_local = threading.local()
_fallback_until = 0.0
_fallback_lock = threading.Lock()


def _proxy_for_new_session():
    forced = config.ashby_proxy()
    if forced:
        return forced
    if time.time() < _fallback_until:
        return config.ashby_fallback_proxy()
    return None


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = _proxy_for_new_session()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        _local.session = sess
    return sess


def _drop_session():
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _note_block():
    """A 403/429 on direct egress: send new sessions through a residential
    exit for a while (no-op when no fallback country is configured)."""
    global _fallback_until
    if config.ashby_fallback_proxy() is not None:
        with _fallback_lock:
            _fallback_until = time.time() + config.ASHBY_BLOCK_COOLDOWN


def dump_debug(name, text):
    dbg = os.environ.get("ASHBY_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass


# ---- requests --------------------------------------------------------------------

def _classify(resp, label):
    status = resp.status_code
    if status == 404:
        raise AshbyNotFound(f"{label} not found")
    if status in (403, 429):
        dump_debug("blocked", resp.text)
        _note_block()
        raise AshbyBlocked(f"HTTP {status} on {label}")
    if status == 400:
        raise AshbyBadRequest(f"upstream rejected {label}")
    if status >= 500 or status != 200:
        raise AshbyUpstreamError(f"HTTP {status} on {label}")


def _json_of(resp, label):
    body = resp.text or ""
    if not body.lstrip().startswith(("{", "[")):
        # the SPA shell (an unknown /api path) or a WAF interstitial: not JSON
        dump_debug("nonjson", body)
        raise AshbyBlocked(f"{label} returned HTML, not JSON")
    try:
        return resp.json()
    except Exception:
        dump_debug("badjson", body)
        raise AshbyUpstreamError(f"could not parse JSON from {label}")


def _retrying(fn):
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except AshbyUpstreamError as e:      # includes AshbyBlocked
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def posting_board(slug):
    """The public Job Postings API document for one board: {"apiVersion",
    "jobs": [...]} with compensation included. AshbyNotFound for an unknown
    slug."""
    url = refs.posting_api_link(slug)
    label = f"board '{slug}'"

    def once():
        try:
            resp = _session().get(url, params={"includeCompensation": "true"}, headers=API_HEADERS,
                                  timeout=POSTING_TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise AshbyUpstreamError(f"request failed: {type(e).__name__}: {e}")
        _classify(resp, label)
        body = _json_of(resp, label)
        if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
            dump_debug("unexpected_posting", resp.text)
            raise AshbyBlocked(f"unexpected posting-api payload for {label}")
        return body

    return _retrying(once)


def graphql(operation, query, variables):
    """POST one of the SPA's operations -> the `data` member. A GraphQL-level
    error is an upstream failure (the queries here are constants)."""
    label = f"graphql {operation}"

    def once():
        try:
            resp = _session().post(f"{GRAPHQL}?op={operation}",
                                   json={"operationName": operation, "variables": variables, "query": query},
                                   headers=GRAPHQL_HEADERS, timeout=TIMEOUT)
        except Exception as e:
            raise AshbyUpstreamError(f"request failed: {type(e).__name__}: {e}")
        _classify(resp, label)
        body = _json_of(resp, label)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            errors = body.get("errors") if isinstance(body, dict) else None
            message = None
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = errors[0].get("message")
            raise AshbyUpstreamError(f"{label}: {message or 'no data in the response'}")
        return data

    return _retrying(once)


def organization(slug):
    """The organization behind a board (GraphQL). AshbyNotFound for an unknown slug."""
    data = graphql("ApiOrganizationFromHostedJobsPageName", ORGANIZATION_QUERY,
                   {"organizationHostedJobsPageName": slug})
    org = data.get("organization")
    if not isinstance(org, dict):
        raise AshbyNotFound(f"board '{slug}' not found")
    return org


def board_with_teams(slug):
    """{"teams": [...], "jobPostings": [...]} (GraphQL). AshbyNotFound for an unknown slug."""
    data = graphql("ApiJobBoardWithTeams", BOARD_QUERY, {"organizationHostedJobsPageName": slug})
    board = data.get("jobBoard")
    if not isinstance(board, dict):
        raise AshbyNotFound(f"board '{slug}' not found")
    return board


def job_posting(slug, job_id):
    """One job posting with its application form (GraphQL). AshbyNotFound
    when the id is not on that board."""
    data = graphql("ApiJobPosting", JOB_POSTING_QUERY,
                   {"organizationHostedJobsPageName": slug, "jobPostingId": job_id})
    posting = data.get("jobPosting")
    if not isinstance(posting, dict):
        raise AshbyNotFound(f"job '{job_id}' not found on board '{slug}'")
    return posting


# ---- fan-out ------------------------------------------------------------------------

_fanout = ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="ashby-fanout")


def run_parallel(fns):
    if not fns:
        return []
    if len(fns) == 1 or threading.current_thread().name.startswith("ashby-fanout"):
        # A fan-out task that fans out again (jobs/search -> board_bundle ->
        # posting + GraphQL) must not wait on this same pool: with every
        # worker blocked on nested futures nothing could run them.
        return [fn() for fn in fns]
    futures = [_fanout.submit(fn) for fn in fns[1:]]
    first = fns[0]()
    return [first] + [f.result() for f in futures]


def soft(fn, default=None):
    """Wrap an optional lookup: any upstream failure or a missing entity
    yields `default` instead of failing the endpoint."""
    def run():
        try:
            return fn()
        except (UpstreamError, BadRequest, NotFound):
            return default
    return run


if __name__ == "__main__":
    # Smoke test: python ashby/fetch.py [board slug]
    slug = sys.argv[1] if len(sys.argv) > 1 else "ramp"
    org = organization(slug)
    print(f"organization: {org.get('name')} ({org.get('publicWebsite')})")
    board = board_with_teams(slug)
    print(f"board: {len(board.get('teams') or [])} teams, {len(board.get('jobPostings') or [])} postings")
    doc = posting_board(slug)
    jobs = doc.get("jobs") or []
    print(f"posting api: {len(jobs)} jobs")
    if jobs:
        job = job_posting(slug, jobs[0]["id"])
        form = job.get("applicationForm") or {}
        fields = sum(len(s.get("fieldEntries") or []) for s in form.get("sections") or [])
        print(f"job: {job.get('title')!r} — {fields} application fields")
