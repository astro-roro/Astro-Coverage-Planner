"""A dummy extension with a scheduled job.

Used by tests/test_ext_scheduled_jobs.py. At noon each day it counts the
plans with no filter rows, through plans_api, and keeps the figure.
"""
counts = []


def make_job(app):
    def tidy():
        doc = app.plans_api.read()
        counts.append(sum(1 for p in doc["plans"] if not p.get("filter_rows")))
    return {"id": "nightly_tidy", "cron": "0 12 * * *", "run": tidy}


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "scheduled_jobs" not in app.acp_extension_hooks:
        return
    app.scheduled_jobs.append(make_job(app))
