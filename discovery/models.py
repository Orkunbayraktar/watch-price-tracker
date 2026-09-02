"""Database-independent data transfer objects for catalog discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class DiscoveredProduct:
	"""Public product-card metadata collected from a marketplace page."""

	platform: str
	product_url: str
	external_product_id: str | None = None
	product_name: str | None = None
	brand: str | None = None
	current_price: Decimal | None = None
	currency: str | None = None
	image_url: str | None = None
	discovery_source_url: str | None = None

	def to_dict(self) -> dict[str, Any]:
		payload = asdict(self)
		payload["current_price"] = str(self.current_price) if self.current_price is not None else None
		return payload

	@classmethod
	def from_dict(cls, payload: dict[str, Any]) -> "DiscoveredProduct":
		price = payload.get("current_price")
		return cls(
			platform=str(payload["platform"]),
			product_url=str(payload["product_url"]),
			external_product_id=payload.get("external_product_id"),
			product_name=payload.get("product_name"),
			brand=payload.get("brand"),
			current_price=Decimal(str(price)) if price is not None else None,
			currency=payload.get("currency"),
			image_url=payload.get("image_url"),
			discovery_source_url=payload.get("discovery_source_url"),
		)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
	"""Structured success or failure returned by every discovery provider."""

	platform: str
	brand: str
	status: str
	source_url: str | None
	products: tuple[DiscoveredProduct, ...] = ()
	pages_scanned: int = 0
	failure_reason: str | None = None
	message: str | None = None

	@property
	def discovered_count(self) -> int:
		return len(self.products)

	def to_dict(self) -> dict[str, Any]:
		return {
			"platform": self.platform,
			"brand": self.brand,
			"status": self.status,
			"source_url": self.source_url,
			"products": [product.to_dict() for product in self.products],
			"pages_scanned": self.pages_scanned,
			"failure_reason": self.failure_reason,
			"message": self.message,
		}

	@classmethod
	def from_dict(cls, payload: dict[str, Any]) -> "DiscoveryResult":
		return cls(
			platform=str(payload["platform"]),
			brand=str(payload["brand"]),
			status=str(payload["status"]),
			source_url=payload.get("source_url"),
			products=tuple(DiscoveredProduct.from_dict(item) for item in payload.get("products", [])),
			pages_scanned=int(payload.get("pages_scanned", 0)),
			failure_reason=payload.get("failure_reason"),
			message=payload.get("message"),
		)
