"""
User ID resolver utility for the bot.

Provides a single entry point for distinguishing between system-generated IDs
and premium/custom IDs across all commands that accept a user_id parameter.

System-generated IDs follow the pattern: PREFIX_XXXXXXXX
  - PREFIX is CLI (client), FRL (freelancer), or SER (server_admin)
  - X is 8 random uppercase alphanumeric characters

**Normalization contract** (must match backend's ``resolve_profile_id()``):
  - System IDs (matched case-insensitively) → ``normalized`` is **ALL UPPERCASE**.
    The backend uses case-sensitive ``startswith("CLI_")`` checks, so the ID
    must arrive in uppercase.
  - Premium/custom IDs → ``normalized`` is **all lowercase**.
    The backend treats these as premium identifiers and does lowercased lookups.

Any ID that does not match the system pattern is considered a premium/custom ID.
"""

import re
from typing import Optional

# Pattern for system-generated IDs
# ^(CLI|FRL|SER)_[A-Z0-9]{8}$
SYSTEM_ID_PATTERN: re.Pattern = re.compile(r'^(CLI|FRL|SER)_[A-Z0-9]{8}$')

# Map prefix → role string
PREFIX_TO_ROLE: dict[str, str] = {
    'CLI': 'client',
    'FRL': 'freelancer',
    'SER': 'server_admin',
}


class UserIDResult:
    """Encapsulates the result of a user ID resolution."""

    def __init__(
        self,
        is_system: bool,
        original: str,
        normalized: str = '',
        prefix: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        """
        Args:
            is_system: True if the ID matches the system-generated pattern.
            original: The raw user input (preserved for display).
            normalized: The ID in the form expected by the backend.
                        UPPERCASE for system IDs, lowercase for premium IDs.
            prefix: The prefix (CLI, FRL, SER) — only set if is_system is True.
            role: The resolved role string (client, freelancer, server_admin) —
                  only set if is_system is True.
        """
        self.is_system = is_system
        self.original = original
        self.normalized = normalized
        self.prefix = prefix
        self.role = role

    @property
    def is_premium(self) -> bool:
        """Convenience property — True if the ID is NOT a system ID."""
        return not self.is_system

    def __repr__(self) -> str:
        return (
            f"UserIDResult(is_system={self.is_system}, "
            f"original={self.original!r}, normalized={self.normalized!r}, "
            f"prefix={self.prefix!r}, role={self.role!r})"
        )


def resolve_user_id(raw_id: str) -> UserIDResult:
    """
    Determines whether *raw_id* is a system-generated ID or a premium/custom ID.

    Resolution rules:
      1. Convert the entire input to **UPPERCASE** and test against the
         system pattern ``^(CLI|FRL|SER)_[A-Z0-9]{8}$``.
      2. If it matches → system ID.  The normalized form is the **uppercased**
         version, which satisfies the backend's case-sensitive ``startswith()``.
      3. If it does NOT match → premium ID.  The normalized form is the
         **lowercased** version.

    This means ``resolve_user_id("cli_A1B2C3D4")`` and
    ``resolve_user_id("CLI_A1B2C3D4")`` both produce the same result:
    ``normalized="CLI_A1B2C3D4"`` (uppercase).  The backend will never receive
    a lowercase system ID.

    Args:
        raw_id: The raw user-provided identifier string.

    Returns:
        A ``UserIDResult`` instance.
    """
    if not raw_id:
        return UserIDResult(
            is_system=False, original=raw_id or '', normalized=''
        )

    # --- System pattern matching (UPPERCASE) ---
    upper_id = raw_id.upper()
    match = SYSTEM_ID_PATTERN.match(upper_id)

    if match:
        prefix = match.group(1)  # CLI, FRL, or SER
        role = PREFIX_TO_ROLE[prefix]
        return UserIDResult(
            is_system=True,
            original=raw_id,
            normalized=upper_id,  # always uppercase → backend expects uppercase
            prefix=prefix,
            role=role,
        )

    # --- Premium / custom ID (lowercase) ---
    return UserIDResult(
        is_system=False,
        original=raw_id,
        normalized=raw_id.lower(),  # always lowercase
    )
