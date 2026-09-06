"""Flask uygulama paketinin başlangıç modülü."""

import logging

from flask import Flask

from app.resource_paths import count_resource_files, get_static_directory, get_template_directory
from app.template_helpers import register_template_helpers
from config.settings import Config
from database.db import init_db


logger = logging.getLogger(__name__)


def create_app(config_overrides: dict | None = None) -> Flask:
	"""Application factory for the Watch Price Tracker app."""
	templates_path = get_template_directory()
	static_path = get_static_directory()
	logger.info(
		"Flask template path resolved to %s (exists=%s, files=%s)",
		templates_path,
		templates_path.is_dir(),
		count_resource_files(templates_path),
	)
	logger.info(
		"Flask static path resolved to %s (exists=%s, files=%s)",
		static_path,
		static_path.is_dir(),
		count_resource_files(static_path),
	)
	app = Flask(
		__name__,
		template_folder=str(templates_path),
		static_folder=str(static_path),
		static_url_path="/static",
	)
	app.config.from_object(Config)

	if config_overrides:
		app.config.update(config_overrides)

	register_template_helpers(app)
	init_db(app)

	from app.routes import main_bp

	app.register_blueprint(main_bp)

	return app
