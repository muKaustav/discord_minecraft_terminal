"""Small security helpers shared by Flask form and JSON mutations."""

from __future__ import annotations

import hmac


def csrf_matches(expected: str | None, supplied: str | None) -> bool:
    return bool(expected and supplied) and hmac.compare_digest(expected, supplied)
