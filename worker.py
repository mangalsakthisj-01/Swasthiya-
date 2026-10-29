"""Background scheduler for hotspot expiry, SLA Guardian, and CivicPulse clustering."""
from __future__ import annotations
import logging
import os
from apscheduler.schedulers.blocking import BlockingScheduler
from .bootstrap import bootstrap_admin
from .civicpulse import compute_signals
from .database import get_session_factory, init_db
from .services import process_scheduled_tasks

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("swasthyagrid.worker")


def run_jobs() -> None:
    factory = get_session_factory()
    with factory() as db:
        try:
            task_result = process_scheduled_tasks(db)
            signal_result = compute_signals(db, days=int(os.getenv("SG_CIVICPULSE_WINDOW_DAYS", "14")),
                                            radius_m=int(os.getenv("SG_CIVICPULSE_RADIUS_M", "1000")),
                                            threshold=int(os.getenv("SG_CIVICPULSE_THRESHOLD", "3")), persist=True)
            logger.info("Scheduled work: %s; stored CivicPulse signals: %d", task_result, len(signal_result))
        except Exception:
            db.rollback()
            logger.exception("Scheduled job failed; no success was assumed")


def main() -> None:
    init_db()
    with get_session_factory()() as db:
        bootstrap_admin(db)
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_jobs, "interval", minutes=1, id="sla-hotspot-jobs", max_instances=1, coalesce=True)
    scheduler.add_job(run_jobs, "interval", minutes=15, id="civicpulse-review-signals", max_instances=1, coalesce=True)
    run_jobs()
    logger.info("SwasthyaGrid worker started.")
    scheduler.start()

if __name__ == "__main__":
    main()
