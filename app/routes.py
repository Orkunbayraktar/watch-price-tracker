"""Flask route definitions for the application."""

import logging

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from discovery import DiscoveryResult
from services.data_management_service import (
	DataManagementBlockedError,
	DataManagementConfirmationError,
	DataManagementError,
	clear_marketplace_data,
	clear_price_history,
	clear_scrape_history,
	clear_watchlist,
	get_data_inventory,
	reset_all_data,
)
from services.dashboard_service import get_dashboard_data
from services.data_quality_service import failure_message, get_data_quality_page
from services.import_service import ImportServiceError, commit_import, preview_import
from services.discovery_preview_store import DiscoveryPreviewError, DiscoveryPreviewStore
from services.product_discovery_service import add_discovered_products, discover_products, get_tracked_discovery_urls
from services.price_analysis_service import get_product_price_intelligence_page
from services.product_service import list_products
from services.scrape_run_service import get_recent_scrape_run_summaries, get_scrape_run_detail
from services.scraping_control_service import (
	ScrapingControlValidationError,
	ScrapingRunInProgressError,
	get_scraping_control_data,
	scrape_one_product,
	update_all_active_products,
	update_selected_products,
)
from services.settings_service import (
	SETTING_DEFINITIONS,
	SettingsServiceError,
	SettingsValidationError,
	get_application_settings,
	restore_default_settings,
	update_application_settings,
)
from services.seller_service import get_seller_detail, list_sellers
from services.watchlist_service import (
	WatchlistValidationError,
	create_watchlist_item,
	delete_watchlist_item,
	get_watchlist_item,
	list_watchlist_items,
	set_watchlist_item_active,
	update_active_watchlist_items,
	update_watchlist_item,
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


@main_bp.route("/data-quality")
def data_quality() -> str:
	"""Render validated operational health filters and problem-item history."""
	quality_page = get_data_quality_page(
		status=request.args.get("status"),
		platform=request.args.get("platform"),
		tracking=request.args.get("tracking"),
		search=request.args.get("search"),
		sort=request.args.get("sort"),
		page=request.args.get("page", 1, type=int) or 1,
	)
	return render_template("data_quality.html", quality_page=quality_page)


@main_bp.route("/scraping")
def scraping_control() -> str:
	"""Render the centralized synchronous scraping operations screen."""
	return _render_scraping_control()


@main_bp.route("/scraping/update-active", methods=["POST"])
def scraping_update_active() -> str:
	"""Update all active products through the Control Center service."""
	try:
		action_result = update_all_active_products()
		_flash_scraping_action_result(action_result)
		return _render_scraping_control(action_result=action_result)
	except ScrapingRunInProgressError as error:
		flash(str(error), "warning")
	except Exception:
		logger.exception("Unexpected error while updating active products from the Control Center")
		flash("An unexpected error occurred while updating active products.", "error")
	return _render_scraping_control()


@main_bp.route("/scraping/update-selected", methods=["POST"])
def scraping_update_selected() -> str:
	"""Update selected active products and report paused selections as skipped."""
	try:
		action_result = update_selected_products(request.form.getlist("item_ids"))
		_flash_scraping_action_result(action_result)
		return _render_scraping_control(action_result=action_result)
	except ScrapingControlValidationError as error:
		flash(str(error), "warning")
	except ScrapingRunInProgressError as error:
		flash(str(error), "warning")
	except Exception:
		logger.exception("Unexpected error while updating selected Control Center products")
		flash("An unexpected error occurred while updating selected products.", "error")
	return _render_scraping_control()


@main_bp.route("/scraping/scrape-one", methods=["POST"])
def scraping_scrape_one() -> str:
	"""Validate and synchronously scrape one supported marketplace URL."""
	submitted_url = request.form.get("url", "")
	try:
		action_result = scrape_one_product(submitted_url)
		_flash_scraping_action_result(action_result)
		return _render_scraping_control(action_result=action_result, manual_url=submitted_url)
	except WatchlistValidationError as error:
		flash(str(error), "error")
	except ScrapingRunInProgressError as error:
		flash(str(error), "warning")
	except Exception:
		logger.exception("Unexpected error while scraping one product from the Control Center")
		flash("An unexpected error occurred while scraping this product.", "error")
	return _render_scraping_control(manual_url=submitted_url)


@main_bp.route("/discovery")
def product_discovery() -> str:
	"""Render the read-only brand discovery workflow."""
	return _render_discovery_page()


@main_bp.route("/discovery/preview", methods=["POST"])
def preview_product_discovery() -> str:
	"""Run bounded marketplace discovery and store its preview server-side."""
	platform = request.form.get("platform", "trendyol")
	brand = request.form.get("brand", "")
	application_settings = get_application_settings()
	max_products = request.form.get("max_products", application_settings.discovery_max_products)
	try:
		result = discover_products(
			platform,
			brand,
			max_products=max_products,
			max_pages=application_settings.discovery_max_pages,
			absolute_max_products=current_app.config["DISCOVERY_ABSOLUTE_MAX_PRODUCTS"],
		)
		preview_token = _discovery_preview_store().save(result) if result.status == "success" else None
	except Exception:
		logger.exception("Unexpected error while discovering marketplace products")
		result = DiscoveryResult(
			platform=platform.strip().lower(),
			brand=" ".join(brand.split()),
			status="failed",
			source_url=None,
			failure_reason="discovery_parse_failed",
			message="The discovery operation could not be completed safely.",
		)
		preview_token = None
	return _render_discovery_page(
		discovery_result=result,
		preview_token=preview_token,
		form_values={"platform": platform, "brand": brand, "max_products": str(max_products)},
	)


@main_bp.route("/discovery/add", methods=["POST"])
def add_product_discovery_selection() -> str:
	"""Add explicitly selected preview products through the Watchlist service."""
	preview_token = request.form.get("preview_token", "")
	try:
		result = _discovery_preview_store().load(preview_token)
		add_result = add_discovered_products(result, request.form.getlist("selected_urls"))
	except DiscoveryPreviewError as error:
		flash(str(error), "warning")
		return _render_discovery_page()
	except Exception:
		logger.exception("Unexpected error while adding discovery selections")
		flash("An unexpected error occurred while adding selected products.", "error")
		return _render_discovery_page()

	if add_result.added_count:
		flash(f"{add_result.added_count} selected product(s) added to the Watchlist.", "success")
	elif not request.form.getlist("selected_urls"):
		flash("Select at least one new product to add to the Watchlist.", "warning")
	else:
		flash("No new products were added. The selection was already tracked or invalid.", "warning")
	return _render_discovery_page(
		discovery_result=result,
		preview_token=preview_token,
		add_result=add_result,
		form_values={"platform": result.platform, "brand": result.brand, "max_products": str(result.discovered_count or get_application_settings().discovery_max_products)},
	)


@main_bp.route("/settings")
def settings_page() -> str:
	"""Render editable application settings and safe data controls."""
	return _render_settings_page()


@main_bp.route("/settings/application", methods=["POST"])
def save_application_settings() -> str:
	"""Validate and persist the allowlisted editable settings."""
	try:
		update_application_settings(request.form)
		flash("Application settings saved.", "success")
		return redirect(url_for("main.settings_page"))
	except SettingsValidationError as error:
		return _render_settings_page(
			field_errors=error.field_errors,
			form_values={definition.key: request.form.get(definition.key, "") for definition in SETTING_DEFINITIONS},
		)
	except SettingsServiceError as error:
		flash(str(error), "error")
	return _render_settings_page()


@main_bp.route("/settings/application/defaults", methods=["POST"])
def restore_application_settings() -> str:
	"""Remove editable overrides without deleting application data."""
	try:
		restore_default_settings()
		flash("Default application settings restored. No marketplace data was changed.", "success")
	except SettingsServiceError as error:
		flash(str(error), "error")
	return redirect(url_for("main.settings_page"))


@main_bp.route("/settings/data/price-history", methods=["POST"])
def settings_clear_price_history() -> str:
	return _run_data_management_action(clear_price_history, "CLEAR_PRICE_HISTORY")


@main_bp.route("/settings/data/scrape-history", methods=["POST"])
def settings_clear_scrape_history() -> str:
	return _run_data_management_action(clear_scrape_history, "CLEAR_SCRAPE_HISTORY")


@main_bp.route("/settings/data/marketplace", methods=["POST"])
def settings_clear_marketplace_data() -> str:
	return _run_data_management_action(clear_marketplace_data, "CLEAR MARKETPLACE")


@main_bp.route("/settings/data/watchlist", methods=["POST"])
def settings_clear_watchlist() -> str:
	return _run_data_management_action(clear_watchlist, "CLEAR WATCHLIST")


@main_bp.route("/settings/data/reset", methods=["POST"])
def settings_reset_all_data() -> str:
	"""Reset all business data only after exact backend typed confirmation."""
	try:
		result = reset_all_data(request.form.get("confirmation", ""))
		flash(result.message, "success")
	except DataManagementConfirmationError as error:
		flash(str(error), "error")
	except DataManagementBlockedError as error:
		flash(str(error), "warning")
	except DataManagementError as error:
		flash(str(error), "error")
	return redirect(url_for("main.settings_page"))


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
	return _watchlist_action_redirect()


@main_bp.route("/watchlist/<int:item_id>/delete", methods=["POST"])
def remove_watchlist_item(item_id: int) -> str:
	"""Delete a tracked URL without removing persisted product history."""
	item = delete_watchlist_item(item_id)
	if item is None:
		abort(404)
	flash("Watchlist item removed.", "success")
	return _watchlist_action_redirect()


@main_bp.route("/data-quality/<int:item_id>/retry", methods=["POST"])
def retry_data_quality_item(item_id: int) -> str:
	"""Retry one active watchlist item through the existing batch service."""
	try:
		result = update_watchlist_item(item_id)
		item_result = result.item_results[0] if result.item_results else None
		if item_result is not None and item_result.status == "success":
			flash(f"Retry succeeded in run ID {result.run_id}.", "success")
		else:
			reason = failure_message(item_result.failure_reason if item_result is not None else None) or "Scrape attempt failed"
			flash(f"Retry finished in run ID {result.run_id}: {reason}.", "warning")
	except WatchlistValidationError as error:
		flash(str(error), "warning")
	except Exception:
		logger.exception("Unexpected error while retrying watchlist item %s", item_id)
		flash("An unexpected error occurred while retrying this item.", "error")
	return _watchlist_action_redirect(default_endpoint="main.data_quality")


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


def _render_scraping_control(*, action_result=None, manual_url: str = "") -> str:
	return render_template(
		"scraping_control.html",
		control=get_scraping_control_data(),
		action_result=action_result,
		manual_url=manual_url,
	)


def _render_discovery_page(
	*,
	discovery_result=None,
	preview_token: str | None = None,
	add_result=None,
	form_values: dict[str, str] | None = None,
) -> str:
	tracked_urls = get_tracked_discovery_urls(discovery_result.products) if discovery_result is not None else set()
	application_settings = get_application_settings()
	return render_template(
		"discovery.html",
		discovery_result=discovery_result,
		preview_token=preview_token,
		tracked_urls=tracked_urls,
		add_result=add_result,
		form_values=form_values or {
			"platform": "trendyol",
			"brand": "",
			"max_products": str(application_settings.discovery_max_products),
		},
		max_products_limit=current_app.config["DISCOVERY_ABSOLUTE_MAX_PRODUCTS"],
	)


def _render_settings_page(
	*,
	field_errors: dict[str, str] | None = None,
	form_values: dict[str, str] | None = None,
) -> str:
	settings = get_application_settings()
	return render_template(
		"settings.html",
		application_settings=settings,
		setting_definitions=SETTING_DEFINITIONS,
		inventory=get_data_inventory(),
		field_errors=field_errors or {},
		form_values=form_values or {key: str(value) for key, value in settings.to_dict().items()},
	)


def _run_data_management_action(operation, expected_confirmation: str):
	if request.form.get("confirmation", "") != expected_confirmation:
		flash("The confirmation did not match. No data was deleted.", "error")
		return redirect(url_for("main.settings_page"))
	try:
		result = operation()
		flash(result.message, "success")
	except DataManagementBlockedError as error:
		flash(str(error), "warning")
	except DataManagementError as error:
		flash(str(error), "error")
	return redirect(url_for("main.settings_page"))


def _discovery_preview_store() -> DiscoveryPreviewStore:
	return DiscoveryPreviewStore(
		current_app.config["DISCOVERY_STATE_DIR"],
		current_app.config["SECRET_KEY"],
		current_app.config["DISCOVERY_PREVIEW_TTL_SECONDS"],
	)


def _flash_scraping_action_result(action_result) -> None:
	batch_result = action_result.batch_result
	if batch_result is None:
		if action_result.skipped_paused:
			flash(
				f"No active selected products were updated. {action_result.skipped_paused} paused item(s) were skipped.",
				"warning",
			)
		else:
			flash("No active watchlist products are available for updating.", "warning")
		return

	message = (
		f"Run ID {batch_result.run_id} finished: {batch_result.successful} successful, "
		f"{batch_result.failed} failed."
	)
	if action_result.skipped_paused:
		message += f" {action_result.skipped_paused} paused item(s) skipped."
	flash(message, "success" if batch_result.failed == 0 else "warning")


def _watchlist_action_redirect(*, default_endpoint: str = "main.watchlist"):
	"""Return safely to an approved operational page after watchlist actions."""
	return_to = request.form.get("return_to")
	if return_to == "scraping":
		return redirect(url_for("main.scraping_control"))
	if return_to != "data_quality":
		return redirect(url_for(default_endpoint))
	allowed_keys = {"status", "platform", "tracking", "search", "sort", "page"}
	params = {
		key: request.form.get(key)
		for key in allowed_keys
		if request.form.get(key) not in {None, ""}
	}
	return redirect(url_for("main.data_quality", **params))
