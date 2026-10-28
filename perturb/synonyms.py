"""Neutral key synonyms for the key-naming condition.

Every key a task's tools can emit should appear here; tests/test_perturb.py fails on
any key the reference solutions see that is missing. Values must be unique and must not
collide with another original key, or the mapping could not be inverted.
"""

SYNONYMS: dict[str, str] = {
    # orders
    "customer_id": "cust_ref",
    "name": "full_name",
    "email": "email_address",
    "tier": "level",
    "orders": "purchases",
    "order_id": "order_ref",
    "placed_at": "ordered_on",
    "status": "state",
    "total": "grand_total",
    "delivered_at": "delivery_date",
    "items": "lines",
    "sku": "product_code",
    "description": "title",
    "quantity": "qty",
    "unit_price": "price_each",
    "subtotal": "items_total",
    "shipping": "delivery_fee",
    "policy_id": "policy_ref",
    "rules": "clauses",
    "refund_id": "refund_ref",
    "amount": "amt",
    "reason": "cause",
    # records
    "record_id": "rec_ref",
    "query": "search_text",
    "matches": "hits",
    "created_at": "opened_on",
    "updated_at": "edited_on",
    "last_active_at": "last_seen_on",
    "date_of_birth": "birth_date",
    "subscription_status": "plan_state",
    "merged_into": "survivor_ref",
    "fields": "attributes",
    "field": "attribute_name",
    "value": "content",
    "verified_at": "confirmed_on",
    "source": "origin",
    "sources": "origins",
    "source_precedence": "origin_ranking",
    "changes": "edits",
    "changed_at": "edited_at",
    "changed_by": "edited_by",
    # inventory
    "warehouses": "sites",
    "warehouse_id": "site_ref",
    "city": "location",
    "on_hand": "in_stock",
    "reserved": "allocated",
    "safety_stock": "min_level",
    "transfer_id": "move_ref",
    "from_warehouse_id": "source_site",
    "to_warehouse_id": "dest_site",

    # scheduling



    # support



}

# Keys deliberately left unchanged in every condition.
UNMAPPED: frozenset[str] = frozenset({"error"})

SYNONYMS_INVERSE: dict[str, str] = {v: k for k, v in SYNONYMS.items()}

if len(SYNONYMS_INVERSE) != len(SYNONYMS):
    raise ValueError("SYNONYMS values must be unique")
if set(SYNONYMS_INVERSE) & (set(SYNONYMS) | UNMAPPED):
    raise ValueError("a synonym collides with an original key")
