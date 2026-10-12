"""Mapping between logical output states (closed/open) and physical GPIO levels."""

__all__ = ["closed_level"]


def closed_level(physical_closed: str, entry_name: str = "physical-closed") -> int:
    """GPIO level (0/1) that closes an output: config value ``low`` → 0, ``high`` → 1.

    :raises ValueError: for any other value.
    """
    value = str(physical_closed).lower()
    if value == "low":
        return 0
    if value == "high":
        return 1
    raise ValueError(f"'{entry_name}' - expected 'low'/'high' but received '{physical_closed}'.")
