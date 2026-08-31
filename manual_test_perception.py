"""
Manual, one-off script to verify perception.py against a REAL page.
Not part of the automated pytest suite -- run directly with:
    python manual_test_perception.py
"""

from playwright.sync_api import sync_playwright

from capability_recorder.perception import capture_observation


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # headless=False so you can SEE it
        page = browser.new_page()
        page.goto("https://www.saucedemo.com/")

        observation = capture_observation(page)

        print(f"URL: {observation.url}")
        print(f"\nFound {len(observation.elements)} interactive elements:\n")
        for el in observation.elements:
            locator = el.locators[0]
            print(f"[{el.index}] {el.role} \"{el.name}\" "
                  f"(confidence: {locator.confidence}, value: {locator.value})")

        input("\nPress Enter to close the browser...")
        browser.close()


if __name__ == "__main__":
    main()