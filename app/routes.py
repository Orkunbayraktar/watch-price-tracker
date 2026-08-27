"""Flask route definitions for the application."""

from flask import Blueprint, render_template


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index() -> str:
	"""Render the homepage dashboard."""
	return render_template("index.html")
