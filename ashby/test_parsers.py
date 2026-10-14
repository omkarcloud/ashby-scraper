"""Offline tests: ref parsing, the request schemas and the normalizers
against raw Ashby responses captured 2026-10-14 (ashby/fixtures/*.json).

    python -m pytest ashby/test_parsers.py -q
"""
import json
import os

import pytest

from schema_fields import load_query
from ashby import parsers as P
from ashby import refs
from ashby import schemas as S
from ashby import shared

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
JOB = "34413f8d-26bf-4bbc-8ade-eb309a0e2245"


def fx(name):
    with open(os.path.join(FIX, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _no_camel_or_url_keys(obj, path="$"):
    """Every emitted key is snake_case and no key is named *url*."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key == key.lower() and " " not in key, f"{path}.{key} not snake_case"
            assert "url" not in key, f"{path}.{key} should be a *link"
            assert "__" not in key, f"{path}.{key} leaks a GraphQL key"
            _no_camel_or_url_keys(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _no_camel_or_url_keys(value, f"{path}[{i}]")


def _no_empty_strings(obj, path="$"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            _no_empty_strings(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _no_empty_strings(value, f"{path}[{i}]")
    else:
        assert obj != "", f"{path} is an empty string (must be null)"


def _bundle():
    """shared._build_bundle on the fixtures, no network."""
    doc, board, org = fx("posting_board_ramp"), fx("gql_board_ramp"), fx("gql_organization_ramp")
    index = P.teams_index(board["teams"])
    briefs = {b["id"]: b for b in board["jobPostings"]}
    summary = P.board_summary(org, "ramp")
    jobs, counts = [], {}
    for row in doc["jobs"]:
        brief = briefs.get(row["id"])
        team_id = brief.get("teamId") if brief else None
        jobs.append(P.job(row, brief, P.team_path(index, team_id), summary))
        if team_id:
            counts[team_id] = counts.get(team_id, 0) + 1
    return {"organization": org, "summary": summary, "teams_index": index, "team_counts": counts,
            "briefs": briefs, "rows": {r["id"]: r for r in doc["jobs"]}, "jobs": jobs}


# ---- refs -----------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("ramp", "ramp"), (" Ramp ", "ramp"), ("https://jobs.ashbyhq.com/ramp", "ramp"),
    ("https://jobs.ashbyhq.com/Ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245?x=1", "ramp"),
    ("jobs.ashbyhq.com/openai", "openai"),
    ("https://api.ashbyhq.com/posting-api/job-board/ramp?includeCompensation=true", "ramp"),
])
def test_resolve_board(value, expected):
    assert refs.resolve_board(value) == expected


@pytest.mark.parametrize("value", ["", "https://boards.greenhouse.io/ramp", "https://jobs.ashbyhq.com/",
                                   "https://jobs.ashbyhq.com/api/x", "has space", "-bad"])
def test_resolve_board_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_board(value)


def test_resolve_boards():
    assert refs.resolve_boards("ramp, https://jobs.ashbyhq.com/openai,RAMP") == ["ramp", "openai"]
    with pytest.raises(ValueError):
        refs.resolve_boards(",".join(f"b{n}" for n in range(21)))


@pytest.mark.parametrize("value, expected", [
    (JOB, {"id": JOB, "board": None}),
    (JOB.upper(), {"id": JOB, "board": None}),
    (f"https://jobs.ashbyhq.com/ramp/{JOB}", {"id": JOB, "board": "ramp"}),
    (f"https://jobs.ashbyhq.com/Ramp/{JOB}/application?utm=1", {"id": JOB, "board": "ramp"}),
])
def test_resolve_job(value, expected):
    assert refs.resolve_job(value) == expected


@pytest.mark.parametrize("value", ["", "12345", "https://jobs.ashbyhq.com/ramp",
                                   "https://jobs.lever.co/ramp/" + JOB, "ramp/" + JOB])
def test_resolve_job_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_job(value)


@pytest.mark.parametrize("value, first", [
    ("Ramp", "ramp"), ("ramp.com", "ramp"), ("https://www.ramp.com/careers", "ramp"),
    ("Ramp Financial", "ramp-financial"), ("jobs.ramp.co.uk", "ramp"),
])
def test_company_slug_candidates(value, first):
    cands = refs.company_slug_candidates(value)
    assert cands[0] == first and len(cands) <= 6 and len(set(cands)) == len(cands)


# ---- schemas ----------------------------------------------------------------------------

def test_schemas():
    data, err = load_query(S.JobsListSchema, {"board": "https://jobs.ashbyhq.com/ramp", "remote": "yes",
                                             "workplace_type": "Hybrid", "posted_after": "2026-01-01",
                                             "min_salary": "150000", "currency": "usd", "per_page": "10"})
    assert err is None and data["board"] == "ramp" and data["remote"] is True
    assert data["workplace_type"] == "hybrid" and data["currency"] == "USD" and data["min_salary"] == 150000
    assert data["sort"] == "newest" and data["description"] == "none" and data["page"] == 1
    assert load_query(S.JobsListSchema, {"board": "ramp", "sort": "salary"})[1]
    assert load_query(S.JobsListSchema, {"board": "ramp", "typo": "x"})[1]
    assert load_query(S.JobsListSchema, {})[1]
    data, err = load_query(S.JobSchema, {"job": f"https://jobs.ashbyhq.com/ramp/{JOB}"})
    assert err is None and data["job"] == {"id": JOB, "board": "ramp"} and data["board"] is None
    data, err = load_query(S.JobSchema, {"job": JOB, "board": "ramp"})
    assert err is None and data["board"] == "ramp"
    _, err = load_query(S.JobSchema, {"job": JOB})
    assert err and "board" in err["errors"]
    data, err = load_query(S.JobsSearchSchema, {"boards": "ramp,openai"})
    assert err is None and data["boards"] == ["ramp", "openai"]
    assert load_query(S.ResolveSchema, {"company": "ramp.com"})[0]["company"] == "ramp.com"


# ---- organization / teams ---------------------------------------------------------------------

def test_organization():
    org = P.organization(fx("gql_organization_ramp"))
    _no_camel_or_url_keys(org)
    _no_empty_strings(org)
    assert org["name"] == "Ramp" and org["slug"] == "ramp" and org["link"] == "https://jobs.ashbyhq.com/ramp"
    assert org["website_link"] == "https://ramp.com" and org["logo_link"].startswith("https://")
    assert org["timezone"] == "America/New_York" and org["allows_search_engine_indexing"] is True
    assert org["primary_color"] == "#1F1F1F" and org["intro"] is None and org["is_demo"] is False
    summary = P.board_summary(fx("gql_organization_ramp"))
    assert set(summary) == {"name", "slug", "link", "website_link", "logo_link"}


def test_teams():
    board = fx("gql_board_ramp")
    index = P.teams_index(board["teams"])
    brief = next(b for b in board["jobPostings"] if b["id"] == JOB)
    assert P.team_path(index, brief["teamId"]) == ["Engineering", "Software Engineering - Industry", "Backend"]
    assert P.team_path(index, None) == [] and P.team_path(index, "nope") == []
    tree = P.team_tree(index, {brief["teamId"]: 2})
    _no_camel_or_url_keys(tree)
    eng = next(t for t in tree if t["name"] == "Engineering")
    assert eng["job_count"] == 2 and eng["children"][0]["children"][0]["name"] == "Backend"
    assert all(t["parent_id"] is None or t["parent_id"] in index for t in index.values())


# ---- jobs -----------------------------------------------------------------------------------

def test_job_row():
    bundle = _bundle()
    job = next(j for j in bundle["jobs"] if j["id"] == JOB)
    _no_camel_or_url_keys(job)
    _no_empty_strings(job)
    assert job["title"] == "Security Engineer, Cloud" and job["link"] == f"https://jobs.ashbyhq.com/ramp/{JOB}"
    assert job["apply_link"].endswith("/application") and job["board"] == {"name": "Ramp", "slug": "ramp"}
    assert job["department"] == "Engineering" and job["team"] == "Backend"
    assert job["team_path"] == ["Engineering", "Software Engineering - Industry", "Backend"]
    assert job["employment_type"] == "full_time" and job["workplace_type"] == "hybrid"
    assert job["is_remote"] is True and job["is_listed"] is True
    assert job["location"] == {"name": "New York, NY (HQ)", "street": None, "city": "New York City",
                               "region": "NY", "postal_code": None, "country": "USA"}
    assert [l["name"] for l in job["secondary_locations"]] == ["Remote (Canada)", "Remote (US)", "Miami, FL"]
    assert job["secondary_locations"][2]["city"] == "Miami"
    assert job["published_at"] == "2026-04-07T17:12:35Z"
    comp = job["compensation"]
    assert comp["salary"] == {"min": 211400, "max": 290600, "currency": "USD", "interval": "year"}
    assert comp["has_equity"] and not comp["has_bonus"] and comp["is_displayed_on_board"] is True
    assert comp["components"][0]["type"] == "equity_percentage" and comp["components"][1]["type"] == "salary"
    assert comp["tiers"][0]["components"][1]["min"] == 211400 and comp["has_multiple_ranges"] is False
    assert job["description"].lower().startswith("about ramp") and job["description_html"].startswith("<h1>")
    listed = P.with_description(job, "none")
    assert "description" not in listed and "description_html" not in listed
    assert "description" in P.with_description(job, "text") and "description_html" not in P.with_description(job, "text")
    assert set(P.with_description(job, "both")) == set(job)
    none = P.job(dict(bundle["rows"][JOB], compensation=None), bundle["briefs"][JOB])
    assert none["compensation"]["summary"] and none["compensation"]["salary"] is None   # the brief's summary only
    assert P.job(dict(bundle["rows"][JOB], compensation=None))["compensation"] is None
    intern = next(j for j in bundle["jobs"] if j["employment_type"] == "intern")
    assert intern["compensation"]["salary"]["interval"] == "month"


def test_multi_tier_compensation():
    row = fx("posting_row_multi_tier")
    comp = P.job(row)["compensation"]
    assert comp["has_multiple_ranges"] and len(comp["tiers"]) > 1 and comp["tiers"][0]["title"]
    assert comp["has_commission"] and comp["salary"]["currency"] == "EUR"
    assert all(c["type"] in ("salary", "equity_percentage", "equity_cash_value", "bonus", "commission")
               for t in comp["tiers"] for c in t["components"])


def test_job_details_and_form():
    bundle = _bundle()
    gql = fx("gql_job_ramp")
    brief = bundle["briefs"][JOB]
    path = P.team_path(bundle["teams_index"], brief["teamId"])
    job = P.job_details(gql, bundle["rows"][JOB], path, bundle["summary"])
    _no_camel_or_url_keys(job)
    _no_empty_strings(job)
    assert job["title"] == "Security Engineer, Cloud" and job["department"] == "Engineering"
    assert job["is_confidential"] is False and job["application_deadline"] is None
    assert job["compensation"]["philosophy"].startswith("The final compensation")
    assert job["compensation"]["salary"]["max"] == 290600 and job["compensation"]["tiers"][0]["components"]
    app = job["application"]
    assert app["link"] == job["apply_link"] and app["field_count"] == 13 and app["required_field_count"] == 11
    assert app["texting_consent"]["is_sms_consent_requested"] and app["texting_consent"]["legal_entity"]
    assert list(job)[-2:] == ["description", "description_html"]

    form = P.application_form(gql["applicationForm"], gql["surveyForms"])
    _no_camel_or_url_keys(form)
    _no_empty_strings(form)
    fields = form["sections"][0]["fields"]
    assert form["field_count"] == 13 and form["has_survey"] is False and form["survey_forms"] == []
    assert fields[0] == {"id": fields[0]["id"], "title": "Legal Name", "description": None, "type": "text",
                         "system_field": "name", "is_required": True, "is_hidden": False,
                         "accepts_multiple_values": False, "options": None, "location_types": None}
    assert [f["type"] for f in fields[:5]] == ["text", "email", "phone", "location", "file"]
    assert fields[3]["location_types"] == ["country", "region", "city"] and fields[4]["system_field"] == "resume"
    assert fields[7]["type"] == "boolean" and fields[9]["type"] == "long_text"

    # GraphQL only (no posting row): still a full job
    alone = P.job_details(gql, None, None, P.board_summary(None, "ramp"))
    assert alone["team_path"] == ["Engineering", "Software Engineering - Industry", "Backend"]
    assert alone["published_at"] == "2026-04-07" and alone["compensation"]["summary"]
    assert alone["link"] == f"https://jobs.ashbyhq.com/ramp/{JOB}" and alone["description"]


def test_form_field_options():
    entry = {"field": {"id": "f1", "title": "Languages", "type": "MultiValueSelect",
                       "selectableValues": [{"label": "German", "value": "German"}, {"label": "French", "value": "French"}]},
             "isRequired": True}
    field = P.form_field(entry)
    assert field["type"] == "multi_select" and field["accepts_multiple_values"] is True
    assert field["options"] == [{"label": "German", "value": "German"}, {"label": "French", "value": "French"}]
    assert P.form_field({})["type"] is None and P.application_form(None) is None


# ---- facets / filters -------------------------------------------------------------------------

def test_facets_stats_filters():
    jobs = _bundle()["jobs"]
    facets = P.facets(jobs)
    _no_camel_or_url_keys(facets)
    assert facets["departments"][0]["job_count"] >= 1 and facets["workplace_types"]
    stats = P.stats(jobs)
    assert stats["job_count"] == len(jobs) and stats["remote_count"] >= 1 and stats["newest_published_at"]

    assert [j["id"] for j in shared.filter_jobs(jobs, {"query": "security"})] == [JOB]
    assert shared.filter_jobs(jobs, {"team": "backend"})[0]["id"] == JOB
    assert shared.filter_jobs(jobs, {"location": "miami"})[0]["id"] == JOB
    assert shared.filter_jobs(jobs, {"country": "canada"})[0]["id"] == JOB
    assert all(j["workplace_type"] == "hybrid" for j in shared.filter_jobs(jobs, {"workplace_type": "hybrid"}))
    assert all(j["is_remote"] for j in shared.filter_jobs(jobs, {"remote": True}))
    assert all(not j["is_remote"] for j in shared.filter_jobs(jobs, {"remote": False}))
    assert shared.filter_jobs(jobs, {"min_salary": 250000})[0]["id"] == JOB
    assert shared.filter_jobs(jobs, {"min_salary": 10_000_000}) == []
    assert shared.filter_jobs(jobs, {"currency": "EUR"}) == []
    assert shared.filter_jobs(jobs, {"posted_after": "2099-01-01"}) == []
    assert shared.filter_jobs(jobs, {"employment_type": "intern"})[0]["employment_type"] == "intern"
    newest = shared.sort_jobs(jobs, "newest")
    assert newest[0]["published_at"] >= newest[-1]["published_at"]
    assert shared.sort_jobs(jobs, "oldest")[0]["id"] == newest[-1]["id"]
    titles = [j["title"].lower() for j in shared.sort_jobs(jobs, "title")]
    assert titles == sorted(titles)
    assert len(shared.page_of(jobs, 1, 2)) == 2 and shared.page_of(jobs, 99, 2) == []


# ---- robustness ---------------------------------------------------------------------------------

def test_partial_payloads_never_raise():
    assert P.job({})["id"] is None and P.job({"id": "x"})["compensation"] is None
    assert P.job_details({}, None, None, None)["application"]["field_count"] is None
    assert P.organization(None)["name"] is None and P.organization({"theme": "junk"})["logo_link"] is None
    assert P.compensation(None) is None and P.compensation({"summaryComponents": "x"}) is None
    assert P.address({"postalAddress": {}}) is None and P.location(None, None) is None
    assert P.team_tree({}) == [] and P.teams_index("x") == {}
    assert P.facets(None)["departments"] == [] and P.stats([])["newest_published_at"] is None
    assert P.iso_datetime("junk") is None and P.iso_date("2026-13") is None
    assert P.clean_html("<p>a&amp;b</p><ul><li>x</li></ul>") == "a&b\n- x"
    assert P.enum("SomethingNew", P.EMPLOYMENT_TYPES) == "something_new"
