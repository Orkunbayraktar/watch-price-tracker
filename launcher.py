"""Desktop-style entry point for the local Watch Price Tracker application."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import sys
from threading import Event, Lock, Thread
import time
from types import TracebackType
from typing import BinaryIO, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import webbrowser

from flask import Flask
from waitress.server import create_server

from app import create_app
from app.resource_paths import configure_playwright_environment, get_log_directory, get_runtime_directory
from database.db import initialize_database
from services.scheduler_service import shutdown_scheduler, start_scheduler


APPLICATION_ID = "watch-price-tracker"
LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DEFAULT_READY_TIMEOUT = 15.0
DEFAULT_READY_INTERVAL = 0.1
DEFAULT_SERVER_THREADS = 4
SHUTDOWN_RESPONSE_GRACE_SECONDS = 0.5

logger = logging.getLogger(__name__)


class Server(Protocol):
	"""Small server interface used by the launcher and its tests."""

	def run(self) -> None: ...

	def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class InstanceState:
	"""Non-sensitive connection details shared with a second launcher process."""

	pid: int
	host: str
	port: int

	@property
	def dashboard_url(self) -> str:
		return f"http://{self.host}:{self.port}/"

	@property
	def health_url(self) -> str:
		return f"http://{self.host}:{self.port}/health"

	def to_dict(self) -> dict[str, int | str]:
		return {"pid": self.pid, "host": self.host, "port": self.port}

	@classmethod
	def from_dict(cls, value: object) -> InstanceState | None:
		if not isinstance(value, dict):
			return None
		try:
			pid = int(value["pid"])
			port = int(value["port"])
		except (KeyError, TypeError, ValueError):
			return None
		host = value.get("host")
		if pid <= 0 or host != LOCAL_HOST or not 1 <= port <= 65535:
			return None
		return cls(pid=pid, host=host, port=port)


class InstanceManager:
	"""Own an OS-backed process lock and its localhost connection state."""

	def __init__(self, runtime_directory: Path) -> None:
		self.runtime_directory = runtime_directory
		self.lock_path = runtime_directory / "instance.lock"
		self.state_path = runtime_directory / "instance.json"
		self._lock_handle: BinaryIO | None = None
		self._owns_lock = False

	def acquire(self) -> bool:
		self.runtime_directory.mkdir(parents=True, exist_ok=True)
		handle = self.lock_path.open("a+b")
		if self.lock_path.stat().st_size == 0:
			handle.write(b"\0")
			handle.flush()
		handle.seek(0)
		if not _try_lock(handle):
			handle.close()
			return False
		self._lock_handle = handle
		self._owns_lock = True
		return True

	def write_state(self, state: InstanceState) -> None:
		if not self._owns_lock:
			raise RuntimeError("Instance state can only be written by the lock owner.")
		self.runtime_directory.mkdir(parents=True, exist_ok=True)
		temporary_path = self.state_path.with_suffix(".tmp")
		temporary_path.write_text(json.dumps(state.to_dict()), encoding="utf-8")
		temporary_path.replace(self.state_path)

	def read_state(self) -> InstanceState | None:
		try:
			value = json.loads(self.state_path.read_text(encoding="utf-8"))
		except (FileNotFoundError, OSError, json.JSONDecodeError):
			return None
		return InstanceState.from_dict(value)

	def clear_state(self) -> None:
		if not self._owns_lock:
			return
		try:
			self.state_path.unlink()
		except FileNotFoundError:
			pass

	def release(self) -> None:
		handle = self._lock_handle
		self._lock_handle = None
		if handle is None:
			return
		try:
			if self._owns_lock:
				_unlock(handle)
		finally:
			self._owns_lock = False
			handle.close()

	def __enter__(self) -> InstanceManager:
		return self

	def __exit__(
		self,
		exception_type: type[BaseException] | None,
		exception: BaseException | None,
		traceback: TracebackType | None,
	) -> None:
		self.release()


@dataclass(slots=True)
class PortReservation:
	"""A bound localhost socket that removes the free-port selection race."""

	socket: socket.socket
	port: int

	def close(self) -> None:
		self.socket.close()


class WaitressServer:
	"""Idempotent lifecycle adapter around Waitress' controllable server API."""

	def __init__(self, raw_server: object) -> None:
		self._server = raw_server
		self._close_lock = Lock()
		self._closed = False

	def run(self) -> None:
		self._server.run()

	def close(self) -> None:
		with self._close_lock:
			if self._closed:
				return
			self._closed = True
		try:
			self._server.close()
			self._close_open_channels()
		finally:
			dispatcher = getattr(self._server, "task_dispatcher", None)
			if dispatcher is not None:
				dispatcher.shutdown(cancel_pending=True)

	def _close_open_channels(self) -> None:
		"""Close keep-alive channels so the async loop can finish promptly."""
		socket_map = getattr(self._server, "_map", None)
		if not isinstance(socket_map, dict):
			return
		for channel in list(socket_map.values()):
			if channel is self._server:
				continue
			close_channel = getattr(channel, "close", None)
			if not callable(close_channel):
				continue
			try:
				close_channel()
			except Exception:
				logger.exception("Waitress connection cleanup failed")


def configure_logging(runtime_directory: Path) -> Path:
	"""Configure concise console and rotating per-user file logging once."""
	runtime_directory.mkdir(parents=True, exist_ok=True)
	log_path = runtime_directory / "launcher.log"
	root_logger = logging.getLogger()
	root_logger.setLevel(logging.INFO)
	if not any(getattr(handler, "_watch_price_tracker", False) for handler in root_logger.handlers):
		formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
		file_handler = RotatingFileHandler(
			log_path,
			maxBytes=1_000_000,
			backupCount=2,
			encoding="utf-8",
		)
		file_handler.setFormatter(formatter)
		file_handler._watch_price_tracker = True
		root_logger.addHandler(file_handler)
		if sys.stderr is not None and not any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers):
			stream_handler = logging.StreamHandler()
			stream_handler.setFormatter(formatter)
			stream_handler._watch_price_tracker = True
			root_logger.addHandler(stream_handler)
	return log_path


def reserve_local_port(
	preferred_port: int,
	*,
	socket_factory: Callable[..., socket.socket] = socket.socket,
) -> PortReservation:
	"""Bind the preferred localhost port, then atomically fall back to an OS port."""
	for port in (preferred_port, 0):
		candidate = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
		try:
			candidate.bind((LOCAL_HOST, port))
		except OSError:
			candidate.close()
			if port == preferred_port:
				logger.warning("Port %s is unavailable; selecting a dynamic localhost port", preferred_port)
				continue
			raise
		selected_port = int(candidate.getsockname()[1])
		return PortReservation(candidate, selected_port)
	raise RuntimeError("No local port could be reserved.")


def create_waitress_server(
	app: Flask,
	reservation: PortReservation,
	*,
	threads: int,
) -> Server:
	"""Transfer the pre-bound local socket to a production WSGI server."""
	try:
		raw_server = create_server(
			app,
			sockets=[reservation.socket],
			threads=threads,
			ident="Watch Price Tracker",
		)
	except Exception:
		reservation.close()
		raise
	return WaitressServer(raw_server)


def probe_server(url: str, *, request_timeout: float = 0.75) -> bool:
	"""Return whether URL is this application's minimal health endpoint."""
	request = Request(url, headers={"User-Agent": "WatchPriceTracker-Launcher/1.0"})
	try:
		with urlopen(request, timeout=request_timeout) as response:
			if response.status != 200:
				return False
			payload = json.loads(response.read().decode("utf-8"))
	except (HTTPError, URLError, OSError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
		return False
	return payload.get("status") == "ok" and payload.get("application") == APPLICATION_ID


def wait_for_server(
	url: str,
	timeout: float,
	interval: float,
	stop_event: Event | None = None,
	*,
	probe: Callable[[str], bool] = probe_server,
	clock: Callable[[], float] = time.monotonic,
	sleeper: Callable[[float], None] = time.sleep,
) -> bool:
	"""Poll a health endpoint until it responds, cancellation occurs, or time expires."""
	deadline = clock() + timeout
	while clock() < deadline:
		if stop_event is not None and stop_event.is_set():
			return False
		if probe(url):
			return True
		if stop_event is not None:
			if stop_event.wait(interval):
				return False
		else:
			sleeper(interval)
	return False


def wait_for_instance_state(
	manager: InstanceManager,
	timeout: float,
	interval: float,
	*,
	clock: Callable[[], float] = time.monotonic,
	sleeper: Callable[[float], None] = time.sleep,
) -> InstanceState | None:
	"""Wait briefly for a first launcher process to publish its selected port."""
	deadline = clock() + timeout
	while clock() < deadline:
		state = manager.read_state()
		if state is not None:
			return state
		sleeper(interval)
	return None


def run_launcher(
	*,
	app_factory: Callable[[], Flask] | None = None,
	database_initializer: Callable[[Flask], None] | None = None,
	scheduler_starter: Callable[[Flask], object] | None = None,
	scheduler_stopper: Callable[..., None] | None = None,
	server_factory: Callable[..., Server] | None = None,
	browser_opener: Callable[..., object] | None = None,
	readiness_waiter: Callable[[str, float, float, Event | None], bool] | None = None,
	instance_manager: InstanceManager | None = None,
	port_reserver: Callable[[int], PortReservation] | None = None,
	log_directory: Path | None = None,
	configure_logs: bool = True,
) -> int:
	"""Run one desktop-local application instance and return a process exit code."""
	configure_playwright_environment()
	app_factory = app_factory or create_app
	database_initializer = database_initializer or initialize_database
	scheduler_starter = scheduler_starter or start_scheduler
	scheduler_stopper = scheduler_stopper or shutdown_scheduler
	server_factory = server_factory or create_waitress_server
	browser_opener = browser_opener or webbrowser.open
	readiness_waiter = readiness_waiter or wait_for_server
	port_reserver = port_reserver or reserve_local_port
	manager = instance_manager or InstanceManager(get_runtime_directory())
	launcher_log_directory = log_directory or get_log_directory()
	log_path = launcher_log_directory / "launcher.log"
	server: Server | None = None
	scheduler_attempted = False
	stop_event = Event()
	shutdown_request_event = Event()
	ready_event = Event()
	readiness_failed = Event()
	browser_thread: Thread | None = None
	shutdown_monitor_thread: Thread | None = None

	try:
		if configure_logs:
			log_path = configure_logging(launcher_log_directory)
		logger.info("Desktop launcher started")
		if not manager.acquire():
			logger.info("Existing application instance detected")
			state = wait_for_instance_state(manager, DEFAULT_READY_TIMEOUT, DEFAULT_READY_INTERVAL)
			if state is not None and readiness_waiter(
				state.health_url,
				DEFAULT_READY_TIMEOUT,
				DEFAULT_READY_INTERVAL,
				None,
			):
				browser_opener(state.dashboard_url, new=2)
				logger.info("Existing dashboard opened at %s", state.dashboard_url)
				return 0
			# The first process may have exited during the bounded state/health wait.
			if not manager.acquire():
				if state is None:
					logger.error("Existing instance did not publish valid connection state")
				else:
					logger.error("Existing instance did not become reachable at %s", state.health_url)
				return 1
			logger.warning("Previous instance exited during detection; recovering its stale state")

		# An OS lock cannot remain held by a dead process. Any old state is stale.
		manager.clear_state()
		app = app_factory()
		app.debug = False

		def request_application_shutdown() -> None:
			logger.info("In-app shutdown requested")
			shutdown_request_event.set()

		app.config.update(
			DESKTOP_MODE=True,
			DESKTOP_SHUTDOWN_ENABLED=True,
			DESKTOP_SHUTDOWN_TOKEN=secrets.token_urlsafe(32),
			DESKTOP_SHUTDOWN_CALLBACK=request_application_shutdown,
		)
		database_initializer(app)
		logger.info("Database initialized")
		scheduler_attempted = True
		scheduler_starter(app)
		logger.info("Scheduler initialized")

		preferred_port = _validated_port(app.config.get("DESKTOP_PORT", DEFAULT_PORT))
		ready_timeout = _positive_float(app.config.get("DESKTOP_READY_TIMEOUT", DEFAULT_READY_TIMEOUT), DEFAULT_READY_TIMEOUT)
		ready_interval = _positive_float(
			app.config.get("DESKTOP_READY_INTERVAL", DEFAULT_READY_INTERVAL),
			DEFAULT_READY_INTERVAL,
		)
		threads = max(1, int(app.config.get("DESKTOP_SERVER_THREADS", DEFAULT_SERVER_THREADS)))
		reservation = port_reserver(preferred_port)
		try:
			server = server_factory(app, reservation, threads=threads)
		except Exception:
			reservation.close()
			raise

		state = InstanceState(pid=os.getpid(), host=LOCAL_HOST, port=reservation.port)
		manager.write_state(state)
		logger.info("Selected localhost port %s", state.port)

		def stop_server_when_requested() -> None:
			shutdown_request_event.wait()
			if stop_event.wait(SHUTDOWN_RESPONSE_GRACE_SECONDS):
				return
			logger.info("Graceful in-app shutdown starting")
			server.close()

		def open_browser_when_ready() -> None:
			try:
				is_ready = readiness_waiter(
					state.health_url,
					ready_timeout,
					ready_interval,
					stop_event,
				)
			except Exception:
				logger.exception("Server readiness check failed")
				is_ready = False
			if not is_ready:
				if not stop_event.is_set():
					readiness_failed.set()
					logger.error("Server readiness timed out at %s", state.health_url)
					server.close()
				return
			ready_event.set()
			logger.info("Server ready at %s", state.dashboard_url)
			try:
				browser_opener(state.dashboard_url, new=2)
				logger.info("Browser opened at %s", state.dashboard_url)
			except Exception:
				logger.exception("Default browser could not be opened")

		shutdown_monitor_thread = Thread(
			target=stop_server_when_requested,
			name="launcher-shutdown",
			daemon=True,
		)
		shutdown_monitor_thread.start()
		browser_thread = Thread(target=open_browser_when_ready, name="launcher-readiness", daemon=True)
		browser_thread.start()
		logger.info("Local server starting on %s:%s", state.host, state.port)
		try:
			server.run()
		except KeyboardInterrupt:
			logger.info("Shutdown requested with Ctrl+C")

		if readiness_failed.is_set() or not ready_event.is_set():
			return 1
		return 0
	except KeyboardInterrupt:
		logger.info("Shutdown requested with Ctrl+C")
		return 0
	except Exception:
		logger.exception("Desktop launcher startup failed. See %s", log_path)
		return 1
	finally:
		logger.info("Shutdown initiated")
		stop_event.set()
		shutdown_request_event.set()
		if server is not None:
			try:
				server.close()
			except Exception:
				logger.exception("Local server cleanup failed")
		if browser_thread is not None:
			browser_thread.join(timeout=1.0)
		if shutdown_monitor_thread is not None:
			shutdown_monitor_thread.join(timeout=1.0)
		if scheduler_attempted:
			try:
				scheduler_stopper(wait=False)
			except Exception:
				logger.exception("Scheduler cleanup failed")
		try:
			manager.clear_state()
		except Exception:
			logger.exception("Runtime state cleanup failed")
		try:
			manager.release()
		except Exception:
			logger.exception("Instance lock release failed")
		logger.info("Launcher shutdown complete")


def _validated_port(value: object) -> int:
	try:
		port = int(value)
	except (TypeError, ValueError):
		return DEFAULT_PORT
	return port if 1 <= port <= 65535 else DEFAULT_PORT


def _positive_float(value: object, default: float) -> float:
	try:
		result = float(value)
	except (TypeError, ValueError):
		return default
	return result if result > 0 else default


def _try_lock(handle: BinaryIO) -> bool:
	if os.name == "nt":
		import msvcrt

		try:
			msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
		except OSError:
			return False
		return True

	import fcntl

	try:
		fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
	except OSError:
		return False
	return True


def _unlock(handle: BinaryIO) -> None:
	handle.seek(0)
	if os.name == "nt":
		import msvcrt

		msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
		return

	import fcntl

	fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> None:
	"""Launch the desktop-style local application process."""
	raise SystemExit(run_launcher())


if __name__ == "__main__":
	main()
