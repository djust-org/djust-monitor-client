import re

SENSITIVE_PATTERNS = re.compile(
    r"(password|secret|token|api_key|authorization|cookie|session|credit_card|ssn)",
    re.IGNORECASE,
)

FILTERED = "[Filtered]"


def scrub(data: dict | list, _depth: int = 0) -> None:
    """Recursively scrub sensitive values from a dict/list in-place."""
    if _depth > 20:
        return

    if isinstance(data, dict):
        for key in data:
            if isinstance(key, str) and SENSITIVE_PATTERNS.search(key):
                data[key] = FILTERED
            elif isinstance(data[key], (dict, list)):
                scrub(data[key], _depth + 1)
            elif isinstance(data[key], str) and isinstance(key, str) and SENSITIVE_PATTERNS.search(key):
                data[key] = FILTERED
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                scrub(item, _depth + 1)
