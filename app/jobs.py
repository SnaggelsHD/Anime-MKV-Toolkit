import threading
import time
import uuid
from dataclasses import dataclass, field

MAX_JOBS = 50

_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    label: str
    total: int
    completed: int = 0
    status: str = "running"  # running | done | error
    results: list = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "total": self.total,
            "completed": self.completed,
            "status": self.status,
            "results": self.results,
            "error": self.error,
        }


def create_job(label: str, total: int) -> Job:
    job = Job(id=uuid.uuid4().hex, label=label, total=total)
    with _lock:
        _jobs[job.id] = job
        _prune_locked()
    return job


def add_result(job_id: str, result: dict) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.results.append(result)
        job.completed = len(job.results)


def finish_job(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.status = "done"


def fail_job(job_id: str, error: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.status = "error"
            job.error = error


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def list_jobs() -> list[Job]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def _prune_locked() -> None:
    if len(_jobs) <= MAX_JOBS:
        return
    oldest = sorted(_jobs.values(), key=lambda j: j.created_at)[: len(_jobs) - MAX_JOBS]
    for job in oldest:
        del _jobs[job.id]
