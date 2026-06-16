import re


def sanitize_location(value: str) -> str:
    normalized = (value or "").strip().upper()
    normalized = re.sub(r"[^A-Z0-9\s\-']", "", normalized)
    return normalized.replace("'", "''")


def normalize_customer_segment(value: str | None) -> str:
    normalized = (value or "res").strip().lower()
    return "com" if normalized == "com" else "res"


def normalize_limit(value: int | str | None, default: int = 25, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
