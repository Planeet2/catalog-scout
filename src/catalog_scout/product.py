from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Product:
    product_id: int
    sku: str
    name: str
    category: str
    price: Decimal | None
    original_price: Decimal | None
    currency: str
    rating: Decimal | None
    review_count: int | None
    availability: str
    product_url: str
    image_url: str
