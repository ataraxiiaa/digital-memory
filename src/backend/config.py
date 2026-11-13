"""Load backend configuration without revealing credentials in errors."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ConfigurationError(Exception):
    """Actionable configuration failure with no credential values."""


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    publishable_key: str = field(repr=False)

    def __post_init__(self):
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme == "https" and parsed.hostname
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and parsed.path in ("", "/") and parsed.port in (None, 443)
                and not any(char.isspace() for char in self.url)
            )
        except ValueError:
            valid = False
        if not valid or "your-project-ref" in self.url:
            raise ConfigurationError("Set SUPABASE_URL to your project's HTTPS origin")
        if (
            not self.publishable_key.startswith("sb_publishable_")
            or "replace_me" in self.publishable_key
            or any(char.isspace() for char in self.publishable_key)
        ):
            raise ConfigurationError("Set SUPABASE_PUBLISHABLE_KEY to a publishable key; do not use a secret key")


def load_supabase_settings(env_file: Path = ENV_FILE) -> SupabaseSettings:
    # Process environment wins, including deliberately empty values.
    values = {**dotenv_values(env_file, encoding="utf-8-sig", interpolate=False), **os.environ}
    required = ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY")
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ConfigurationError("Missing configuration: " + ", ".join(missing))
    return SupabaseSettings(
        url=str(values["SUPABASE_URL"]).strip().rstrip("/"),
        publishable_key=str(values["SUPABASE_PUBLISHABLE_KEY"]).strip(),
    )
