"""Scheduler persistence, lifecycle, and Watchlist orchestration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from flask import has_app_context

from app import create_app
from database.db import db, initialize_database
from database.models import ScrapeRun, ScrapeSchedule, WatchlistItem
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult
from services.data_management_service import clear_scrape_history, reset_all_data
from services.scheduler_service import (
	STATUS_FAILED,
	STATUS_SKIPPED_BUSY,
	STATUS_SKIPPED_NO_ACTIVE_ITEMS,
	SchedulerValidationError,
	create_daily_schedule,
	delete_schedule,
	get_scheduler_page_data,
	restore_enabled_schedules,
	run_schedule_now,
	set_schedule_enabled,
	shutdown_scheduler,
	start_scheduler,
	update_daily_schedule,
)


TRENDYOL_URL = "https://www.trendyol.com/casio/f-91w-p-1001"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df"


@pytest.fixture
def app_context(tmp_path: Path):
	shutdown_scheduler(wait=False)
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"DISCOVERY_STATE_DIR": tmp_path / "discovery",
		}
	)
	context = app.app_context()
	context.push()
	initialize_database(app)
	try:
		yield app
	finally:
		shutdown_scheduler(wait=False)
		db.session.remove()
		db.drop_all()
		db.engine.dispose()
		context.pop()


def make_batch_result(run_id: int, *, status: str = "completed", failed: int = 0) -> BatchScrapeResult:
	now = datetime.now(timezone.utc)
	return BatchScrapeResult(
		run_id=run_id,
		status=status,
		total=1,
		successful=0 if failed else 1,
		failed=failed,
		skipped=0,
		started_at=now,
		finished_at=now + timedelta(seconds=1),
		duration=timedelta(seconds=1),
		item_results=[
			BatchScrapeItemResult(
				url=TRENDYOL_URL,
				platform="trendyol",
				status="failed" if failed else "success",
				failure_reason="blocked_by_platform" if failed else None,
				error_message="HTTP 403" if failed else None,
				listing_id=None,
				product_name=None,
				current_price=None,
				currency=None,
				started_at=now,
				finished_at=now + timedelta(seconds=1),
			)
		],
	)


def add_watchlist(url: str, *, platform: str, active: bool) -> WatchlistItem:
	item = WatchlistItem(platform=platform, url=url, is_active=active)
	db.session.add(item)
	db.session.commit()
	return item


def test_daily_schedule_is_persisted_with_istanbul_timezone_and_next_run(app_context) -> None:
	schedule = create_daily_schedule("Morning Update", "09:00")

	stored = db.session.get(ScrapeSchedule, schedule.id)
	assert stored is not None
	assert stored.name == "Morning Update"
	assert stored.schedule_type == "daily"
	assert stored.time_of_day.strftime("%H:%M") == "09:00"
	assert stored.timezone == "Europe/Istanbul"
	assert stored.is_enabled is True
	assert stored.next_run_at is not None


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_schedule_name_is_rejected(app_context, name: str) -> None:
	with pytest.raises(SchedulerValidationError) as error:
		create_daily_schedule(name, "09:00")

	assert "name" in error.value.field_errors
	assert ScrapeSchedule.query.count() == 0


@pytest.mark.parametrize("time_value", ["", "9am", "25:00", "09:30:15"])
def test_invalid_schedule_time_is_rejected(app_context, time_value: str) -> None:
	with pytest.raises(SchedulerValidationError) as error:
		create_daily_schedule("Morning", time_value)

	assert "time_of_day" in error.value.field_errors
	assert ScrapeSchedule.query.count() == 0


def test_exact_duplicate_schedule_is_rejected(app_context) -> None:
	create_daily_schedule("Morning Update", "09:00")

	with pytest.raises(SchedulerValidationError):
		create_daily_schedule("morning update", "09:00")

	assert ScrapeSchedule.query.count() == 1


def test_disabled_schedule_has_no_next_run(app_context) -> None:
	schedule = create_daily_schedule("Paused", "10:30", is_enabled=False)

	assert schedule.next_run_at is None
	assert get_scheduler_page_data().summary.enabled_count == 0


def test_schedule_can_be_edited_without_leaving_old_job(app_context) -> None:
	schedule = create_daily_schedule("Morning", "09:00")
	scheduler = start_scheduler(app_context, force=True)

	updated = update_daily_schedule(schedule.id, "Lunch", "12:30", is_enabled=True)
	job = scheduler.get_job(f"watchlist-schedule-{schedule.id}")

	assert updated.name == "Lunch"
	assert updated.time_of_day.strftime("%H:%M") == "12:30"
	assert job is not None and job.name == "Lunch"
	assert len(scheduler.get_jobs()) == 1


def test_schedule_can_be_disabled_and_enabled_at_runtime(app_context) -> None:
	schedule = create_daily_schedule("Morning", "09:00")
	scheduler = start_scheduler(app_context, force=True)

	set_schedule_enabled(schedule.id, False)
	assert scheduler.get_job(f"watchlist-schedule-{schedule.id}") is None
	assert db.session.get(ScrapeSchedule, schedule.id).next_run_at is None

	set_schedule_enabled(schedule.id, True)
	assert scheduler.get_job(f"watchlist-schedule-{schedule.id}") is not None
	assert db.session.get(ScrapeSchedule, schedule.id).next_run_at is not None


def test_deleting_schedule_preserves_scrape_run_history(app_context) -> None:
	run = ScrapeRun(platform="trendyol", status="completed")
	db.session.add(run)
	db.session.commit()
	schedule = create_daily_schedule("Morning", "09:00")
	schedule.last_scrape_run_id = run.id
	db.session.commit()

	delete_schedule(schedule.id)

	assert ScrapeSchedule.query.count() == 0
	assert db.session.get(ScrapeRun, run.id) is not None


def test_enabled_schedules_restore_and_disabled_schedules_do_not_register(app_context) -> None:
	enabled = create_daily_schedule("Morning", "09:00")
	disabled = create_daily_schedule("Evening", "18:00", is_enabled=False)
	scheduler = start_scheduler(app_context, force=True)

	job = scheduler.get_job(f"watchlist-schedule-{enabled.id}")
	assert job is not None
	assert job.coalesce is True
	assert job.max_instances == 1
	assert job.misfire_grace_time == 60
	assert scheduler.get_job(f"watchlist-schedule-{disabled.id}") is None
	assert restore_enabled_schedules() == 1


def test_scheduler_start_is_idempotent(app_context) -> None:
	first = start_scheduler(app_context, force=True)
	second = start_scheduler(app_context, force=True)

	assert first is second


def test_testing_app_does_not_start_scheduler_automatically(app_context) -> None:
	assert start_scheduler(app_context) is None
	assert get_scheduler_page_data().summary.is_running is False


def test_scheduler_restart_restores_persisted_schedules(app_context) -> None:
	schedule = create_daily_schedule("Morning", "09:00")
	start_scheduler(app_context, force=True)
	shutdown_scheduler(wait=False)

	restarted = start_scheduler(app_context, force=True)

	assert restarted.get_job(f"watchlist-schedule-{schedule.id}") is not None


def test_automatic_callback_uses_flask_context_and_persists_result(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	import services.scheduler_service as scheduler_service

	schedule = create_daily_schedule("Morning", "09:00")
	run = ScrapeRun(platform="trendyol", status="completed", products_found=1, listings_found=1)
	db.session.add(run)
	db.session.commit()
	context_states: list[bool] = []

	def fake_update():
		context_states.append(has_app_context())
		return make_batch_result(run.id)

	monkeypatch.setattr(scheduler_service, "update_active_watchlist_items", fake_update)
	start_scheduler(app_context, force=True)
	scheduler_service._scheduled_job(schedule.id)

	db.session.expire_all()
	stored = db.session.get(ScrapeSchedule, schedule.id)
	assert context_states == [True]
	assert stored.last_run_status == "completed"
	assert stored.last_scrape_run_id == run.id


def test_run_now_reuses_active_watchlist_pipeline_and_creates_scrape_run(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	add_watchlist(TRENDYOL_URL, platform="trendyol", active=True)
	add_watchlist(HEPSIBURADA_URL, platform="hepsiburada", active=False)
	schedule = create_daily_schedule("Morning", "09:00")
	captured: dict[str, object] = {}

	def fake_run_batch(urls, fetch_strategy="requests", **_kwargs):
		captured["urls"] = urls
		captured["fetch_strategy"] = fetch_strategy
		run = ScrapeRun(platform="trendyol", status="completed", products_found=1, listings_found=1)
		db.session.add(run)
		db.session.commit()
		return make_batch_result(run.id)

	monkeypatch.setattr("services.watchlist_service.run_batch", fake_run_batch)
	result = run_schedule_now(schedule.id)
	stored = db.session.get(ScrapeSchedule, schedule.id)

	assert captured == {"urls": [TRENDYOL_URL], "fetch_strategy": "playwright"}
	assert result.status == "completed"
	assert ScrapeRun.query.count() == 1
	assert stored.last_scrape_run_id == result.scrape_run_id
	assert stored.last_run_at is not None
	assert stored.next_run_at is not None


def test_hepsiburada_blocked_result_does_not_crash_future_schedule(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	add_watchlist(HEPSIBURADA_URL, platform="hepsiburada", active=True)
	schedule = create_daily_schedule("Evening", "18:00")
	run = ScrapeRun(platform="hepsiburada", status="failed", errors_count=1)
	db.session.add(run)
	db.session.commit()
	monkeypatch.setattr(
		"services.scheduler_service.update_active_watchlist_items",
		lambda: make_batch_result(run.id, status="failed", failed=1),
	)

	result = run_schedule_now(schedule.id)

	assert result.status == "failed"
	assert db.session.get(ScrapeSchedule, schedule.id).last_run_status == "failed"
	assert db.session.get(ScrapeSchedule, schedule.id).next_run_at is not None


def test_running_scrape_prevents_overlap_and_records_skipped_busy(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	running = ScrapeRun(platform="trendyol", status="running")
	db.session.add(running)
	db.session.commit()
	schedule = create_daily_schedule("Morning", "09:00")
	monkeypatch.setattr(
		"services.scheduler_service.update_active_watchlist_items",
		lambda: pytest.fail("Busy schedules must not enter the Watchlist pipeline"),
	)

	result = run_schedule_now(schedule.id)

	assert result.status == STATUS_SKIPPED_BUSY
	assert db.session.get(ScrapeSchedule, schedule.id).last_run_status == STATUS_SKIPPED_BUSY
	assert ScrapeRun.query.count() == 1


def test_process_lock_prevents_two_scheduler_executions(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	import services.scheduler_service as scheduler_service

	schedule = create_daily_schedule("Morning", "09:00")
	monkeypatch.setattr(
		scheduler_service,
		"update_active_watchlist_items",
		lambda: pytest.fail("A second scheduler execution must be skipped"),
	)
	scheduler_service._execution_lock.acquire()
	try:
		result = run_schedule_now(schedule.id)
	finally:
		scheduler_service._execution_lock.release()

	assert result.status == STATUS_SKIPPED_BUSY
	assert db.session.get(ScrapeSchedule, schedule.id).last_run_status == STATUS_SKIPPED_BUSY


def test_no_active_items_is_a_safe_no_op_without_scrape_run(app_context) -> None:
	add_watchlist(TRENDYOL_URL, platform="trendyol", active=False)
	schedule = create_daily_schedule("Morning", "09:00")

	result = run_schedule_now(schedule.id)

	assert result.status == STATUS_SKIPPED_NO_ACTIVE_ITEMS
	assert ScrapeRun.query.count() == 0
	assert db.session.get(ScrapeSchedule, schedule.id).last_scrape_run_id is None


def test_job_exception_is_recorded_without_disabling_future_runs(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	add_watchlist(TRENDYOL_URL, platform="trendyol", active=True)
	schedule = create_daily_schedule("Morning", "09:00")
	monkeypatch.setattr(
		"services.scheduler_service.update_active_watchlist_items",
		lambda: (_ for _ in ()).throw(RuntimeError("simulated failure")),
	)

	result = run_schedule_now(schedule.id)
	stored = db.session.get(ScrapeSchedule, schedule.id)

	assert result.status == STATUS_FAILED
	assert stored.last_run_status == STATUS_FAILED
	assert stored.is_enabled is True
	assert stored.next_run_at is not None


def test_run_now_is_allowed_for_disabled_schedule(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	schedule = create_daily_schedule("Manual Only", "09:00", is_enabled=False)
	monkeypatch.setattr("services.scheduler_service.update_active_watchlist_items", lambda: None)

	result = run_schedule_now(schedule.id)

	assert result.status == STATUS_SKIPPED_NO_ACTIVE_ITEMS
	assert db.session.get(ScrapeSchedule, schedule.id).is_enabled is False


def test_clear_scrape_history_unlinks_schedule_and_preserves_configuration(app_context) -> None:
	run = ScrapeRun(platform="trendyol", status="completed")
	db.session.add(run)
	db.session.commit()
	schedule = create_daily_schedule("Morning", "09:00")
	schedule.last_scrape_run_id = run.id
	db.session.commit()

	clear_scrape_history()

	stored = db.session.get(ScrapeSchedule, schedule.id)
	assert stored is not None
	assert stored.last_scrape_run_id is None
	assert ScrapeRun.query.count() == 0


def test_reset_all_preserves_schedule_configuration_without_dangling_run(app_context) -> None:
	run = ScrapeRun(platform="trendyol", status="completed")
	db.session.add(run)
	db.session.commit()
	schedule = create_daily_schedule("Morning", "09:00")
	schedule.last_scrape_run_id = run.id
	db.session.commit()

	reset_all_data("RESET")

	stored = db.session.get(ScrapeSchedule, schedule.id)
	assert stored is not None
	assert stored.last_scrape_run_id is None
	assert ScrapeRun.query.count() == 0
