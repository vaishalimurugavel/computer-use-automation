from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto('https://www.saucedemo.com/')
    print(pg.locator('body').aria_snapshot())
    b.close()