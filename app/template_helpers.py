"""Shared Jinja filters and helpers for dashboard presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

from flask import Flask, request


PLATFORM_LABELS = {
	"trendyol": "Trendyol",
	"hepsiburada": "Hepsiburada",
}


def register_template_helpers(app: Flask) -> None:
	"""Register Jinja filters and helpers used across dashboard templates."""

	@app.template_filter("format_price")
	def format_price(value: Decimal | int | float | None, currency: str = "TRY") -> str:
		return _format_price_text(value, currency)

	@app.template_filter("format_signed_price")
	def format_signed_price(value: Decimal | int | float | None, currency: str = "TRY") -> str:
		decimal_value = _coerce_decimal(value)
		if decimal_value is None:
			return "Not available"
		if decimal_value > 0:
			return f"+{_format_price_text(decimal_value, currency)}"
		return _format_price_text(decimal_value, currency)

	@app.template_filter("format_percentage")
	def format_percentage(value: Decimal | int | float | None, always_sign: bool = False) -> str:
		decimal_value = _coerce_decimal(value)
		if decimal_value is None:
			return "Not available"
		quantized = decimal_value.quantize(Decimal("0.01"))
		formatted = f"{quantized:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
		if always_sign and quantized > 0:
			return f"+{formatted}%"
		return f"{formatted}%"

	@app.template_filter("format_datetime")
	def format_datetime(value: datetime | None) -> str:
		if value is None:
			return "Not available"
		if value.tzinfo is None:
			value = value.replace(tzinfo=timezone.utc)
		value = value.astimezone(timezone.utc)
		return value.strftime("%Y-%m-%d %H:%M UTC")

	@app.template_filter("platform_label")
	def platform_label(value: str | None) -> str:
		if value is None or not value.strip():
			return "Unknown"
		normalized = value.strip().lower()
		return PLATFORM_LABELS.get(normalized, normalized.replace("-", " ").title())

	@app.template_filter("platform_badge_class")
	def platform_badge_class(value: str | None) -> str:
		normalized = (value or "other").strip().lower()
		if normalized == "trendyol":
			return "platform-badge platform-badge--trendyol"
		if normalized == "hepsiburada":
			return "platform-badge platform-badge--hepsiburada"
		return "platform-badge platform-badge--other"

	@app.template_filter("change_direction")
	def change_direction(value: Decimal | int | float | None) -> str:
		decimal_value = _coerce_decimal(value)
		if decimal_value is None or decimal_value == 0:
			return "neutral"
		if decimal_value < 0:
			return "decrease"
		return "increase"

	@app.context_processor
	def inject_template_helpers() -> dict[str, object]:
		return {
			"merge_query_params": merge_query_params,
			"is_active_nav": is_active_nav,
		}


def _format_price_text(value: Decimal | int | float | None, currency: str = "TRY") -> str:
	decimal_value = _coerce_decimal(value)
	if decimal_value is None:
		return "Not available"
	quantized = decimal_value.quantize(Decimal("0.01"))
	formatted = f"{quantized:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
	if currency.upper() == "TRY":
		return f"{formatted} TL"
	return f"{formatted} {currency.upper()}"


def _coerce_decimal(value: Decimal | int | float | None) -> Decimal | None:
	if value is None:
		return None
	if isinstance(value, Decimal):
		return value
	return Decimal(str(value))


def merge_query_params(**updates: object) -> str:
	"""Merge query parameters while preserving existing filters in links."""
	query_params = request.args.to_dict(flat=True)
	for key, value in updates.items():
		if value is None or value == "":
			query_params.pop(key, None)
		else:
			query_params[key] = str(value)
	return urlencode(query_params)


def is_active_nav(section: str) -> bool:
	"""Return whether the current request endpoint belongs to a nav section."""
	endpoint = request.endpoint or ""
	sections = {
		"dashboard": {"main.index"},
		"products": {"main.products", "main.product_detail"},
		"sellers": {"main.sellers", "main.seller_detail"},
		"scrape_runs": {"main.scrape_runs", "main.scrape_run_detail"},
		"watchlist": {"main.watchlist", "main.add_watchlist_item", "main.toggle_watchlist_item", "main.remove_watchlist_item", "main.update_watchlist"},
		"data_quality": {"main.data_quality", "main.retry_data_quality_item"},
		"scraping": {"main.scraping_control", "main.scraping_update_active", "main.scraping_update_selected", "main.scraping_scrape_one"},
		"discovery": {"main.product_discovery", "main.preview_product_discovery", "main.add_product_discovery_selection"},
		"import": {"main.import_data"},
		"settings": {
			"main.settings_page",
			"main.save_application_settings",
			"main.restore_application_settings",
			"main.settings_clear_price_history",
			"main.settings_clear_scrape_history",
			"main.settings_clear_marketplace_data",
			"main.settings_clear_watchlist",
			"main.settings_reset_all_data",
		},
	}
	return endpoint in sections.get(section, set())
