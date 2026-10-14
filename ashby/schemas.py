"""Marshmallow request schemas for every /ashby/* route. Generic fields come
from the shared schema_fields.py; this module adds the Ashby resolvers.
Every schema's load() output is the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair):

    board     a job-board slug (ramp) OR any jobs.ashbyhq.com / posting-api link of the board
    boards    comma-separated slugs and/or links (max 20)
    job       a job posting id (34413f8d-26bf-4bbc-8ade-eb309a0e2245) OR a
              jobs.ashbyhq.com job link; a bare id needs `board` next to it
              (Ashby looks a job up only inside its board)
    company   a company name, domain or website (boards/resolve)

Choice values are literal lists here on purpose: the listing tooling reads
them from this file's AST.
"""
from marshmallow import ValidationError, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CurrencyField, DateField, Flag, NonNegativeNumber,
                           PageField, PageSizeField, QueryField, RefField, StrippedString)
from ashby import refs

WORKPLACE_TYPES = ["remote", "hybrid", "on_site"]
EMPLOYMENT_TYPES = ["full_time", "part_time", "contract", "intern", "temporary"]
SORTS = ["newest", "oldest", "title"]
DESCRIPTIONS = ["none", "text", "html", "both"]


# ---- id-or-link fields -------------------------------------------------------------

class BoardField(RefField):
    resolver = staticmethod(refs.resolve_board)


class BoardListField(RefField):
    resolver = staticmethod(refs.resolve_boards)


class JobField(RefField):
    resolver = staticmethod(refs.resolve_job)


# ---- schemas ---------------------------------------------------------------------------

class BoardSchema(BaseSchema):
    board = BoardField()


class ResolveSchema(BaseSchema):
    company = QueryField(max_length=120)


class JobFiltersSchema(BaseSchema):
    """The filters jobs/list and jobs/search share. Text filters are
    case-insensitive "contains" matches; `query` looks at the title,
    department, team path and location names."""
    query = StrippedString(load_default=None)
    department = StrippedString(load_default=None)
    team = StrippedString(load_default=None)
    location = StrippedString(load_default=None)
    country = StrippedString(load_default=None)
    workplace_type = ChoiceField(WORKPLACE_TYPES)
    employment_type = ChoiceField(EMPLOYMENT_TYPES)
    remote = Flag()
    posted_after = DateField()
    min_salary = NonNegativeNumber()
    currency = CurrencyField()
    sort = ChoiceField(SORTS, load_default="newest")
    page = PageField(max_page=1000)
    per_page = PageSizeField(default=50, max_size=100)
    description = ChoiceField(DESCRIPTIONS, load_default="none")


class JobsListSchema(JobFiltersSchema):
    board = BoardField()


class JobsSearchSchema(JobFiltersSchema):
    boards = BoardListField()


class JobSchema(BaseSchema):
    job = JobField()
    board = BoardField(required=False, load_default=None)

    @validates_schema
    def needs_board(self, data, **kwargs):
        job = data.get("job") or {}
        if job and not job.get("board") and not data.get("board"):
            raise ValidationError("board is required when job is a bare posting id "
                                  "(or pass the full jobs.ashbyhq.com/<board>/<id> link as job)", "board")
