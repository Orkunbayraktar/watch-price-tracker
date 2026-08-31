"""Flask uygulama paketinin başlangıç modülü."""

from pathlib import Path

from flask import Flask

from config.settings import Config
from database.db import init_db


def create_app(config_overrides: dict | None = None) -> Flask:
	"""Application factory for the Watch Price Tracker app."""
	project_root = Path(__file__).resolve().parent.parent
	app = Flask(
		__name__,
		template_folder=str(project_root / "templates"),
		static_folder=str(project_root / "static"),
		static_url_path="/static",
	)
	app.config.from_object(Config)

	if config_overrides:
		app.config.update(config_overrides)

	init_db(app)

	from app.routes import main_bp

	app.register_blueprint(main_bp)

	return app
