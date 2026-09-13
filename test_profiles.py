"""Compatibility imports; implementation lives in test_engine.test_profiles."""

from test_engine.test_profiles import (
    load_profile,
    save_profile,
    list_profiles,
    init_profiles,
    generate_sweep_steps,
)

__all__ = [
    "load_profile",
    "save_profile",
    "list_profiles",
    "init_profiles",
    "generate_sweep_steps",
]
