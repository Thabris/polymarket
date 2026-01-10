#!/usr/bin/env python3
"""Interactive script to set up Polymarket credentials securely."""

import getpass
import sys
from typing import Optional

try:
    import keyring
except ImportError:
    print("Error: keyring package not installed.")
    print("Run: pip install keyring")
    sys.exit(1)

SERVICE_NAME = "polymarket-monitor"

CREDENTIALS = {
    "polymarket_api_key": {
        "prompt": "Polymarket API Key",
        "required": True,
        "secret": False,
    },
    "polymarket_api_secret": {
        "prompt": "Polymarket API Secret",
        "required": True,
        "secret": True,
    },
    "polymarket_private_key": {
        "prompt": "Wallet Private Key (for trading, optional)",
        "required": False,
        "secret": True,
    },
}


def get_input(prompt: str, secret: bool = False, required: bool = True) -> Optional[str]:
    """Get user input, optionally hiding the input for secrets."""
    suffix = "" if required else " (press Enter to skip)"
    full_prompt = f"{prompt}{suffix}: "

    if secret:
        value = getpass.getpass(full_prompt)
    else:
        value = input(full_prompt)

    value = value.strip()

    if not value and required:
        print("This field is required.")
        return get_input(prompt, secret, required)

    return value if value else None


def store_credential(name: str, value: str) -> bool:
    """Store a credential in keyring."""
    try:
        keyring.set_password(SERVICE_NAME, name, value)
        return True
    except Exception as e:
        print(f"Failed to store {name}: {e}")
        return False


def get_credential(name: str) -> Optional[str]:
    """Get a credential from keyring."""
    try:
        return keyring.get_password(SERVICE_NAME, name)
    except Exception:
        return None


def delete_credential(name: str) -> bool:
    """Delete a credential from keyring."""
    try:
        keyring.delete_password(SERVICE_NAME, name)
        return True
    except keyring.errors.PasswordDeleteError:
        return True  # Already deleted
    except Exception as e:
        print(f"Failed to delete {name}: {e}")
        return False


def show_status():
    """Show current credential status."""
    print("\n=== Current Credential Status ===")
    for name, config in CREDENTIALS.items():
        value = get_credential(name)
        if value:
            # Show only first/last few characters
            masked = f"{value[:4]}...{value[-4:]}" if len(value) > 10 else "****"
            status = f"Set ({masked})"
        else:
            status = "Not set"
        print(f"  {config['prompt']}: {status}")
    print()


def setup_credentials():
    """Interactive credential setup."""
    print("\n" + "=" * 50)
    print("  Polymarket Monitor - Credential Setup")
    print("=" * 50)
    print("\nCredentials will be stored securely in Windows Credential Manager.")
    print("You can get API credentials from your Polymarket builder profile.\n")

    show_status()

    print("Enter new credentials (or press Ctrl+C to cancel):\n")

    try:
        for name, config in CREDENTIALS.items():
            existing = get_credential(name)
            if existing:
                update = input(f"{config['prompt']} is already set. Update? (y/N): ")
                if update.lower() != "y":
                    continue

            value = get_input(
                config["prompt"],
                secret=config["secret"],
                required=config["required"],
            )

            if value:
                if store_credential(name, value):
                    print(f"  -> Stored successfully\n")
                else:
                    print(f"  -> Failed to store\n")
            elif not config["required"]:
                print(f"  -> Skipped\n")

    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        return

    print("\n" + "=" * 50)
    show_status()
    print("Setup complete!")


def clear_credentials():
    """Clear all stored credentials."""
    print("\nThis will delete all stored Polymarket credentials.")
    confirm = input("Are you sure? (yes/N): ")

    if confirm.lower() != "yes":
        print("Cancelled.")
        return

    for name in CREDENTIALS:
        if delete_credential(name):
            print(f"  Deleted: {name}")

    print("\nAll credentials cleared.")


def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command == "clear":
            clear_credentials()
        elif command == "status":
            show_status()
        elif command == "help":
            print("Usage: python setup_credentials.py [command]")
            print("\nCommands:")
            print("  (none)  - Interactive credential setup")
            print("  status  - Show current credential status")
            print("  clear   - Delete all stored credentials")
            print("  help    - Show this help message")
        else:
            print(f"Unknown command: {command}")
            print("Run with 'help' for usage information.")
    else:
        setup_credentials()


if __name__ == "__main__":
    main()
