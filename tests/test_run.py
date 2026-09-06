"""Development server port selection and startup tests."""

from unittest.mock import MagicMock, call

import pytest

import run


@pytest.mark.parametrize("unavailable_count", [0, 1, 2, 10, 11])
@pytest.mark.parametrize("error_type", [PermissionError, OSError])
def test_find_available_port(monkeypatch, unavailable_count, error_type):
    sockets = []
    attempts = []

    def socket_factory(family, kind):
        assert (family, kind) == (run.socket.AF_INET, run.socket.SOCK_STREAM)
        candidate = MagicMock()
        candidate.__enter__.return_value = candidate

        def bind(address):
            attempts.append(address)
            if len(attempts) <= unavailable_count:
                raise error_type("Port unavailable")

        candidate.bind.side_effect = bind
        sockets.append(candidate)
        return candidate

    monkeypatch.setattr(run.socket, "socket", socket_factory)

    if unavailable_count == 11:
        with pytest.raises(RuntimeError, match=r"127\.0\.0\.1.*5000-5010"):
            run.find_available_port()
    else:
        assert run.find_available_port() == 5000 + unavailable_count

    assert attempts == [
        ("127.0.0.1", port)
        for port in range(5000, 5000 + min(unavailable_count + 1, 11))
    ]
    for candidate in sockets:
        candidate.__exit__.assert_called_once()


@pytest.mark.parametrize("server_error", [None, RuntimeError("Server failed")])
def test_main_uses_selected_port_and_stops_scheduler(monkeypatch, server_error):
    lifecycle = MagicMock()
    lifecycle.app.run.side_effect = server_error
    monkeypatch.setattr(run, "find_available_port", lambda: 5001)
    monkeypatch.setattr(run, "create_app", lambda: lifecycle.app)
    monkeypatch.setattr(run, "initialize_database", lifecycle.initialize_database)
    monkeypatch.setattr(run, "start_scheduler", lifecycle.start_scheduler)
    monkeypatch.setattr(run, "shutdown_scheduler", lifecycle.shutdown_scheduler)

    if server_error:
        with pytest.raises(RuntimeError, match="Server failed"):
            run.main()
    else:
        run.main()

    assert lifecycle.mock_calls == [
        call.initialize_database(lifecycle.app),
        call.start_scheduler(lifecycle.app),
        call.app.run(host="127.0.0.1", port=5001, debug=True, use_reloader=False),
        call.shutdown_scheduler(wait=False),
    ]
