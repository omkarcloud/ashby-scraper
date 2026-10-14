"""Ashby normalizers: the posting API's camelCase rows and the SPA's GraphQL
objects -> one clean snake_case shape per entity. Pure functions, no
network; every parser tolerates missing / null / wrong-typed fields and
never raises on a partial payload.

Output conventions (shared with the other scrapers here): `link` for
canonical page URLs (always absolute), `*_count` for counters, `is_*` /
`has_*` for booleans, ISO-8601 UTC timestamps ("2026-04-07T17:12:35Z") and
ISO dates ("2026-04-07"), numbers as numbers, money as a numeric amount next
to one `currency`, null for missing (never "" or an omitted key).

One job shape everywhere (list, search, details):

  job  {id, title, link, apply_link, board {name, slug}, department, team,
        team_path, employment_type, workplace_type, is_remote, is_listed,
        location, secondary_locations, compensation, published_at}
       + details: description, description_html, is_confidential,
         application_deadline, compensation.philosophy / guide_link,
         application {link, field_count, required_field_count, limit_notice,
         automated_processing_notice, texting_consent}

Deliberately dropped as noise:
  * SPA / tracking plumbing — __typename, ddRum* keys, recaptcha site keys
    and recaptchaAction, activeFeatureFlags, appConfirmationTrackingPixelHtml,
    candidateScheduleCancellationReasonRequirementStatus, showCookieBanner,
    showAutofillApplicationsBox, jobPostingBackUrl, theme.colors.version;
  * form render state that is empty for an anonymous reader — fieldValue,
    formControls (the Submit button), errorMessages, formErrors,
    sourceFormDefinitionId, field.metadata ({} everywhere seen),
    field.isDeactivated / isPrivate (always false on a published form),
    field.isNullable (= not required), field.__autoSerializationID (= type),
    field.path for custom questions (an opaque uuid; the `_systemfield_*`
    paths are kept as `system_field`), field.humanReadablePath ("" for
    custom questions, the title otherwise);
  * ids of locations and teams inside a job (locationId, teamId: resolved to
    names / the team path), the posting API's `department` + `team` once the
    team path is known (both are in it), and the compensation tier /
    component uuids (`id` on tiers is kept; components are positional).
"""
import html
import re
from datetime import datetime, timezone

from ashby import refs

# upstream enum -> public value (unknown values fall back to snake_case)
EMPLOYMENT_TYPES = {"FullTime": "full_time", "PartTime": "part_time", "Contract": "contract",
                    "Intern": "intern", "Temporary": "temporary"}
WORKPLACE_TYPES = {"OnSite": "on_site", "Hybrid": "hybrid", "Remote": "remote"}
COMPENSATION_TYPES = {"Salary": "salary", "EquityPercentage": "equity_percentage",
                      "EquityCashValue": "equity_cash_value", "Bonus": "bonus", "Commission": "commission"}
INTERVALS = {"1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week", "1 DAY": "day", "1 HOUR": "hour",
             "NONE": None}
FIELD_TYPES = {"String": "text", "LongText": "long_text", "Email": "email", "Phone": "phone",
               "Location": "location", "File": "file", "Boolean": "boolean", "ValueSelect": "select",
               "MultiValueSelect": "multi_select", "Date": "date", "Number": "number", "Url": "link"}

_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<\s*(?:br\s*/?|/p|/div|/li|/h[1-6]|/tr|/ul|/ol)\s*>", re.I)
_LI_RE = re.compile(r"<\s*li[^>]*>", re.I)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# ---- value helpers ------------------------------------------------------------
# (kept local so the package stands alone, like the other site packages)

def as_dict(value):
    return value if isinstance(value, dict) else {}


def as_list(value):
    return value if isinstance(value, list) else []


def text(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def to_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def to_float(value):
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def number(value):
    """int when whole, float otherwise (211400.0 -> 211400, 1.75 -> 1.75)."""
    out = to_float(value)
    if out is None:
        return None
    return int(out) if out.is_integer() else out


def bool_or_none(value):
    return value if isinstance(value, bool) else None


def snake(value):
    """'EquityCashValue' -> 'equity_cash_value' (enum fallback)."""
    value = text(value)
    if value is None:
        return None
    out = _CAMEL_RE.sub("_", value).replace(" ", "_").replace("-", "_").lower()
    return re.sub(r"_+", "_", out).strip("_") or None


def enum(value, table):
    value = text(value)
    if value is None:
        return None
    return table[value] if value in table else snake(value)


def clean_html(value):
    """Rich text -> plain text with paragraph / list breaks kept."""
    if not isinstance(value, str) or not value.strip():
        return None
    out = _LI_RE.sub("- ", value)
    out = _BREAK_RE.sub("\n", out)
    out = html.unescape(_TAG_RE.sub("", out)).replace("\xa0", " ").replace("\r", "")
    lines = [" ".join(line.split()) for line in out.split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out or None


def html_or_none(value):
    """An HTML string, or null when it is empty markup ('<p></p>')."""
    value = text(value)
    return value if value and clean_html(value) else None


def _parse_dt(value):
    value = text(value)
    if not value:
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso_datetime(value):
    """'2026-04-07T17:12:35.753+00:00' -> '2026-04-07T17:12:35Z' (UTC)."""
    dt = _parse_dt(value)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def iso_date(value):
    """'2026-04-07' / '2026-04-07T...' -> '2026-04-07' (the calendar date as written)."""
    value = text(value)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", value) if value else None
    return match.group(1) if match else None


def unique(values):
    out = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def pagination(page, per_page, total):
    total = to_int(total) or 0
    return {"page": page, "items_per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total else 0,
            "total_count": total}


# ---- organization ------------------------------------------------------------------

def _theme(org):
    return as_dict(as_dict(org).get("theme"))


def organization(raw):
    """The ApiOrganizationFromHostedJobsPageName object (or the board page's
    `organization`) -> the board / employer record."""
    org = as_dict(raw)
    theme = _theme(org)
    slug = text(org.get("hostedJobsPageSlug"))
    colors = as_dict(theme.get("colors"))
    return {
        "id": text(org.get("organizationId")),
        "name": text(org.get("name")),
        "slug": slug,
        "link": refs.board_link(slug),
        "website_link": text(org.get("publicWebsite")),
        "custom_jobs_page_link": text(org.get("customJobsPageUrl")),
        "logo_link": text(theme.get("logoSquareImageUrl")),
        "wordmark_link": text(theme.get("logoWordmarkImageUrl")),
        "privacy_policy_link": text(org.get("recruitingPrivacyPolicyUrl")),
        "timezone": text(org.get("timezone")),
        "primary_color": text(colors.get("colorPrimary600")) or text(colors.get("colorPrimary900")),
        "allows_search_engine_indexing": bool_or_none(org.get("allowJobPostIndexing")),
        "is_demo": bool_or_none(org.get("isDemoOrg")),
        "shows_job_filters": bool_or_none(theme.get("showJobFilters")),
        "shows_location_address": bool_or_none(theme.get("showLocationAddress")),
        "shows_teams": bool_or_none(theme.get("showTeams")),
        "intro": clean_html(theme.get("jobBoardTopDescriptionHtml")),
        "intro_html": html_or_none(theme.get("jobBoardTopDescriptionHtml")),
        "footer": clean_html(theme.get("jobBoardBottomDescriptionHtml")),
        "footer_html": html_or_none(theme.get("jobBoardBottomDescriptionHtml")),
        "application_success_message": text(theme.get("applicationSubmittedSuccessMessage")),
    }


def board_summary(org, slug=None):
    """The compact employer block carried by every job."""
    full = organization(org) if org else {}
    slug = full.get("slug") or slug
    return {
        "name": full.get("name"),
        "slug": slug,
        "link": refs.board_link(slug),
        "website_link": full.get("website_link"),
        "logo_link": full.get("logo_link"),
    }


# ---- teams ---------------------------------------------------------------------------

def teams_index(raw_teams):
    """GraphQL teams -> {id: {"id", "name", "parent_id"}} (the public
    externalName wins over the internal name)."""
    index = {}
    for team in as_list(raw_teams):
        team = as_dict(team)
        team_id = text(team.get("id"))
        if not team_id:
            continue
        index[team_id] = {
            "id": team_id,
            "name": text(team.get("externalName")) or text(team.get("name")),
            "parent_id": text(team.get("parentTeamId")),
        }
    return index


def team_path(index, team_id):
    """[root team name, ..., leaf team name] for a job's teamId."""
    names = []
    seen = set()
    node = index.get(team_id) if team_id else None
    while node and node["id"] not in seen:
        seen.add(node["id"])
        if node["name"]:
            names.append(node["name"])
        node = index.get(node["parent_id"]) if node.get("parent_id") else None
    names.reverse()
    return names


def team_tree(index, counts=None):
    """The team hierarchy as nested nodes with job counts (a node's count
    includes its descendants)."""
    counts = counts or {}
    children = {}
    for node in index.values():
        children.setdefault(node["parent_id"], []).append(node)

    def build(node, seen):
        if node["id"] in seen:
            return None
        seen = seen | {node["id"]}
        kids = [k for k in (build(c, seen) for c in children.get(node["id"], [])) if k]
        kids.sort(key=lambda k: (-(k["job_count"] or 0), k["name"] or ""))
        return {
            "id": node["id"],
            "name": node["name"],
            "job_count": (counts.get(node["id"]) or 0) + sum(k["job_count"] for k in kids),
            "children": kids,
        }

    roots = [r for r in (build(n, frozenset()) for n in index.values() if not n["parent_id"]) if r]
    roots.sort(key=lambda k: (-(k["job_count"] or 0), k["name"] or ""))
    return roots


# ---- locations -------------------------------------------------------------------------

def address(raw):
    """{"postalAddress": {...}} (or the postalAddress itself) -> the parts, or
    None when nothing is filled."""
    raw = as_dict(raw)
    postal = as_dict(raw.get("postalAddress")) or raw
    out = {
        "street": text(postal.get("streetAddress")),
        "city": text(postal.get("addressLocality")),
        "region": text(postal.get("addressRegion")),
        "postal_code": text(postal.get("postalCode")) or text(postal.get("addressPostalCode")),
        "country": text(postal.get("addressCountry")),
    }
    return out if any(out.values()) else None


def location(name, raw_address=None):
    """A location name plus its postal address -> one flat location."""
    name = text(name)
    parts = address(raw_address) or {}
    if not name and not parts:
        return None
    return {
        "name": name,
        "street": parts.get("street"),
        "city": parts.get("city"),
        "region": parts.get("region"),
        "postal_code": parts.get("postal_code"),
        "country": parts.get("country"),
    }


def secondary_locations(rows, names=None):
    """Posting-API secondaryLocations rows (name + address) or GraphQL
    name lists -> [location, ...]."""
    out = []
    for row in as_list(rows):
        if isinstance(row, dict):
            loc = location(row.get("location") or row.get("locationName"), row.get("address"))
        else:
            loc = location(row)
        if loc:
            out.append(loc)
    if not out:
        out = [loc for loc in (location(n) for n in as_list(names)) if loc]
    return out


# ---- compensation ------------------------------------------------------------------------

def component(raw):
    raw = as_dict(raw)
    return {
        "type": enum(raw.get("compensationType"), COMPENSATION_TYPES),
        "summary": text(raw.get("summary")),
        "min": number(raw.get("minValue")),
        "max": number(raw.get("maxValue")),
        "currency": text(raw.get("currencyCode")),
        "interval": enum(raw.get("interval"), INTERVALS) if text(raw.get("interval")) in INTERVALS
        else snake(raw.get("interval")),
    }


def tier(raw):
    raw = as_dict(raw)
    return {
        "id": text(raw.get("id")),
        "title": text(raw.get("title")),
        "summary": text(raw.get("tierSummary")),
        "additional_information": text(raw.get("additionalInformation")),
        "components": [component(c) for c in as_list(raw.get("components")) if isinstance(c, dict)],
    }


def compensation(raw, is_displayed=None):
    """The posting API's `compensation` block (or the GraphQL job's
    compensationTierSummary / compensationTiers) -> one block, or None when
    the employer publishes no pay."""
    raw = as_dict(raw)
    summary = text(raw.get("compensationTierSummary"))
    tiers = [tier(t) for t in as_list(raw.get("compensationTiers")) if isinstance(t, dict)]
    components = [component(c) for c in as_list(raw.get("summaryComponents")) if isinstance(c, dict)]
    salary = next((c for c in components if c["type"] == "salary"), None)
    if not (summary or tiers or components):
        return None
    types = {c["type"] for c in components} | {c["type"] for t in tiers for c in t["components"]}
    return {
        "summary": summary,
        "salary_summary": text(raw.get("scrapeableCompensationSalarySummary")),
        "salary": {k: salary[k] for k in ("min", "max", "currency", "interval")} if salary else None,
        "has_equity": any(t and t.startswith("equity") for t in types) or ("Equity" in (summary or "")),
        "has_bonus": "bonus" in types or ("Bonus" in (summary or "")),
        "has_commission": "commission" in types or ("Commission" in (summary or "")),
        "has_multiple_ranges": len(tiers) > 1 or ("Multiple Ranges" in (summary or "")),
        "is_displayed_on_board": bool_or_none(is_displayed),
        "components": components,
        "tiers": tiers,
    }


# ---- jobs ----------------------------------------------------------------------------------

def job(row, brief=None, path=None, board=None):
    """A posting-API row (+ its GraphQL board brief for the team id, + the
    resolved team path, + the board summary) -> the job object WITH the
    description fields (callers strip them per the `description` param)."""
    row = as_dict(row)
    brief = as_dict(brief)
    board = board or {}
    slug = board.get("slug")
    job_id = text(row.get("id")) or text(brief.get("id"))
    department = text(row.get("department"))
    team = text(row.get("team"))
    path = unique(path or []) or unique([department, team])
    return {
        "id": job_id,
        "title": text(row.get("title")) or text(brief.get("title")),
        "link": text(row.get("jobUrl")) or refs.job_link(slug, job_id),
        "apply_link": text(row.get("applyUrl")) or refs.apply_link(slug, job_id),
        "board": {"name": board.get("name"), "slug": slug},
        "department": department or (path[0] if path else None),
        "team": team or (path[-1] if path else None),
        "team_path": path,
        "employment_type": enum(row.get("employmentType") or brief.get("employmentType"), EMPLOYMENT_TYPES),
        "workplace_type": enum(row.get("workplaceType") or brief.get("workplaceType"), WORKPLACE_TYPES),
        "is_remote": bool_or_none(row.get("isRemote")),
        "is_listed": bool_or_none(row.get("isListed")),
        "location": location(row.get("location") or brief.get("locationName"), row.get("address")),
        "secondary_locations": secondary_locations(row.get("secondaryLocations") or brief.get("secondaryLocations")),
        "compensation": compensation(row.get("compensation"), row.get("shouldDisplayCompensationOnJobPostings"))
        or compensation({"compensationTierSummary": brief.get("compensationTierSummary")}),
        "published_at": iso_datetime(row.get("publishedAt")),
        "description": text(row.get("descriptionPlain")) or clean_html(row.get("descriptionHtml")),
        "description_html": html_or_none(row.get("descriptionHtml")),
    }


def with_description(job_obj, mode):
    """Copy a job keeping the description fields the caller asked for:
    none | text | html | both."""
    out = dict(job_obj)
    if mode not in ("text", "both"):
        out.pop("description", None)
    if mode not in ("html", "both"):
        out.pop("description_html", None)
    return out


def _texting_consent(gql):
    asked = bool_or_none(gql.get("shouldAskForTextingConsent"))
    whatsapp = bool_or_none(gql.get("shouldAskForWhatsAppConsent"))
    if not asked and not whatsapp:
        return None
    return {
        "is_sms_consent_requested": bool(asked),
        "is_whatsapp_consent_requested": bool(whatsapp),
        "legal_entity": text(gql.get("legalEntityNameForTextingConsent")),
        "privacy_policy_link": text(gql.get("candidateTextingPrivacyPolicyUrl")),
        "terms_link": text(gql.get("candidateTextingTermsAndConditionsUrl")),
    }


def job_details(gql, row=None, path=None, board=None):
    """The GraphQL job posting joined with its posting-API row -> the full
    job object (description, compensation philosophy, application summary)."""
    gql = as_dict(gql)
    row = as_dict(row)
    brief = {
        "id": gql.get("id"), "title": gql.get("title"), "locationName": gql.get("locationName"),
        "workplaceType": gql.get("workplaceType"), "employmentType": gql.get("employmentType"),
        "compensationTierSummary": gql.get("compensationTierSummary"),
        "secondaryLocations": as_list(gql.get("secondaryLocationNames")),
    }
    base = job(row, brief, path or as_list(gql.get("teamNames")), board)
    public_department = text(gql.get("departmentExternalName")) or text(gql.get("departmentName"))
    if public_department:
        base["department"] = public_department
    if not row and base["location"] and isinstance(gql.get("locationAddress"), dict):
        base["location"] = location(gql.get("locationName"), gql.get("locationAddress"))
    base["is_listed"] = bool_or_none(gql.get("isListed")) if base["is_listed"] is None else base["is_listed"]
    base["published_at"] = base["published_at"] or iso_date(gql.get("publishedDate"))
    comp = base["compensation"] or compensation(gql)
    if comp is not None:
        if not comp["tiers"]:
            comp["tiers"] = [tier(t) for t in as_list(gql.get("compensationTiers")) if isinstance(t, dict)]
        comp["salary_summary"] = comp["salary_summary"] or text(gql.get("scrapeableCompensationSalarySummary"))
        comp["philosophy"] = clean_html(gql.get("compensationPhilosophyHtml"))
        comp["guide_link"] = text(gql.get("compensationTierGuideUrl"))
    base["compensation"] = comp
    base["description_html"] = html_or_none(gql.get("descriptionHtml")) or base["description_html"]
    base["description"] = base["description"] or clean_html(gql.get("descriptionHtml"))
    form = application_form(gql.get("applicationForm"), gql.get("surveyForms"))
    notice = as_dict(gql.get("automatedProcessingLegalNotice"))
    out = dict(base)
    out.pop("description", None)
    out.pop("description_html", None)
    out.update({
        "is_confidential": bool_or_none(gql.get("isConfidential")),
        "application_deadline": iso_datetime(gql.get("applicationDeadline")) or iso_date(gql.get("applicationDeadline")),
        "application": {
            "link": base["apply_link"],
            "form_id": form["id"] if form else None,
            "field_count": form["field_count"] if form else None,
            "required_field_count": form["required_field_count"] if form else None,
            "has_survey": form["has_survey"] if form else None,
            "limit_notice": clean_html(gql.get("applicationLimitCalloutHtml")),
            "automated_processing_notice": clean_html(notice.get("automatedProcessingLegalNoticeHtml")),
            "texting_consent": _texting_consent(gql),
        },
        "description": base["description"],
        "description_html": base["description_html"],
    })
    return out


# ---- application form ---------------------------------------------------------------------

def form_field(entry):
    entry = as_dict(entry)
    field = as_dict(entry.get("field"))
    path = text(field.get("path")) or ""
    field_type = text(field.get("type"))
    options = [{"label": text(as_dict(o).get("label")), "value": text(as_dict(o).get("value"))}
               for o in as_list(field.get("selectableValues")) if isinstance(o, dict)]
    location_types = [t for t in (snake(x) for x in as_list(field.get("locationTypes"))) if t]
    return {
        "id": text(field.get("id")),
        "title": text(field.get("title")),
        "description": clean_html(entry.get("descriptionHtml")),
        "type": enum(field_type, FIELD_TYPES),
        "system_field": snake(path[len("_systemfield_"):]) if path.startswith("_systemfield_") else None,
        "is_required": bool(entry.get("isRequired")),
        "is_hidden": bool(entry.get("isHidden")),
        "accepts_multiple_values": bool(field.get("isMany")) or field_type == "MultiValueSelect",
        "options": options or None,
        "location_types": location_types or None,
    }


def form_section(raw):
    raw = as_dict(raw)
    fields = [form_field(e) for e in as_list(raw.get("fieldEntries")) if isinstance(e, dict)]
    return {
        "title": text(raw.get("title")),
        "description": clean_html(raw.get("descriptionHtml")),
        "is_hidden": bool(raw.get("isHidden")),
        "fields": fields,
    }


def _form(raw):
    raw = as_dict(raw)
    sections = [form_section(s) for s in as_list(raw.get("sections")) if isinstance(s, dict)]
    fields = [f for s in sections for f in s["fields"]]
    return {
        "id": text(raw.get("id")),
        "field_count": len(fields),
        "required_field_count": sum(1 for f in fields if f["is_required"]),
        "sections": sections,
    }


def application_form(raw, survey_forms=None):
    """The job's FormRender (+ its survey forms) -> sections of fields. None
    when the posting carries no form."""
    if not isinstance(raw, dict):
        return None
    out = _form(raw)
    surveys = [_form(s) for s in as_list(survey_forms) if isinstance(s, dict)]
    out["has_survey"] = bool(surveys)
    out["survey_forms"] = surveys
    return out


# ---- facets ---------------------------------------------------------------------------------

def _count(values):
    counts = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    rows = [{"name": k, "job_count": v} for k, v in counts.items()]
    rows.sort(key=lambda r: (-r["job_count"], r["name"]))
    return rows


def facets(jobs):
    """Filter values with counts for one board's jobs (the values the list
    endpoint's department / team / location / country / workplace_type /
    employment_type params accept)."""
    jobs = as_list(jobs)
    return {
        "departments": _count(j.get("department") for j in jobs),
        "teams": _count(j.get("team") for j in jobs),
        "locations": _count(j.get("location", {}).get("name") if j.get("location") else None for j in jobs),
        "countries": _count(j.get("location", {}).get("country") if j.get("location") else None for j in jobs),
        "workplace_types": _count(j.get("workplace_type") for j in jobs),
        "employment_types": _count(j.get("employment_type") for j in jobs),
    }


def stats(jobs):
    jobs = as_list(jobs)
    dates = sorted(d for d in (j.get("published_at") for j in jobs) if d)
    return {
        "job_count": len(jobs),
        "remote_count": sum(1 for j in jobs if j.get("is_remote")),
        "with_compensation_count": sum(1 for j in jobs if j.get("compensation")),
        "department_count": len({j.get("department") for j in jobs if j.get("department")}),
        "team_count": len({j.get("team") for j in jobs if j.get("team")}),
        "location_count": len({j["location"]["name"] for j in jobs if j.get("location") and j["location"].get("name")}),
        "newest_published_at": dates[-1] if dates else None,
        "oldest_published_at": dates[0] if dates else None,
    }
