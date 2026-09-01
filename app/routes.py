"""Flask route definitions for the application."""

import logging

from flask import Blueprint, current_app, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from services.import_service import ImportServiceError, commit_import, preview_import


main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)


@main_bp.route("/")
def index() -> str:
	"""Render the homepage dashboard."""
	return render_template("index.html")


@main_bp.route("/import", methods=["GET", "POST"])
def import_data() -> str:
	"""Render the file import page and handle preview/import actions."""
	context = {
		"page_error": None,
		"preview_result": None,
		"import_summary": None,
	}

	if request.method == "GET":
		return render_template("import.html", **context)

	action = request.form.get("action", "preview")
	state_dir = current_app.config.get("IMPORT_STATE_DIR")
	preview_limit = int(current_app.config.get("IMPORT_PREVIEW_LIMIT", 20))

	try:
		if action == "preview":
			uploaded_file = request.files.get("import_file")
			if uploaded_file is None or not uploaded_file.filename:
				context["page_error"] = "Select a CSV or XLSX file before previewing."
				return render_template("import.html", **context)

			context["preview_result"] = preview_import(
				uploaded_file,
				state_dir=state_dir,
				preview_limit=preview_limit,
			)
			if context["preview_result"].valid_rows == 0:
				context["page_error"] = "No valid rows were found in the uploaded file."
			return render_template("import.html", **context)

		if action == "import":
			preview_token = request.form.get("preview_token", "")
			context["import_summary"] = commit_import(preview_token, state_dir=state_dir)
			return render_template("import.html", **context)

		context["page_error"] = "Unsupported form action."
	except ImportServiceError as error:
		logger.warning("Import page action failed: %s", error)
		context["page_error"] = str(error)
	except Exception:
		logger.exception("Unexpected error while handling the import page")
		context["page_error"] = "An unexpected import error occurred."

	return render_template("import.html", **context)


@main_bp.app_errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(error: RequestEntityTooLarge) -> tuple[str, int]:
	"""Render a friendly message when an uploaded file exceeds the configured size limit."""
	logger.warning("Import upload rejected because it exceeded MAX_CONTENT_LENGTH: %s", error)
	return (
		render_template(
			"import.html",
			page_error="The uploaded file is too large. Maximum supported upload size is 10 MB.",
			preview_result=None,
			import_summary=None,
		),
		413,
	)
