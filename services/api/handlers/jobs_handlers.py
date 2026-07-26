from services.installer_worker import jobs
from services.api.router import register

# ── Jobs ────────────────────────────────────────────────────────────────

def op_jobs_list(_params):
    jobs.ensure_install_jobs()
    return jobs.list_jobs()


def op_jobs_add(params):
    ref = params.get("ref")
    job_type = params.get("job_type", "install")
    if not ref:
        return {"status": "error", "error": "missing 'ref'"}
    jobs.ensure_install_jobs()
    jid = jobs.enqueue_job(ref, job_type)
    return {"status": "ok", "job_id": jid, "duplicate": jid == 0}


def op_jobs_status(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    jobs.ensure_install_jobs()
    st, log = jobs.job_status(int(jid))
    return {"status": "ok", "job_status": st, "log": log}


def op_jobs_cancel(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    jobs.ensure_install_jobs()
    jobs.cancel_job(int(jid))
    return {"status": "ok"}


def op_jobs_clear(_params):
    jobs.ensure_install_jobs()
    jobs.clear_jobs()
    return {"status": "ok"}


# ── Route registration ─────────────────────────────────────────────────

register("jobs/list",    op_jobs_list)
register("jobs/add",     op_jobs_add)
register("jobs/status",  op_jobs_status)
register("jobs/cancel",  op_jobs_cancel)
register("jobs/clear",   op_jobs_clear)
