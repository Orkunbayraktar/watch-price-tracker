"""Persistent daily scheduling for the existing active Watchlist update flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import logging
from threading import Lock, RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from database.db import db
from database.models import ScrapeSchedule
from services.scrape_run_service import get_running_scrape_run_summary
from services.watchlist_service import update_active_watchlist_items


logger = logging.getLogger(__name__)

DAILY_SCHEDULE_TYPE = "daily"
DEFAULT_TIMEZONE = "Europe/Istanbul"
JOB_ID_PREFIX = "watchlist-schedule-"
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
STATUS_SKIPPED_BUSY = "skipped_busy"
STATUS_SKIPPED_NO_ACTIVE_ITEMS = "skipped_no_active_items"

_scheduler: BackgroundScheduler | None = None
_scheduler_app: Flask | None = None
_lifecycle_lock = RLock()
_execution_lock = Lock()


class SchedulerServiceError(RuntimeError):
	"""Base error raised for schedule management failures."""


class SchedulerValidationError(SchedulerServiceError):
	"""Raised when submitted schedule fields are invalid."""

	def __init__(self, field_errors: dict[str, str]):
		self.field_errors = field_errors
		super().__init__("Review the highlighted schedule fields.")


class ScheduleNotFoundError(SchedulerServiceError):
	"""Raised when a requested schedule no longer exists."""


@dataclass(frozen=True, slots=True)
class SchedulerSummary:
	is_running: bool
	enabled_count: int
	next_run_at: datetime | None
	last_run_at: datetime | None
	last_run_status: str | None


@dataclass(frozen=True, slots=True)
class SchedulerPageData:
	summary: SchedulerSummary
	schedules: list[ScrapeSchedule]


@dataclass(frozen=True, slots=True)
class ScheduleExecutionResult:
	schedule_id: int
	status: str
	scrape_run_id: int | None
	message: str


def start_scheduler(app: Flask, *, force: bool = False) -> BackgroundScheduler | None:
	"""Start one background scheduler and restore enabled database schedules."""
	global _scheduler, _scheduler_app

	if not app.config.get("SCHEDULER_ENABLED", True):
		logger.info("Scheduler startup is disabled by configuration")
		return None
	if app.config.get("TESTING", False) and not force:
		logger.info("Scheduler startup skipped while TESTING is enabled")
		return None

	with _lifecycle_lock:
		if _scheduler is not None and _scheduler.running:
			return _scheduler

		timezone_name = app.config.get("SCHEDULER_TIMEZONE", DEFAULT_TIMEZONE)
		timezone_value = _load_timezone(timezone_name)
		scheduler = BackgroundScheduler(timezone=timezone_value)
		scheduler.start(paused=True)
		_scheduler = scheduler
		_scheduler_app = app

		try:
			with app.app_context():
				restore_enabled_schedules()
			scheduler.resume()
		except Exception:
			logger.exception("Scheduler startup failed while restoring persisted schedules")
			scheduler.shutdown(wait=False)
			_scheduler = None
			_scheduler_app = None
			raise

	logger.info("Scheduler started with %s enabled schedule(s)", _registered_job_count())
	return scheduler


def shutdown_scheduler(*, wait: bool = False) -> None:
	"""Stop the process-local scheduler and release its Flask app reference."""
	global _scheduler, _scheduler_app

	with _lifecycle_lock:
		scheduler = _scheduler
		_scheduler = None
		_scheduler_app = None
	if scheduler is not None and scheduler.running:
		scheduler.shutdown(wait=wait)
		logger.info("Scheduler stopped")


def restore_enabled_schedules() -> int:
	"""Replace in-memory jobs with the enabled schedules persisted in SQLite."""
	scheduler = _get_running_scheduler()
	if scheduler is None:
		return 0

	for job in scheduler.get_jobs():
		if job.id.startswith(JOB_ID_PREFIX):
			scheduler.remove_job(job.id)

	schedules = ScrapeSchedule.query.order_by(ScrapeSchedule.id.asc()).all()
	for schedule in schedules:
		if schedule.is_enabled:
			_register_schedule_job(schedule)
		else:
			schedule.next_run_at = None
	db.session.commit()
	logger.info("Restored %s enabled schedule(s)", sum(schedule.is_enabled for schedule in schedules))
	return sum(schedule.is_enabled for schedule in schedules)


def get_scheduler_page_data() -> SchedulerPageData:
	"""Return schedules and centralized runtime metadata for the Scheduler page."""
	_refresh_persisted_next_run_times()
	schedules = ScrapeSchedule.query.order_by(ScrapeSchedule.time_of_day.asc(), ScrapeSchedule.id.asc()).all()
	return SchedulerPageData(summary=_build_summary(schedules), schedules=schedules)


def get_scheduler_summary() -> SchedulerSummary:
	"""Return compact scheduler state for the Scraping Control Center."""
	_refresh_persisted_next_run_times()
	schedules = ScrapeSchedule.query.order_by(ScrapeSchedule.time_of_day.asc(), ScrapeSchedule.id.asc()).all()
	return _build_summary(schedules)


def get_schedule(schedule_id: int) -> ScrapeSchedule | None:
	return db.session.get(ScrapeSchedule, schedule_id)


def create_daily_schedule(name: str, time_value: str, *, is_enabled: bool = True) -> ScrapeSchedule:
	"""Validate, persist, and register one daily Istanbul-time schedule."""
	clean_name, parsed_time = _validate_schedule_fields(name, time_value)
	_ensure_not_duplicate(clean_name, parsed_time)
	schedule = ScrapeSchedule(
		name=clean_name,
		is_enabled=bool(is_enabled),
		schedule_type=DAILY_SCHEDULE_TYPE,
		time_of_day=parsed_time,
		timezone=DEFAULT_TIMEZONE,
	)
	try:
		db.session.add(schedule)
		db.session.flush()
		schedule.next_run_at = _calculate_next_run_at(schedule)
		db.session.commit()
	except SQLAlchemyError as error:
		db.session.rollback()
		raise SchedulerServiceError("The schedule could not be saved.") from error
	_sync_schedule_job(schedule.id)
	logger.info("Schedule created: id=%s name=%s enabled=%s", schedule.id, schedule.name, schedule.is_enabled)
	return schedule


def update_daily_schedule(
	schedule_id: int,
	name: str,
	time_value: str,
	*,
	is_enabled: bool,
) -> ScrapeSchedule:
	"""Update persisted fields and replace the corresponding in-memory job."""
	schedule = _require_schedule(schedule_id)
	clean_name, parsed_time = _validate_schedule_fields(name, time_value)
	_ensure_not_duplicate(clean_name, parsed_time, exclude_id=schedule_id)
	schedule.name = clean_name
	schedule.time_of_day = parsed_time
	schedule.is_enabled = bool(is_enabled)
	schedule.next_run_at = _calculate_next_run_at(schedule) if schedule.is_enabled else None
	try:
		db.session.commit()
	except SQLAlchemyError as error:
		db.session.rollback()
		raise SchedulerServiceError("The schedule could not be updated.") from error
	_sync_schedule_job(schedule.id)
	logger.info("Schedule updated: id=%s name=%s enabled=%s", schedule.id, schedule.name, schedule.is_enabled)
	return schedule


def set_schedule_enabled(schedule_id: int, is_enabled: bool) -> ScrapeSchedule:
	"""Persist enabled state and add or remove its APScheduler job immediately."""
	schedule = _require_schedule(schedule_id)
	schedule.is_enabled = bool(is_enabled)
	schedule.next_run_at = _calculate_next_run_at(schedule) if schedule.is_enabled else None
	try:
		db.session.commit()
	except SQLAlchemyError as error:
		db.session.rollback()
		raise SchedulerServiceError("The schedule status could not be changed.") from error
	_sync_schedule_job(schedule.id)
	logger.info("Schedule %s: id=%s", "enabled" if schedule.is_enabled else "disabled", schedule.id)
	return schedule


def delete_schedule(schedule_id: int) -> str:
	"""Remove schedule configuration without deleting any scrape history."""
	schedule = _require_schedule(schedule_id)
	name = schedule.name
	try:
		db.session.delete(schedule)
		db.session.commit()
	except SQLAlchemyError as error:
		db.session.rollback()
		raise SchedulerServiceError("The schedule could not be deleted.") from error
	_remove_registered_job(schedule_id)
	logger.info("Schedule deleted: id=%s name=%s", schedule_id, name)
	return name


def run_schedule_now(schedule_id: int) -> ScheduleExecutionResult:
	"""Run one schedule immediately through the same guarded Watchlist pipeline."""
	_require_schedule(schedule_id)
	return _execute_schedule(schedule_id, require_enabled=False)


def _scheduled_job(schedule_id: int) -> None:
	app = _scheduler_app
	if app is None:
		logger.error("Scheduler job %s has no Flask application context", schedule_id)
		return
	try:
		with app.app_context():
			_execute_schedule(schedule_id, require_enabled=True)
	except Exception:
		logger.exception("Unhandled scheduler job error for schedule %s", schedule_id)


def _execute_schedule(schedule_id: int, *, require_enabled: bool) -> ScheduleExecutionResult:
	schedule = _require_schedule(schedule_id)
	if require_enabled and not schedule.is_enabled:
		_remove_registered_job(schedule_id)
		return ScheduleExecutionResult(schedule_id, "disabled", None, "The disabled schedule was not run.")

	attempted_at = datetime.now(timezone.utc)
	if not _execution_lock.acquire(blocking=False):
		return _record_execution_outcome(
			schedule_id,
			status=STATUS_SKIPPED_BUSY,
			scrape_run_id=None,
			attempted_at=attempted_at,
			message="Automatic update skipped because another scheduler update is already running.",
		)

	try:
		running_run = get_running_scrape_run_summary()
		if running_run is not None:
			logger.info(
				"Schedule %s skipped because scrape run %s is running",
				schedule_id,
				running_run.run_id,
			)
			return _record_execution_outcome(
				schedule_id,
				status=STATUS_SKIPPED_BUSY,
				scrape_run_id=None,
				attempted_at=attempted_at,
				message=f"Update skipped because scrape run {running_run.run_id} is already running.",
			)

		schedule.last_run_at = attempted_at
		schedule.last_run_status = STATUS_RUNNING
		schedule.last_scrape_run_id = None
		db.session.commit()
		logger.info("Automatic Watchlist update started for schedule %s", schedule_id)

		try:
			batch_result = update_active_watchlist_items()
		except Exception as error:
			db.session.rollback()
			logger.exception("Automatic Watchlist update failed for schedule %s", schedule_id)
			return _record_execution_outcome(
				schedule_id,
				status=STATUS_FAILED,
				scrape_run_id=None,
				attempted_at=attempted_at,
				message="The scheduled Watchlist update failed. Future runs remain scheduled.",
				error=error,
			)

		if batch_result is None:
			logger.info("Schedule %s skipped because no active Watchlist items exist", schedule_id)
			return _record_execution_outcome(
				schedule_id,
				status=STATUS_SKIPPED_NO_ACTIVE_ITEMS,
				scrape_run_id=None,
				attempted_at=attempted_at,
				message="No active Watchlist items were available; no ScrapeRun was created.",
			)

		message = (
			f"Watchlist update completed in run {batch_result.run_id}: "
			f"{batch_result.successful} successful and {batch_result.failed} failed."
		)
		logger.info("Automatic Watchlist update completed for schedule %s: %s", schedule_id, message)
		return _record_execution_outcome(
			schedule_id,
			status=batch_result.status,
			scrape_run_id=batch_result.run_id,
			attempted_at=attempted_at,
			message=message,
		)
	finally:
		_execution_lock.release()


def _record_execution_outcome(
	schedule_id: int,
	*,
	status: str,
	scrape_run_id: int | None,
	attempted_at: datetime,
	message: str,
	error: Exception | None = None,
) -> ScheduleExecutionResult:
	schedule = get_schedule(schedule_id)
	if schedule is not None:
		schedule.last_run_at = attempted_at
		schedule.last_run_status = status
		schedule.last_scrape_run_id = scrape_run_id
		schedule.next_run_at = _calculate_next_run_at(schedule) if schedule.is_enabled else None
		try:
			db.session.commit()
		except SQLAlchemyError:
			db.session.rollback()
			logger.exception("Could not persist outcome for schedule %s", schedule_id)
			if error is None:
				raise SchedulerServiceError("The schedule outcome could not be saved.")
	return ScheduleExecutionResult(schedule_id, status, scrape_run_id, message)


def _sync_schedule_job(schedule_id: int) -> None:
	schedule = get_schedule(schedule_id)
	if schedule is None:
		_remove_registered_job(schedule_id)
		return
	scheduler = _get_running_scheduler()
	if scheduler is not None and schedule.is_enabled:
		_register_schedule_job(schedule)
	elif scheduler is not None:
		_remove_registered_job(schedule_id)
	schedule.next_run_at = _next_run_for_schedule(schedule) if schedule.is_enabled else None
	db.session.commit()


def _register_schedule_job(schedule: ScrapeSchedule):
	scheduler = _get_running_scheduler()
	if scheduler is None:
		return None
	job = scheduler.add_job(
		_scheduled_job,
		trigger=_build_trigger(schedule),
		args=[schedule.id],
		id=_job_id(schedule.id),
		name=schedule.name,
		replace_existing=True,
		coalesce=True,
		max_instances=1,
		misfire_grace_time=_misfire_grace_seconds(),
	)
	schedule.next_run_at = _ensure_utc(job.next_run_time) if job.next_run_time is not None else None
	logger.info("Schedule registered: id=%s next_run=%s", schedule.id, schedule.next_run_at)
	return job


def _remove_registered_job(schedule_id: int) -> None:
	scheduler = _get_running_scheduler()
	if scheduler is not None and scheduler.get_job(_job_id(schedule_id)) is not None:
		scheduler.remove_job(_job_id(schedule_id))


def _refresh_persisted_next_run_times() -> None:
	schedules = ScrapeSchedule.query.all()
	changed = False
	for schedule in schedules:
		next_run_at = _next_run_for_schedule(schedule) if schedule.is_enabled else None
		if _normalized_datetime(schedule.next_run_at) != _normalized_datetime(next_run_at):
			schedule.next_run_at = next_run_at
			changed = True
	if changed:
		db.session.commit()


def _next_run_for_schedule(schedule: ScrapeSchedule) -> datetime | None:
	scheduler = _get_running_scheduler()
	if scheduler is not None:
		job = scheduler.get_job(_job_id(schedule.id))
		if job is not None and job.next_run_time is not None:
			return _ensure_utc(job.next_run_time)
	return _calculate_next_run_at(schedule)


def _calculate_next_run_at(schedule: ScrapeSchedule, now: datetime | None = None) -> datetime:
	now_value = _ensure_utc(now or datetime.now(timezone.utc))
	next_fire_time = _build_trigger(schedule).get_next_fire_time(None, now_value)
	if next_fire_time is None:
		raise SchedulerServiceError("The next schedule time could not be calculated.")
	return _ensure_utc(next_fire_time)


def _build_trigger(schedule: ScrapeSchedule) -> CronTrigger:
	return CronTrigger(
		hour=schedule.time_of_day.hour,
		minute=schedule.time_of_day.minute,
		timezone=_load_timezone(schedule.timezone),
	)


def _validate_schedule_fields(name: str, time_value: str) -> tuple[str, time]:
	field_errors: dict[str, str] = {}
	clean_name = " ".join((name or "").split())
	if not clean_name:
		field_errors["name"] = "Enter a schedule name."
	elif len(clean_name) > 120:
		field_errors["name"] = "Schedule names must be 120 characters or fewer."

	try:
		parsed_time = datetime.strptime((time_value or "").strip(), "%H:%M").time()
	except (TypeError, ValueError):
		field_errors["time_of_day"] = "Enter a valid time in 24-hour HH:MM format."
		parsed_time = time(0, 0)

	if field_errors:
		raise SchedulerValidationError(field_errors)
	return clean_name, parsed_time


def _ensure_not_duplicate(name: str, time_of_day: time, *, exclude_id: int | None = None) -> None:
	query = ScrapeSchedule.query.filter(
		func.lower(ScrapeSchedule.name) == name.lower(),
		ScrapeSchedule.time_of_day == time_of_day,
	)
	if exclude_id is not None:
		query = query.filter(ScrapeSchedule.id != exclude_id)
	if query.first() is not None:
		raise SchedulerValidationError(
			{"name": "A schedule with this name and time already exists."}
		)


def _require_schedule(schedule_id: int) -> ScrapeSchedule:
	schedule = get_schedule(schedule_id)
	if schedule is None:
		raise ScheduleNotFoundError("The schedule no longer exists.")
	return schedule


def _build_summary(schedules: list[ScrapeSchedule]) -> SchedulerSummary:
	enabled_schedules = [schedule for schedule in schedules if schedule.is_enabled]
	last_schedule = max(
		(schedule for schedule in schedules if schedule.last_run_at is not None),
		key=lambda schedule: _ensure_utc(schedule.last_run_at),
		default=None,
	)
	next_run_at = min(
		(_ensure_utc(schedule.next_run_at) for schedule in enabled_schedules if schedule.next_run_at is not None),
		default=None,
	)
	return SchedulerSummary(
		is_running=_get_running_scheduler() is not None,
		enabled_count=len(enabled_schedules),
		next_run_at=next_run_at,
		last_run_at=None if last_schedule is None else _ensure_utc(last_schedule.last_run_at),
		last_run_status=None if last_schedule is None else last_schedule.last_run_status,
	)


def _get_running_scheduler() -> BackgroundScheduler | None:
	with _lifecycle_lock:
		if _scheduler is None or not _scheduler.running:
			return None
		return _scheduler


def _registered_job_count() -> int:
	scheduler = _get_running_scheduler()
	if scheduler is None:
		return 0
	return sum(job.id.startswith(JOB_ID_PREFIX) for job in scheduler.get_jobs())


def _job_id(schedule_id: int) -> str:
	return f"{JOB_ID_PREFIX}{schedule_id}"


def _misfire_grace_seconds() -> int:
	app = _scheduler_app
	if app is None:
		return 60
	return max(1, int(app.config.get("SCHEDULER_MISFIRE_GRACE_SECONDS", 60)))


def _load_timezone(timezone_name: str) -> ZoneInfo:
	try:
		return ZoneInfo(timezone_name)
	except ZoneInfoNotFoundError as error:
		raise SchedulerServiceError(f"Timezone data is unavailable for {timezone_name}.") from error


def _normalized_datetime(value: datetime | None) -> datetime | None:
	if value is None:
		return None
	return _ensure_utc(value).replace(microsecond=0)


def _ensure_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)
