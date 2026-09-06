"""Desktop launcher lifecycle, localhost, readiness, and single-instance tests."""

from __future__ import annotations

import json
from pathlib import Path
import socket
from threading import Event
from typing import Callable
from urllib.error import URLError

from flask import Flask
import pytest

import launcher
from app import create_app
from app import resource_paths
from database.db import db, initialize_database
from database.models import AppSetting


class FakeSocket:
	def __init__(self, selected_port: int = 61234, *, bind_error: bool = False) -> None:
		self.selected_port = selected_port
		self.bind_error = bind_error
		self.bind_calls: list[tuple[str, int]] = []
		self.close_count = 0

	def bind(self, address: tuple[str, int]) -> None:
		self.bind_calls.append(address)
		if self.bind_error:
			raise OSError("occupied")

	def getsockname(self) -> tuple[str, int]:
		return launcher.LOCAL_HOST, self.selected_port

	def close(self) -> None:
		self.close_count += 1


class FakeManager:
	def __init__(self, runtime_directory: Path, *, acquired: bool, state: launcher.InstanceState | None = None) -> None:
		self.runtime_directory = runtime_directory
		self.acquired = acquired
		self.state = state
		self.acquire_count = 0
		self.clear_count = 0
		self.release_count = 0
		self.written_states: list[launcher.InstanceState] = []

	def acquire(self) -> bool:
		self.acquire_count += 1
		return self.acquired

	def read_state(self) -> launcher.InstanceState | None:
		return self.state

	def write_state(self, state: launcher.InstanceState) -> None:
		self.state = state
		self.written_states.append(state)

	def clear_state(self) -> None:
		self.clear_count += 1

	def release(self) -> None:
		self.release_count += 1


class FakeServer:
	def __init__(self, finish_event: Event, *, interrupt: bool = False) -> None:
		self.finish_event = finish_event
		self.interrupt = interrupt
		self.run_count = 0
		self.close_count = 0
		self.closed = False

	def run(self) -> None:
		self.run_count += 1
		if self.interrupt:
			raise KeyboardInterrupt
		assert self.finish_event.wait(1), "launcher worker did not finish"

	def close(self) -> None:
		if self.closed:
			return
		self.closed = True
		self.close_count += 1
		self.finish_event.set()


class FakeResponse:
	def __init__(self, payload: dict[str, str], status: int = 200) -> None:
		self.payload = payload
		self.status = status

	def __enter__(self):
		return self

	def __exit__(self, *args) -> None:
		return None

	def read(self) -> bytes:
		return json.dumps(self.payload).encode("utf-8")


def make_app(**overrides: object) -> Flask:
	app = Flask(__name__)
	app.config.update(
		DESKTOP_PORT=5000,
		DESKTOP_READY_TIMEOUT=0.1,
		DESKTOP_READY_INTERVAL=0.001,
		DESKTOP_SERVER_THREADS=4,
	)
	app.config.update(overrides)
	return app


def run_first_instance(
	tmp_path: Path,
	*,
	readiness: Callable[[str, float, float, Event | None], bool] | None = None,
	server: FakeServer | None = None,
	browser: Callable[..., object] | None = None,
	app: Flask | None = None,
	manager: FakeManager | launcher.InstanceManager | None = None,
	port: int = 61234,
	database_initializer: Callable[[Flask], None] | None = None,
	scheduler_starter: Callable[[Flask], object] | None = None,
	scheduler_stopper: Callable[..., None] | None = None,
) -> tuple[int, FakeServer, FakeManager | launcher.InstanceManager]:
	finish_event = Event()
	fake_server = server or FakeServer(finish_event)
	fake_manager = manager or FakeManager(tmp_path, acquired=True)
	ready = readiness or (lambda url, timeout, interval, stop: True)
	browser_opener = browser or (lambda url, **kwargs: finish_event.set())
	reservation = launcher.PortReservation(FakeSocket(port), port)
	result = launcher.run_launcher(
		app_factory=lambda: app or make_app(),
		database_initializer=database_initializer or (lambda flask_app: None),
		scheduler_starter=scheduler_starter or (lambda flask_app: object()),
		scheduler_stopper=scheduler_stopper or (lambda **kwargs: None),
		server_factory=lambda flask_app, reserved, **kwargs: fake_server,
		browser_opener=browser_opener,
		readiness_waiter=ready,
		instance_manager=fake_manager,
		port_reserver=lambda preferred: reservation,
		configure_logs=False,
	)
	return result, fake_server, fake_manager


def test_runtime_directory_uses_local_app_data(tmp_path: Path) -> None:
	assert launcher.get_runtime_directory({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "WatchPriceTracker" / "runtime"


def test_runtime_directory_uses_windows_home_fallback(tmp_path: Path) -> None:
	assert launcher.get_runtime_directory({}, home=tmp_path) == tmp_path / "AppData" / "Local" / "WatchPriceTracker" / "runtime"


def test_log_directory_uses_separate_user_data_folder(tmp_path: Path) -> None:
	assert resource_paths.get_log_directory({"LOCALAPPDATA": str(tmp_path)}) == tmp_path / "WatchPriceTracker" / "logs"


def test_source_database_path_remains_in_repository_data() -> None:
	assert resource_paths.get_database_path(frozen=False) == resource_paths.get_source_root() / "data" / "watch_tracker.db"


def test_source_template_and_static_paths_exist() -> None:
	assert resource_paths.get_template_directory().is_dir()
	assert resource_paths.get_static_directory().is_dir()
	app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
	assert Path(app.template_folder) == resource_paths.get_source_root() / "templates"
	assert Path(app.static_folder) == resource_paths.get_source_root() / "static"


@pytest.mark.parametrize(
	("asset_path", "content_types"),
	[
		("css/style.css", ("text/css",)),
		("js/main.js", ("text/javascript", "application/javascript")),
	],
)
def test_source_app_serves_known_static_assets(asset_path: str, content_types: tuple[str, ...]) -> None:
	app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
	response = app.test_client().get(f"/static/{asset_path}")
	assert response.status_code == 200
	assert response.mimetype in content_types
	assert response.data


def test_frozen_database_path_uses_writable_user_data(tmp_path: Path) -> None:
	path = resource_paths.get_database_path(frozen=True, environ={"LOCALAPPDATA": str(tmp_path)})
	assert path == tmp_path / "WatchPriceTracker" / "data" / "watch_tracker.db"


def test_frozen_resource_root_uses_centralized_bundle_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(resource_paths.sys, "frozen", True, raising=False)
	monkeypatch.setattr(resource_paths.sys, "_MEIPASS", str(tmp_path), raising=False)
	assert resource_paths.get_resource_root() == tmp_path


def test_frozen_app_resolves_bundled_templates_and_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	(tmp_path / "templates").mkdir()
	(tmp_path / "static" / "css").mkdir(parents=True)
	(tmp_path / "static" / "js").mkdir()
	(tmp_path / "static" / "css" / "style.css").write_text("body { color: black; }", encoding="utf-8")
	(tmp_path / "static" / "js" / "main.js").write_text("window.frozenAssetLoaded = true;", encoding="utf-8")
	monkeypatch.setattr(resource_paths.sys, "frozen", True, raising=False)
	monkeypatch.setattr(resource_paths.sys, "_MEIPASS", str(tmp_path), raising=False)
	app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
	assert Path(app.template_folder) == tmp_path / "templates"
	assert Path(app.static_folder) == tmp_path / "static"
	assert app.test_client().get("/static/css/style.css").status_code == 200
	assert app.test_client().get("/static/js/main.js").status_code == 200


def test_frozen_resource_root_falls_back_to_executable_internal_directory(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	bundle_root = tmp_path / "reported-bundle"
	bundle_root.mkdir()
	executable_directory = tmp_path / "portable-app"
	internal_directory = executable_directory / "_internal"
	(internal_directory / "templates").mkdir(parents=True)
	(internal_directory / "static").mkdir()
	monkeypatch.setattr(resource_paths.sys, "frozen", True, raising=False)
	monkeypatch.setattr(resource_paths.sys, "_MEIPASS", str(bundle_root), raising=False)
	monkeypatch.setattr(resource_paths.sys, "executable", str(executable_directory / "WatchPriceTracker.exe"))

	assert resource_paths.get_resource_root() == internal_directory
	assert resource_paths.resource_path("static") == internal_directory / "static"


@pytest.mark.parametrize("value", ["../static", Path("C:/absolute/static")])
def test_resource_path_rejects_unsafe_values(value: str | Path) -> None:
	with pytest.raises(ValueError):
		resource_paths.resource_path(value)


def test_frozen_playwright_path_points_to_bundled_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(resource_paths.sys, "frozen", True, raising=False)
	monkeypatch.setattr(resource_paths.sys, "_MEIPASS", str(tmp_path), raising=False)
	environ: dict[str, str] = {}
	path = resource_paths.configure_playwright_environment(environ)
	assert path == tmp_path / "playwright" / "driver" / "package" / ".local-browsers"
	assert environ["PLAYWRIGHT_BROWSERS_PATH"] == str(path)


def test_source_playwright_environment_is_unchanged() -> None:
	environ = {"PLAYWRIGHT_BROWSERS_PATH": "developer-cache"}
	assert resource_paths.configure_playwright_environment(environ, frozen=False) is None
	assert environ["PLAYWRIGHT_BROWSERS_PATH"] == "developer-cache"


@pytest.mark.parametrize(
	"value",
	[
		None,
		{},
		{"pid": 0, "host": launcher.LOCAL_HOST, "port": 5000},
		{"pid": 1, "host": "0.0.0.0", "port": 5000},
		{"pid": 1, "host": launcher.LOCAL_HOST, "port": 0},
		{"pid": 1, "host": launcher.LOCAL_HOST, "port": 70000},
	],
)
def test_instance_state_rejects_invalid_or_nonlocal_values(value: object) -> None:
	assert launcher.InstanceState.from_dict(value) is None


def test_instance_state_builds_exact_urls() -> None:
	state = launcher.InstanceState(pid=10, host=launcher.LOCAL_HOST, port=54321)
	assert state.dashboard_url == "http://127.0.0.1:54321/"
	assert state.health_url == "http://127.0.0.1:54321/health"


def test_instance_manager_writes_reads_and_clears_state(tmp_path: Path) -> None:
	manager = launcher.InstanceManager(tmp_path)
	assert manager.acquire() is True
	state = launcher.InstanceState(pid=10, host=launcher.LOCAL_HOST, port=5000)
	manager.write_state(state)
	assert manager.read_state() == state
	manager.clear_state()
	assert manager.read_state() is None
	manager.release()


def test_released_lock_can_be_reacquired_and_stale_state_recovered(tmp_path: Path) -> None:
	state_path = tmp_path / "instance.json"
	state_path.write_text('{"pid": 999999, "host": "127.0.0.1", "port": 5000}', encoding="utf-8")
	first = launcher.InstanceManager(tmp_path)
	assert first.acquire() is True
	first.clear_state()
	first.release()
	second = launcher.InstanceManager(tmp_path)
	assert second.acquire() is True
	assert second.read_state() is None
	second.release()


def test_reserve_local_port_binds_only_loopback() -> None:
	fake_socket = FakeSocket()
	reservation = launcher.reserve_local_port(5000, socket_factory=lambda *args: fake_socket)
	assert fake_socket.bind_calls == [("127.0.0.1", 5000)]
	assert reservation.port == 61234
	reservation.close()


def test_reserve_local_port_falls_back_when_preferred_is_occupied() -> None:
	occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	occupied.bind((launcher.LOCAL_HOST, 0))
	occupied_port = occupied.getsockname()[1]
	try:
		reservation = launcher.reserve_local_port(occupied_port)
		assert reservation.port != occupied_port
		assert reservation.port > 0
		reservation.close()
	finally:
		occupied.close()


def test_create_waitress_server_uses_reserved_socket_and_threads(monkeypatch: pytest.MonkeyPatch) -> None:
	captured: dict[str, object] = {}

	class Dispatcher:
		def shutdown(self, **kwargs) -> None:
			captured["shutdown"] = kwargs

	class RawServer:
		task_dispatcher = Dispatcher()

		def run(self) -> None:
			pass

		def close(self) -> None:
			captured["closed"] = True

	def fake_create_server(app, **kwargs):
		captured.update(kwargs)
		return RawServer()

	monkeypatch.setattr(launcher, "create_server", fake_create_server)
	reservation = launcher.PortReservation(FakeSocket(), 61234)
	server = launcher.create_waitress_server(make_app(), reservation, threads=3)
	assert captured["sockets"] == [reservation.socket]
	assert captured["threads"] == 3
	assert "host" not in captured
	server.close()
	server.close()
	assert captured["closed"] is True
	assert captured["shutdown"] == {"cancel_pending": True}


def test_waitress_server_closes_keep_alive_channels_once() -> None:
	class Channel:
		def __init__(self) -> None:
			self.close_count = 0

		def close(self) -> None:
			self.close_count += 1

	class Dispatcher:
		def __init__(self) -> None:
			self.shutdown_count = 0

		def shutdown(self, **kwargs) -> None:
			assert kwargs == {"cancel_pending": True}
			self.shutdown_count += 1

	class RawServer:
		def __init__(self) -> None:
			self.close_count = 0
			self.channel = Channel()
			self.task_dispatcher = Dispatcher()
			self._map = {1: self, 2: self.channel}

		def close(self) -> None:
			self.close_count += 1

	raw_server = RawServer()
	server = launcher.WaitressServer(raw_server)
	server.close()
	server.close()

	assert raw_server.close_count == 1
	assert raw_server.channel.close_count == 1
	assert raw_server.task_dispatcher.shutdown_count == 1


def test_probe_server_accepts_only_own_health_payload(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(
		launcher,
		"urlopen",
		lambda request, timeout: FakeResponse({"application": "watch-price-tracker", "status": "ok"}),
	)
	assert launcher.probe_server("http://127.0.0.1:5000/health") is True


def test_probe_server_rejects_an_unrelated_local_service(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(
		launcher,
		"urlopen",
		lambda request, timeout: FakeResponse({"application": "something-else", "status": "ok"}),
	)
	assert launcher.probe_server("http://127.0.0.1:5000/health") is False


def test_probe_server_handles_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
	def fail(request, timeout):
		raise URLError("not ready")

	monkeypatch.setattr(launcher, "urlopen", fail)
	assert launcher.probe_server("http://127.0.0.1:5000/health") is False


def test_wait_for_server_retries_until_ready() -> None:
	results = iter([False, False, True])
	now = [0.0]

	def clock() -> float:
		return now[0]

	def sleep(interval: float) -> None:
		now[0] += interval

	assert launcher.wait_for_server("health", 1.0, 0.1, probe=lambda url: next(results), clock=clock, sleeper=sleep)


def test_wait_for_server_has_bounded_timeout() -> None:
	now = [0.0]

	def clock() -> float:
		return now[0]

	def sleep(interval: float) -> None:
		now[0] += interval

	assert not launcher.wait_for_server("health", 0.3, 0.1, probe=lambda url: False, clock=clock, sleeper=sleep)
	assert now[0] == pytest.approx(0.3)


def test_health_endpoint_returns_minimal_identity() -> None:
	app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
	response = app.test_client().get("/health")
	assert response.status_code == 200
	assert response.get_json() == {"application": "watch-price-tracker", "status": "ok"}


def test_launcher_initializes_app_database_scheduler_and_server_once(tmp_path: Path) -> None:
	calls: list[str] = []
	app = make_app()
	app.run = lambda *args, **kwargs: pytest.fail("Flask development server must not run")
	finish = Event()
	server = FakeServer(finish)

	def browser(url: str, **kwargs) -> None:
		calls.append("browser")
		finish.set()

	result, fake_server, _ = run_first_instance(
		tmp_path,
		app=app,
		server=server,
		browser=browser,
		database_initializer=lambda flask_app: calls.append("database"),
		scheduler_starter=lambda flask_app: calls.append("scheduler"),
		scheduler_stopper=lambda **kwargs: calls.append("stop-scheduler"),
	)
	assert result == 0
	assert app.debug is False
	assert calls == ["database", "scheduler", "browser", "stop-scheduler"]
	assert fake_server.run_count == 1
	assert fake_server.close_count == 1


def test_browser_opens_only_after_readiness_with_dynamic_port(tmp_path: Path) -> None:
	events: list[str] = []
	finish = Event()

	def readiness(url: str, timeout: float, interval: float, stop: Event | None) -> bool:
		events.append(f"ready:{url}")
		return True

	def browser(url: str, **kwargs) -> None:
		events.append(f"browser:{url}")
		finish.set()

	result, _, manager = run_first_instance(
		tmp_path,
		readiness=readiness,
		server=FakeServer(finish),
		browser=browser,
		port=63421,
	)
	assert result == 0
	assert events == ["ready:http://127.0.0.1:63421/health", "browser:http://127.0.0.1:63421/"]
	assert manager.written_states[0].port == 63421


def test_readiness_timeout_stops_server_without_opening_browser(tmp_path: Path) -> None:
	finish = Event()
	browser_calls: list[str] = []
	result, server, _ = run_first_instance(
		tmp_path,
		readiness=lambda url, timeout, interval, stop: False,
		server=FakeServer(finish),
		browser=lambda url, **kwargs: browser_calls.append(url),
	)
	assert result == 1
	assert browser_calls == []
	assert server.close_count == 1


def test_second_instance_opens_existing_url_without_starting_services(tmp_path: Path) -> None:
	state = launcher.InstanceState(pid=42, host=launcher.LOCAL_HOST, port=60123)
	manager = FakeManager(tmp_path, acquired=False, state=state)
	browser_calls: list[str] = []

	def must_not_run(*args, **kwargs):
		pytest.fail("second instance started application services")

	result = launcher.run_launcher(
		app_factory=must_not_run,
		database_initializer=must_not_run,
		scheduler_starter=must_not_run,
		server_factory=must_not_run,
		browser_opener=lambda url, **kwargs: browser_calls.append(url),
		readiness_waiter=lambda url, timeout, interval, stop: True,
		instance_manager=manager,
		configure_logs=False,
	)
	assert result == 0
	assert browser_calls == ["http://127.0.0.1:60123/"]
	assert manager.release_count == 1


def test_unreachable_existing_instance_does_not_open_browser(tmp_path: Path) -> None:
	state = launcher.InstanceState(pid=42, host=launcher.LOCAL_HOST, port=60123)
	manager = FakeManager(tmp_path, acquired=False, state=state)
	browser_calls: list[str] = []
	result = launcher.run_launcher(
		browser_opener=lambda url, **kwargs: browser_calls.append(url),
		readiness_waiter=lambda url, timeout, interval, stop: False,
		instance_manager=manager,
		configure_logs=False,
	)
	assert result == 1
	assert browser_calls == []


def test_instance_that_exits_during_detection_is_recovered(tmp_path: Path) -> None:
	state = launcher.InstanceState(pid=42, host=launcher.LOCAL_HOST, port=60123)

	class RecoveringManager(FakeManager):
		def __init__(self) -> None:
			super().__init__(tmp_path, acquired=False, state=state)
			self.acquire_results = iter([False, True])

		def acquire(self) -> bool:
			self.acquire_count += 1
			return next(self.acquire_results)

	manager = RecoveringManager()
	readiness_results = iter([False, True])
	result, server, _ = run_first_instance(
		tmp_path,
		manager=manager,
		readiness=lambda url, timeout, interval, stop: next(readiness_results),
	)
	assert result == 0
	assert manager.acquire_count == 2
	assert server.run_count == 1
	assert manager.written_states[0].port == 61234


def test_keyboard_interrupt_is_a_clean_shutdown(tmp_path: Path) -> None:
	server = FakeServer(Event(), interrupt=True)
	result, _, manager = run_first_instance(tmp_path, server=server)
	assert result == 0
	assert server.close_count == 1
	assert manager.clear_count == 2
	assert manager.release_count == 1


def test_in_app_shutdown_callback_stops_server_scheduler_and_runtime_state(tmp_path: Path) -> None:
	finish = Event()
	app = make_app()
	manager = FakeManager(tmp_path, acquired=True)
	stop_calls: list[bool] = []

	class ShutdownServer(FakeServer):
		def run(self) -> None:
			self.run_count += 1
			assert app.config["DESKTOP_SHUTDOWN_ENABLED"] is True
			assert app.config["DESKTOP_SHUTDOWN_TOKEN"]
			app.config["DESKTOP_SHUTDOWN_CALLBACK"]()
			assert self.finish_event.wait(1), "in-app shutdown did not stop the server"

	server = ShutdownServer(finish)
	result = launcher.run_launcher(
		app_factory=lambda: app,
		database_initializer=lambda flask_app: None,
		scheduler_starter=lambda flask_app: object(),
		scheduler_stopper=lambda *, wait: stop_calls.append(wait),
		server_factory=lambda flask_app, reserved, **kwargs: server,
		browser_opener=lambda url, **kwargs: None,
		readiness_waiter=lambda url, timeout, interval, stop: True,
		instance_manager=manager,
		port_reserver=lambda preferred: launcher.PortReservation(FakeSocket(), 61234),
		configure_logs=False,
	)
	assert result == 0
	assert server.close_count == 1
	assert stop_calls == [False]
	assert manager.clear_count == 2
	assert manager.release_count == 1


def test_startup_failure_cleans_state_and_scheduler(tmp_path: Path) -> None:
	manager = FakeManager(tmp_path, acquired=True)
	stop_calls: list[bool] = []
	result = launcher.run_launcher(
		app_factory=make_app,
		database_initializer=lambda app: None,
		scheduler_starter=lambda app: (_ for _ in ()).throw(RuntimeError("scheduler failed")),
		scheduler_stopper=lambda *, wait: stop_calls.append(wait),
		instance_manager=manager,
		configure_logs=False,
	)
	assert result == 1
	assert stop_calls == [False]
	assert manager.clear_count == 2
	assert manager.release_count == 1


def test_logging_setup_failure_returns_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	manager = FakeManager(tmp_path, acquired=True)
	monkeypatch.setattr(launcher, "configure_logging", lambda path: (_ for _ in ()).throw(PermissionError("denied")))
	assert launcher.run_launcher(instance_manager=manager) == 1
	assert manager.acquire_count == 0
	assert manager.release_count == 1


def test_database_initialization_preserves_existing_rows(tmp_path: Path) -> None:
	database_path = tmp_path / "launcher.db"
	app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path.as_posix()}"})
	initialize_database(app)
	with app.app_context():
		db.session.add(AppSetting(key="sentinel", value="kept"))
		db.session.commit()
	initialize_database(app)
	with app.app_context():
		assert db.session.get(AppSetting, "sentinel").value == "kept"
		db.session.remove()
		db.engine.dispose()


def test_launcher_startup_does_not_invoke_playwright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from scrapers.fetchers.playwright_fetcher import PlaywrightFetcher

	def fail_if_fetched(*args, **kwargs):
		pytest.fail("Playwright was started during launcher startup")

	monkeypatch.setattr(PlaywrightFetcher, "fetch", fail_if_fetched)
	result, _, _ = run_first_instance(tmp_path)
	assert result == 0
