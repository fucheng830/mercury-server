"""Scheduled daily recap generation."""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from recap_config import get_config
from recap.llm_service import LLMService
from recap.recap_engine import generate_recap

logger = logging.getLogger(__name__)

_scheduler = None


def start_scheduler(provider, llm_service: LLMService):
    """Start the background scheduler for daily recap."""
    global _scheduler

    config = get_config()["recap"]["schedule"]
    if not config.get("enabled", False):
        logger.info("Recap scheduler disabled")
        return

    cron_parts = config.get("cron", "0 23 * * *").split()
    _scheduler = BackgroundScheduler(timezone=config.get("timezone", "Asia/Shanghai"))
    _scheduler.add_job(
        _run_daily_recap,
        "cron",
        hour=int(cron_parts[1]),
        minute=int(cron_parts[0]),
        args=[provider, llm_service],
        id="daily_recap",
        replace_existing=True,
    )
    # Memory iteration jobs
    try:
        from recap_config import get_iteration_config
        iter_config = get_iteration_config()

        daily_cron = iter_config.get("daily_cron", "59 23 * * *").split()
        _scheduler.add_job(
            _run_daily_ingestion, "cron",
            hour=int(daily_cron[1]), minute=int(daily_cron[0]),
            id="daily_ingestion", replace_existing=True,
        )
        # DB-based recap (reads sessions table, not local files)
        _scheduler.add_job(
            _run_db_recap, "cron",
            hour=0, minute=5,
            args=[llm_service],
            id="db_recap", replace_existing=True,
        )

        weekly_cron = iter_config.get("weekly_cron", "0 1 * * 0").split()
        _scheduler.add_job(
            _run_weekly_core_review, "cron",
            hour=int(weekly_cron[1]), minute=int(weekly_cron[0]),
            day_of_week=int(weekly_cron[4]),
            id="weekly_core_review", replace_existing=True,
        )

        monthly_cron = iter_config.get("monthly_cron", "0 2 1 * *").split()
        _scheduler.add_job(
            _run_monthly_graph_maintenance, "cron",
            hour=int(monthly_cron[1]), minute=int(monthly_cron[0]),
            day=int(monthly_cron[2]),
            id="monthly_graph_maintenance", replace_existing=True,
        )

        logger.info("Memory iteration jobs registered")
    except Exception as e:
        logger.warning(f"Failed to register iteration jobs: {e}")

    _scheduler.start()
    logger.info(f"Recap scheduler started: {config.get('cron')}")


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown()
        logger.info("Recap scheduler stopped")


def _run_daily_recap(provider, llm_service: LLMService):
    """Job: generate recap for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Running scheduled recap for {today}")
    try:
        result = generate_recap(provider, today, llm_service)
        if "error" in result:
            logger.warning(f"Recap generation: {result['error']}")
        else:
            logger.info(f"Recap generated for {today}")
    except Exception as e:
        logger.error(f"Scheduled recap failed: {e}")


def _run_db_recap(llm_service=None):
    """Job: run server-side recap from sessions table (DB-based)."""
    logger.info("Running DB-based server recap")
    try:
        from hermes.server_recap import run_server_recap
        results = run_server_recap(llm_service=llm_service)
        logger.info("DB recap stored %d memories", len(results))
    except Exception as e:
        logger.error("DB recap failed: %s", e)


def _run_daily_ingestion():
    logger.info("Running daily memory ingestion")
    try:
        from hermes.iteration import run_daily_ingestion
        result = run_daily_ingestion()
        logger.info(f"Daily ingestion: {result}")
    except Exception as e:
        logger.error(f"Daily ingestion failed: {e}")


def _run_weekly_core_review():
    logger.info("Running weekly core review")
    try:
        from hermes.iteration import run_weekly_core_review
        result = run_weekly_core_review()
        logger.info(f"Weekly core review: {result}")
    except Exception as e:
        logger.error(f"Weekly core review failed: {e}")


def _run_monthly_graph_maintenance():
    logger.info("Running monthly graph maintenance")
    try:
        from hermes.iteration import run_monthly_graph_maintenance
        result = run_monthly_graph_maintenance()
        logger.info(f"Monthly graph maintenance: {result}")
    except Exception as e:
        logger.error(f"Monthly graph maintenance failed: {e}")
