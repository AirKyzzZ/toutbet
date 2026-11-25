import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description="Screenshot the Locust web UI while a test runs.")
    parser.add_argument("--url", default="http://127.0.0.1:8089")
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(args.url)
        page.wait_for_timeout(args.wait_seconds * 1000)
        page.get_by_role("tab", name="Statistics").click()
        page.wait_for_timeout(1500)
        page.screenshot(path=f"{args.prefix}-statistics.png", full_page=True)
        page.get_by_role("tab", name="Charts").click()
        page.wait_for_timeout(3000)
        page.screenshot(path=f"{args.prefix}-charts.png", full_page=True)
        page.get_by_role("tab", name="Failures").click()
        page.wait_for_timeout(1500)
        page.screenshot(path=f"{args.prefix}-failures.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
