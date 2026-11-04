"""Background jobs: status transitions, progress, failures, stale jobs after a restart."""

import threading
import time

from scgraph.jobs import JobRunner, JobStore


def wait(store, job_id, statuses=("done", "failed"), timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = store.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} still {store.get(job_id)['status']}")


def test_queued_running_done_with_progress(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    runner = JobRunner(store)
    started, release = threading.Event(), threading.Event()

    def work(job):
        job.report(step=1)
        started.set()
        release.wait(5)
        job.report(step=2)
        return {"answer": 42}

    job_id = runner.submit("test", "a job", work, {"step": 0})
    assert store.get(job_id)["status"] in ("queued", "running")
    assert started.wait(5)
    running = store.get(job_id)
    assert running["status"] == "running" and running["progress"] == {"step": 1} and running["result"] is None
    release.set()
    done = wait(store, job_id)
    assert done["status"] == "done" and done["progress"] == {"step": 2} and done["result"] == {"answer": 42}
    assert done["title"] == "a job" and done["kind"] == "test" and done["error"] is None
    runner.shutdown()


def test_failure_is_recorded_with_its_progress(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    runner = JobRunner(store)

    def work(job):
        job.report(done=1)
        raise ValueError("broken file")

    job = wait(store, runner.submit("test", "fails", work))
    assert job["status"] == "failed" and job["progress"] == {"done": 1}
    assert job["error"].startswith("ValueError: broken file")
    runner.shutdown()


def test_list_newest_first_by_kind(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite")
    ids = [store.create("ingest", f"job {i}") for i in range(3)]
    store.create("other", "x")
    assert [j["id"] for j in store.list("ingest")] == ids[::-1]
    assert len(store.list()) == 4 and len(store.list("ingest", limit=2)) == 2


def test_unfinished_jobs_fail_at_startup(tmp_path):
    path = tmp_path / "jobs.sqlite"
    store = JobStore(path)
    queued = store.create("ingest", "queued")
    running = store.create("ingest", "running")
    store.update(running, status="running")
    finished = store.create("ingest", "done")
    store.update(finished, status="done", result={})
    store.close()

    store = JobStore(path)
    JobRunner(store).shutdown()
    assert store.get(queued)["status"] == store.get(running)["status"] == "failed"
    assert "stopped" in store.get(running)["error"]
    assert store.get(finished)["status"] == "done"
