from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


Builder = Callable[[str], dict]
ScopeSource = Callable[[int], Iterable[str]]


@dataclass(frozen=True)
class ReadModelSpec:
    model_key: str
    scope_type: str
    schema_version: int
    ttl_seconds: int
    builder: Builder
    scope_source: ScopeSource | None = None


_REGISTRY: dict[str, ReadModelSpec] = {}


def register(spec: ReadModelSpec) -> None:
    existing = _REGISTRY.get(spec.model_key)
    if existing is not None and existing != spec:
        raise RuntimeError(f"Read model {spec.model_key!r} is already registered.")
    _REGISTRY[spec.model_key] = spec


def registered(model_key: str) -> ReadModelSpec:
    try:
        return _REGISTRY[model_key]
    except KeyError as exc:
        raise KeyError(f"No read-model builder is registered for {model_key!r}.") from exc


def clear_registry() -> None:
    """Test helper; production code never unregisters builders."""
    _REGISTRY.clear()

