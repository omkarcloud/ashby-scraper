"""/ashby/boards/* — one employer's hosted job board: the organization
record, job counts, filter facets and the team tree; and the company ->
board-slug resolver."""
from ashby import parsers as P
from ashby import refs
from ashby import shared
from ashby.fetch import AshbyNotFound, board_with_teams, organization, run_parallel, soft


def details(board):
    bundle = shared.board_bundle(board)
    org = P.organization(bundle["organization"]) if bundle["organization"] else P.board_summary(None, board)
    jobs = bundle["jobs"]
    return {
        "board": org,
        "stats": P.stats(jobs),
        "facets": P.facets(jobs),
        "teams": P.team_tree(bundle["teams_index"], bundle["team_counts"]),
    }


def resolve(company):
    """A company name, domain or website -> the Ashby boards that exist
    under the slugs it suggests (ramp.com -> ramp). Ashby has no board
    directory, so the lookup is by trying the likely slugs."""
    candidates = refs.company_slug_candidates(company)
    if not candidates:
        raise ValueError("company must be a company name, domain or website")
    orgs = run_parallel([soft(lambda s=s: organization(s), default=None) for s in candidates])
    found = [(slug, org) for slug, org in zip(candidates, orgs) if org]
    if not found:
        raise AshbyNotFound(f"no Ashby job board found for '{company}' (tried {', '.join(candidates)}); "
                            "pass the slug from https://jobs.ashbyhq.com/<slug> as `board` if you know it")
    boards = run_parallel([soft(lambda s=s: board_with_teams(s), default=None) for s, _ in found])
    rows = []
    for (slug, org), board in zip(found, boards):
        summary = P.board_summary(org, slug)
        summary["job_count"] = len(P.as_list((board or {}).get("jobPostings"))) if board else None
        rows.append(summary)
    return {"company": company, "tried": candidates, "count": len(rows), "boards": rows}
