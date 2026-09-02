"""
Manual, one-off script for a LIVE, end-to-end discovery run against
SauceDemo. Not part of the automated pytest suite -- requires a real
Playwright browser and a real Gemini API key.

Set your API key as an environment variable before running:
    PowerShell:  $env:GEMINI_API_KEY = "your-key-here"
    Then:        python manual_test_discovery.py

Never hardcode API keys directly in source files, especially in a
public repository.
"""

import json
import os

from playwright.sync_api import sync_playwright

from capability_recorder.agent import discover_capability

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY environment variable is not set. "
        "Run: $env:GEMINI_API_KEY = 'your-key-here' before running this script."
    )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # headless=False so you can SEE it work
        page = browser.new_page()
        page.goto("https://www.saucedemo.com/")

        capability = discover_capability(
            page=page,
            goal=(
                "Log in with username 'standard_user' and password 'secret_sauce', "
                "add the Sauce Labs Backpack to the cart, then go to the cart page."
            ),
            api_key=GEMINI_API_KEY,
            capability_id="cap-001",
            name="login_add_backpack_view_cart",
            target_app="saucedemo",
            success_check_text="Your Cart",
            max_iterations=15,
        )

        print("\n=== DISCOVERED CAPABILITY ===")
        print(json.dumps(capability.model_dump(mode="json"), indent=2))

        input("\nPress Enter to close the browser...")
        browser.close()


if __name__ == "__main__":
    main()