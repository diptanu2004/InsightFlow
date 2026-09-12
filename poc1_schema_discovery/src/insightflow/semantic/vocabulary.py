from typing import Optional

CANONICAL_FIELDS: dict[str, str] = {
    "customer_id": "Unique identifier for a customer",
    "customer_name": "Name of the customer",
    "region": "Geographic region of the customer",
    "signup_date": "Date the customer signed up",
    "order_id": "Unique identifier for an order",
    "transaction_date": "Date the order/transaction occurred",
    "revenue": "Monetary value of an order/transaction",
    "product_id": "Unique identifier for a product",
    "product_name": "Name/title of a product",
    "category": "Product category",
    "price": "Unit price of a product",
}


class SemanticVocabulary:
    """The canonical field vocabulary the mapper is allowed to map columns into.
    Swap or extend this per-business-domain later without touching SemanticMapper."""

    def __init__(self, fields: Optional[dict[str, str]] = None):
        self.canonical_fields = fields or CANONICAL_FIELDS

    def as_prompt_block(self) -> str:
        return "\n".join(f"- {name}: {desc}" for name, desc in self.canonical_fields.items())
