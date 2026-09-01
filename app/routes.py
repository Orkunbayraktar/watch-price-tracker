"""Flask route definitions for the application."""

import logging

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from services.dashboard_service import get_dashboard_data
from services.import_service import ImportServiceError, commit_import, preview_import
from services.price_analysis_service import get_product_price_intelligence_page
from services.product_service import list_products
from services.scrape_run_service import get_recent_scrape_run_summaries, get_scrape_run_detail
from services.seller_service import get_seller_detail, list_sellers
from services.watchlist_service import (
	WatchlistValidationError,
	create_watchlist_item,
	delete_watchlist_item,
	get_watchlist_item,
	list_watchlist_items,
	set_watchlist_item_active,
	update_active_watchlist_items,
)


main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)


@main_bp.route("/")
def index() -> str:
	"""Render the homepage dashboard."""
	return render_template("index.html", dashboard=get_dashboard_data())


@main_bp.route("/products")
def products() -> str:
	"""Render the paginated products analytics page."""
	product_result = list_products(
		q=request.args.get("q"),
		brand=request.args.get("brand"),
		platform=request.args.get("platform"),
		sort=request.args.get("sort", "updated_desc"),
		page=request.args.get("page", 1, type=int) or 1,
	)
	return render_template("products.html", product_result=product_result)


@main_bp.route("/products/<int:product_id>")
def product_detail(product_id: int) -> str:
	"""Render the product detail page for a single product."""
	product_page = get_product_price_intelligence_page(
		product_id,
		range_key=request.args.get("range"),
		selected_listing_id=request.args.get("listing", type=int),
		history_page=request.args.get("history_page", 1, type=int) or 1,
	)
	if product_page is None:
		abort(404)
	return render_template("product_detail.html", product_page=product_page)


@main_bp.route("/sellers")
def sellers() -> str:
	"""Render the paginated sellers analytics page."""
	seller_result = list_sellers(
		q=request.args.get("q"),
		platform=request.args.get("platform"),
		page=request.args.get("page", 1, type=int) or 1,
	)
	return render_template("sellers.html", seller_result=seller_result)


@main_bp.route("/scrape-runs")
def scrape_runs() -> str:
	"""Render recent scrape runs for operational visibility."""
	return render_template("scrape_runs.html", scrape_runs=get_recent_scrape_run_summaries(limit=25))


@main_bp.route("/scrape-runs/<int:run_id>")
def scrape_run_detail(run_id: int) -> str:
	"""Render the detail page for a single scrape run."""
	run_detail = get_scrape_run_detail(run_id)
	if run_detail is None:
		abort(404)
	return render_template("scrape_run_detail.html", run_detail=run_detail)


@main_bp.route("/sellers/<int:seller_id>")
def seller_detail(seller_id: int) -> str:
	"""Render the seller detail page for a single seller."""
	seller_page = get_seller_detail(seller_id)
	if seller_page is None:
		abort(404)
	return render_template("seller_detail.html", seller_page=seller_page)


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


@main_bp.route("/watchlist")
def watchlist() -> str:
	"""Render the watchlist management page."""
	return _render_watchlist_page()


@main_bp.route("/watchlist", methods=["POST"])
def add_watchlist_item() -> str:
	"""Validate and store a tracked marketplace product URL."""
	submitted_url = request.form.get("url", "")
	submitted_label = request.form.get("label", "")
	try:
		create_watchlist_item(submitted_url, display_name=submitted_label)
		flash("Product added to the watchlist.", "success")
		return redirect(url_for("main.watchlist"))
	except WatchlistValidationError as error:
		flash(str(error), "error")
	except Exception:
		logger.exception("Unexpected error while creating a watchlist item")
		flash("An unexpected error occurred while adding the watchlist item.", "error")
	return _render_watchlist_page(form_values={"url": submitted_url, "label": submitted_label})


@main_bp.route("/watchlist/<int:item_id>/toggle", methods=["POST"])
def toggle_watchlist_item(item_id: int) -> str:
	"""Pause or reactivate a tracked watchlist URL."""
	item = get_watchlist_item(item_id)
	if item is None:
		abort(404)

	updated_item = set_watchlist_item_active(item_id, not item.is_active)
	if updated_item is None:
		abort(404)

	if updated_item.is_active:
		flash("Watchlist item activated.", "success")
	else:
		flash("Watchlist item paused.", "warning")
	return redirect(url_for("main.watchlist"))


@main_bp.route("/watchlist/<int:item_id>/delete", methods=["POST"])
def remove_watchlist_item(item_id: int) -> str:
	"""Delete a tracked URL without removing persisted product history."""
	item = delete_watchlist_item(item_id)
	if item is None:
		abort(404)
	flash("Watchlist item removed.", "success")
	return redirect(url_for("main.watchlist"))


@main_bp.route("/watchlist/update-active", methods=["POST"])
def update_watchlist() -> str:
	"""Run a synchronous batch update for active watchlist items."""
	try:
		update_result = update_active_watchlist_items()
		if update_result is None:
			flash("No active watchlist items are available for updating.", "warning")
			return _render_watchlist_page()
		flash(
			f"Batch update finished. Run ID {update_result.run_id}: {update_result.successful} successful, {update_result.failed} failed.",
			"success" if update_result.failed == 0 else "warning",
		)
		return _render_watchlist_page(update_result=update_result)
	except WatchlistValidationError as error:
		flash(str(error), "error")
	except Exception:
		logger.exception("Unexpected error while updating watchlist items")
		flash("An unexpected error occurred while updating the watchlist.", "error")
	return _render_watchlist_page()


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


def _render_watchlist_page(*, update_result=None, form_values: dict[str, str] | None = None) -> str:
	items = list_watchlist_items()
	return render_template(
		"watchlist.html",
		watchlist_items=items,
		active_watchlist_count=sum(1 for item in items if item.is_active),
		update_result=update_result,
		form_values=form_values or {"url": "", "label": ""},
	)
