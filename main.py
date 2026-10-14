"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json.
"""
import json
import os

from ashby.boards import details as board_details
from ashby.jobs import details as job_details
from ashby.jobs import listing as company_jobs
from ashby.refs import resolve_job

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # every open job at one company, with filters (50 per page)
    save("jobs_openai_engineer.json", company_jobs("openai", query="engineer", workplace_type="hybrid"))

    # one full posting — a job link, or a bare id with board="openai"
    save("job_openai_chatgpt_infra.json", job_details(resolve_job(
        "https://jobs.ashbyhq.com/openai/e6981259-c1d0-46de-8376-56bde28cfb10")))

    # a company's hiring profile: logo, website, open jobs, team tree, jobs by department and location
    save("board_openai.json", board_details("openai"))
