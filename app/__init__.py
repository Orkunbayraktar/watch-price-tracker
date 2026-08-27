"""Flask uygulama paketinin başlangıç modülü."""

from pathlib import Path

from flask import Flask


def create_app() -> Flask:
	"""Application factory for the Watch Price Tracker app."""
	project_root = Path(__file__).resolve().parent.parent
	app = Flask(
		__name__,
		template_folder=str(project_root / "templates"),
		static_folder=str(project_root / "static"),
		static_url_path="/static",
	)

	from app.routes import main_bp

	app.register_blueprint(main_bp)

	return app
