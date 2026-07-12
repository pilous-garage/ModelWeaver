"""Dépendances du service `tester`."""
from services.installer_worker.jobs import enqueue_job, job_status

CONSUMES = {
    "services.installer_worker": ["jobs"],
    "services.installer_worker.jobs": ["enqueue_job", "job_status"],
    "services._common": ["log_to_file", "acquire_instance_lock", "_db_paths"],
}
