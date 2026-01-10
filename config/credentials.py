"""Secure credential management using keyring with fallback to environment variables."""

import os
from typing import Optional

import keyring

# Service name for keyring storage
SERVICE_NAME = "polymarket-monitor"

# Credential keys
CREDENTIAL_KEYS = {
    "api_key": "polymarket_api_key",
    "api_secret": "polymarket_api_secret",
    "private_key": "polymarket_private_key",
}


class CredentialManager:
    """Manages secure credential storage and retrieval."""

    def __init__(self, service_name: str = SERVICE_NAME):
        self.service_name = service_name

    def get(self, key: str) -> Optional[str]:
        """
        Get a credential value.

        Priority:
        1. Keyring (Windows Credential Manager)
        2. Environment variable
        3. None

        Args:
            key: Credential key (api_key, api_secret, private_key)

        Returns:
            Credential value or None if not found
        """
        credential_name = CREDENTIAL_KEYS.get(key, key)

        # Try keyring first
        try:
            value = keyring.get_password(self.service_name, credential_name)
            if value:
                return value
        except Exception:
            # Keyring not available or error
            pass

        # Fall back to environment variable
        env_var = f"POLYMARKET_{key.upper()}"
        return os.getenv(env_var)

    def set(self, key: str, value: str) -> bool:
        """
        Store a credential securely in keyring.

        Args:
            key: Credential key (api_key, api_secret, private_key)
            value: Credential value

        Returns:
            True if stored successfully, False otherwise
        """
        credential_name = CREDENTIAL_KEYS.get(key, key)

        try:
            keyring.set_password(self.service_name, credential_name, value)
            return True
        except Exception as e:
            print(f"Failed to store credential in keyring: {e}")
            return False

    def delete(self, key: str) -> bool:
        """
        Delete a credential from keyring.

        Args:
            key: Credential key

        Returns:
            True if deleted successfully, False otherwise
        """
        credential_name = CREDENTIAL_KEYS.get(key, key)

        try:
            keyring.delete_password(self.service_name, credential_name)
            return True
        except keyring.errors.PasswordDeleteError:
            # Credential doesn't exist
            return True
        except Exception as e:
            print(f"Failed to delete credential from keyring: {e}")
            return False

    def has_credentials(self) -> bool:
        """Check if API credentials are configured."""
        api_key = self.get("api_key")
        api_secret = self.get("api_secret")
        return bool(api_key and api_secret)

    def has_trading_credentials(self) -> bool:
        """Check if trading credentials (private key) are configured."""
        return bool(self.get("private_key"))


# Global credential manager instance
_credential_manager = CredentialManager()


def get_credential(key: str) -> Optional[str]:
    """Get a credential value."""
    return _credential_manager.get(key)


def set_credential(key: str, value: str) -> bool:
    """Store a credential securely."""
    return _credential_manager.set(key, value)


def delete_credential(key: str) -> bool:
    """Delete a credential."""
    return _credential_manager.delete(key)


def has_credentials() -> bool:
    """Check if API credentials are configured."""
    return _credential_manager.has_credentials()


def has_trading_credentials() -> bool:
    """Check if trading credentials are configured."""
    return _credential_manager.has_trading_credentials()
