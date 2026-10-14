"""Cache TTL per /ashby/* endpoint (cache.py, keyed on the validated params).

A board changes when a recruiter publishes or closes a role — a few times a
day at the busiest employers, weekly at most. Job pages and application
forms are edited rarely once published.
"""
from datetime import timedelta

BOARD_CACHE = timedelta(minutes=30)      # boards/details (counts + facets + teams)
RESOLVE_CACHE = timedelta(days=1)        # boards/resolve (company -> slug)
JOBS_CACHE = timedelta(minutes=30)       # jobs/list and jobs/search pages
JOB_CACHE = timedelta(hours=1)           # jobs/details
FORM_CACHE = timedelta(hours=6)          # jobs/application-form
