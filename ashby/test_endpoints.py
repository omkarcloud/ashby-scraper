"""Live endpoint smoke tests: one call per /ashby/* route against a running
service, with example values proven to return data (2026-10-14). The
listing tooling reads each route's FIRST call from this file's AST as its
working example, so the values stay literals.

Skipped unless ASHBY_BASE points at a running service:

    ONLY_SCRAPER=ashby python run.py
    ASHBY_BASE=http://127.0.0.1:6002 python -m pytest ashby/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("ASHBY_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set ASHBY_BASE to run live endpoint tests")


def call(path, status=200, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=180)
    assert resp.status_code == status, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def test_board():
    board = call("/ashby/boards/details", board="ramp")
    assert board["board"]["name"] == "Ramp" and board["board"]["website_link"] == "https://ramp.com"
    assert board["stats"]["job_count"] > 50 and board["facets"]["departments"] and board["teams"]
    assert any(t["name"] == "Engineering" and t["children"] for t in board["teams"])
    assert call("/ashby/boards/details", board="https://jobs.ashbyhq.com/openai")["stats"]["job_count"] > 100
    found = call("/ashby/boards/resolve", company="ramp.com")
    assert found["boards"][0]["slug"] == "ramp" and found["boards"][0]["job_count"] > 50


def test_jobs_list():
    page = call("/ashby/jobs/list", board="ramp")
    assert page["count"] > 50 and page["per_page"] == 50 and page["next"] and len(page["jobs"]) == 50
    job = page["jobs"][0]
    assert job["link"].startswith("https://jobs.ashbyhq.com/ramp/") and job["team_path"] and job["published_at"]
    assert "description" not in job and job["board"]["slug"] == "ramp"
    filtered = call("/ashby/jobs/list", board="ramp", query="engineer", workplace_type="hybrid", description="text",
                    sort="oldest", per_page=5)
    assert 0 < filtered["count"] < page["count"] and len(filtered["jobs"]) == 5
    assert all("engineer" in j["title"].lower() or "engineer" in j["department"].lower()
               or any("engineer" in t.lower() for t in j["team_path"]) for j in filtered["jobs"])
    assert all(j["workplace_type"] == "hybrid" and j["description"] for j in filtered["jobs"])
    paid = call("/ashby/jobs/list", board="ramp", min_salary=200000, currency="USD")
    assert paid["count"] > 0 and all(j["compensation"]["salary"]["max"] >= 200000 for j in paid["jobs"])
    assert call("/ashby/jobs/list", board="ramp", remote="true", country="canada")["count"] > 0
    assert call("/ashby/jobs/list", board="ramp", page=2)["current_page"] == 2


def test_jobs_search():
    page = call("/ashby/jobs/search", boards="ramp,linear,this-board-does-not-exist-xyz", query="engineer")
    assert page["not_found"] == ["this-board-does-not-exist-xyz"] and len(page["boards"]) == 2
    assert page["count"] > 10 and {j["board"]["slug"] for j in page["jobs"]} <= {"ramp", "linear"}


def test_job_details_and_form():
    job = call("/ashby/jobs/details", job="https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245")["job"]
    assert job["title"] == "Security Engineer, Cloud" and job["description_html"] and job["description"]
    assert job["compensation"]["salary"]["currency"] == "USD" and job["compensation"]["philosophy"]
    assert job["application"]["field_count"] > 5 and job["team_path"][-1] == "Backend"
    same = call("/ashby/jobs/details", job="34413f8d-26bf-4bbc-8ade-eb309a0e2245", board="ramp")["job"]
    assert same["id"] == job["id"]
    form = call("/ashby/jobs/application-form", job="34413f8d-26bf-4bbc-8ade-eb309a0e2245", board="ramp")["form"]
    assert form["field_count"] > 5 and form["sections"][0]["fields"][0]["system_field"] == "name"
    assert any(f["type"] == "file" for s in form["sections"] for f in s["fields"])


def test_errors():
    # paths via variables so the listing tooling never reads an error case
    # as a route's working example
    board, listing, details = "/ashby/boards/details", "/ashby/jobs/list", "/ashby/jobs/details"
    form, resolve = "/ashby/jobs/application-form", "/ashby/boards/resolve"
    call(board, status=404, board="this-board-does-not-exist-xyz")
    call(board, status=400, board="https://boards.greenhouse.io/ramp")
    call(listing, status=400)
    call(listing, status=400, board="ramp", sort="salary")
    call(details, status=400, job="34413f8d-26bf-4bbc-8ade-eb309a0e2245")
    call(details, status=404, job="00000000-0000-0000-0000-000000000000", board="ramp")
    call(form, status=404, job="00000000-0000-0000-0000-000000000000", board="ramp")
    call(resolve, status=404, company="zzqqxxyy-no-such-company")
