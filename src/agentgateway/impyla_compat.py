"""Call impyla.connect with kwargs the installed client accepts.

CML and older wheels lack `verify_cert`. JWT still required (`impyla>=0.19`).
"""

from __future__ import annotations

import inspect
from typing import Any, Callable


def filter_impyla_kwargs(connect: Callable, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        params = inspect.signature(connect).parameters
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return dict(kwargs)
    allowed = set(params)
    return {key: value for key, value in kwargs.items() if key in allowed}


def connect_impyla(connect: Callable, kwargs: dict[str, Any]):
    filtered = filter_impyla_kwargs(connect, kwargs)
    if kwargs.get("jwt") and "jwt" not in filtered:
        raise TypeError("impyla is too old for JWT; pip install 'impyla>=0.19'")
    return connect(**filtered)
