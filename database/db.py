"""Database extension and initialization helpers."""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from config.settings import DATA_DIR


db = SQLAlchemy()


def init_db(app: Flask) -> None:
	"""Bind the SQLAlchemy extension to the Flask app."""
	DATA_DIR.mkdir(parents=True, exist_ok=True)
	db.init_app(app)


def initialize_database(app: Flask) -> None:
	"""Create database tables if they do not already exist."""
	from database import models  # noqa: F401

	with app.app_context():
		db.create_all()
