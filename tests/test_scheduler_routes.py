"""Scheduler page and POST action tests without live scraping."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import ScrapeRun, ScrapeSchedule
from services.scheduler_service import ScheduleExecutionResult, create_daily_schedule, shutdown_scheduler


@pytest.fixture
def client(tmp_path: Path):
	shutdown_scheduler(wait=False)
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"DISCOVERY_STATE_DIR": tmp_path / "discovery",
		}
	)
	with app.app_context():
		initialize_database(app)
		with app.test_client() as test_client:
			yield test_client
		shutdown_scheduler(wait=False)
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def test_scheduler_page_loads_with_empty_stopped_state(client) -> None:
	response = client.get("/scheduler")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Automatic Watchlist Updates" in body
	assert "No automatic schedules yet." in body
	assert "Stopped" in body
	assert "Europe/Istanbul" in body


def test_daily_schedule_can_be_created_from_page(client) -> None:
	response = client.post(
		"/scheduler",
		data={"name": "Morning Update", "time_of_day": "09:00", "is_enabled": "on"},
		follow_redirects=True,
	)

	assert response.status_code == 200
	assert "Daily schedule created." in response.get_data(as_text=True)
	schedule = ScrapeSchedule.query.one()
	assert schedule.name == "Morning Update"
	assert schedule.time_of_day.strftime("%H:%M") == "09:00"
	assert schedule.is_enabled is True


def test_blank_name_is_rejected_inline(client) -> None:
	response = client.post(
		"/scheduler",
		data={"name": " ", "time_of_day": "09:00", "is_enabled": "on"},
	)

	assert response.status_code == 400
	assert "Enter a schedule name." in response.get_data(as_text=True)
	assert ScrapeSchedule.query.count() == 0


def test_invalid_time_is_rejected_inline(client) -> None:
	response = client.post(
		"/scheduler",
		data={"name": "Morning", "time_of_day": "29:00", "is_enabled": "on"},
	)

	assert response.status_code == 400
	assert "valid time in 24-hour HH:MM format" in response.get_data(as_text=True)
	assert ScrapeSchedule.query.count() == 0


def test_unsafe_schedule_name_is_escaped(client) -> None:
	create_daily_schedule("<script>alert(1)</script>", "09:00")

	body = client.get("/scheduler").get_data(as_text=True)

	assert "<script>alert(1)</script>" not in body
	assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_schedule_edit_page_displays_current_values(client) -> None:
	schedule = create_daily_schedule("Morning", "09:00")

	response = client.get(f"/scheduler/{schedule.id}/edit")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Edit Schedule" in body
	assert 'value="Morning"' in body
	assert 'value="09:00"' in body


def test_schedule_can_be_edited_from_page(client) -> None:
	schedule = create_daily_schedule("Morning", "09:00")

	response = client.post(
		f"/scheduler/{schedule.id}/edit",
		data={"name": "Evening", "time_of_day": "18:00"},
		follow_redirects=True,
	)

	assert "Schedule updated." in response.get_data(as_text=True)
	stored = db.session.get(ScrapeSchedule, schedule.id)
	assert stored.name == "Evening"
	assert stored.time_of_day.strftime("%H:%M") == "18:00"
	assert stored.is_enabled is False


def test_schedule_can_be_disabled_and_enabled_from_page(client) -> None:
	schedule = create_daily_schedule("Morning", "09:00")

	disabled = client.post(f"/scheduler/{schedule.id}/toggle", follow_redirects=True)
	assert "Schedule disabled." in disabled.get_data(as_text=True)
	assert db.session.get(ScrapeSchedule, schedule.id).is_enabled is False

	enabled = client.post(f"/scheduler/{schedule.id}/toggle", follow_redirects=True)
	assert "Schedule enabled." in enabled.get_data(as_text=True)
	assert db.session.get(ScrapeSchedule, schedule.id).is_enabled is True


def test_schedule_delete_preserves_scrape_history(client) -> None:
	run = ScrapeRun(platform="trendyol", status="completed", started_at=datetime.now(timezone.utc))
	db.session.add(run)
	db.session.commit()
	schedule = create_daily_schedule("Morning", "09:00")
	schedule.last_scrape_run_id = run.id
	db.session.commit()

	response = client.post(f"/scheduler/{schedule.id}/delete", follow_redirects=True)

	assert "Scrape history was preserved." in response.get_data(as_text=True)
	assert ScrapeSchedule.query.count() == 0
	assert ScrapeRun.query.count() == 1


def test_run_now_route_uses_scheduler_service_result(client, monkeypatch: pytest.MonkeyPatch) -> None:
	schedule = create_daily_schedule("Morning", "09:00")
	captured: list[int] = []

	def fake_run_now(schedule_id: int) -> ScheduleExecutionResult:
		captured.append(schedule_id)
		return ScheduleExecutionResult(schedule_id, "completed", 42, "Watchlist update completed in run 42.")

	monkeypatch.setattr("app.routes.run_schedule_now", fake_run_now)
	response = client.post(f"/scheduler/{schedule.id}/run", follow_redirects=True)

	assert captured == [schedule.id]
	assert "Watchlist update completed in run 42." in response.get_data(as_text=True)


@pytest.mark.parametrize("action", ["toggle", "run", "delete"])
def test_schedule_actions_do_not_support_get(client, action: str) -> None:
	schedule = create_daily_schedule("Morning", "09:00")

	assert client.get(f"/scheduler/{schedule.id}/{action}").status_code == 405


def test_scheduler_navigation_is_active_on_scheduler_page(client) -> None:
	body = client.get("/scheduler").get_data(as_text=True)

	assert 'class="sidebar-link is-active" href="/scheduler">Scheduler</a>' in body


def test_scraping_control_renders_compact_scheduler_summary(client) -> None:
	create_daily_schedule("Morning", "09:00")

	response = client.get("/scraping")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Automatic Updates" in body
	assert "Manage Scheduler" in body
	assert "Enabled" in body


def test_scheduler_page_is_available_after_full_data_reset(client) -> None:
	create_daily_schedule("Morning", "09:00")
	client.post("/settings/data/reset", data={"confirmation": "RESET"})

	response = client.get("/scheduler")

	assert response.status_code == 200
	assert "Morning" in response.get_data(as_text=True)
