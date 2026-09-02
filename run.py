"""Geliştirme sırasında uygulamayı çalıştırmak için kullanılacak giriş dosyası."""

from app import create_app
from database.db import initialize_database
from services.scheduler_service import shutdown_scheduler, start_scheduler


def main() -> None:
    """Start the local Flask development server."""
    app = create_app()
    initialize_database(app)
    start_scheduler(app)
    try:
        # A single serving process prevents Werkzeug's reloader from duplicating jobs.
        app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
    finally:
        shutdown_scheduler(wait=False)


if __name__ == "__main__":
    main()
