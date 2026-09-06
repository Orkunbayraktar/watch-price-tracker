"""Geliştirme sırasında uygulamayı çalıştırmak için kullanılacak giriş dosyası."""

import socket

from app import create_app
from database.db import initialize_database
from services.scheduler_service import shutdown_scheduler, start_scheduler


LOCAL_HOST = "127.0.0.1"
FIRST_PORT = 5000
LAST_PORT = 5010


def find_available_port() -> int:
    """Return the first bindable localhost port in the development range."""
    for port in range(FIRST_PORT, LAST_PORT + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            try:
                candidate.bind((LOCAL_HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f"No available development server port on {LOCAL_HOST} "
        f"in range {FIRST_PORT}-{LAST_PORT}."
    )


def main() -> None:
    """Start the local Flask development server."""
    port = find_available_port()
    app = create_app()
    initialize_database(app)
    start_scheduler(app)
    try:
        # A single serving process prevents Werkzeug's reloader from duplicating jobs.
        app.run(host=LOCAL_HOST, port=port, debug=True, use_reloader=False)
    finally:
        shutdown_scheduler(wait=False)


if __name__ == "__main__":
    main()
