"""Configuration module for Polymarket Monitor."""

from .settings import settings
from .credentials import get_credential, set_credential, CredentialManager

__all__ = ["settings", "get_credential", "set_credential", "CredentialManager"]
