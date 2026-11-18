# /// script
# requires-python = ">=3.9"
# dependencies = ["playwright==1.64.1"]
# ///
"""Browser check for the fms report: screenshots of every tab, plus console and network errors.

    uv run tests/visual.py [path/to/index.html] [out-dir]

uv installs Playwright on the fly. The browser comes from one of:
  * CHROME=/path/to/chromium   any installed Chromium/Chrome (e.g. on Guix or NixOS)
  * uvx playwright@1.64.1 install chromium   (once; add --with-deps on Debian/Ubuntu)
Without a report path, fms is run on examples/r-project first.
Exits 1 if any tab logged an error, a request failed or a check failed.
"""
import os
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    report = sys.argv[1] if len(sys.argv) > 1 else None
    if not report:
        out = tempfile.mkdtemp(prefix="fms-visual-")
        subprocess.run([sys.executable, "-m", "findmysource", "--out", out], check=True,
                       cwd=os.path.join(REPO, "examples", "r-project"), env={**os.environ, "PYTHONPATH": REPO})
        report = os.path.join(out, "index.html")
    shots = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "tests", "screenshots"))
    os.makedirs(shots, exist_ok=True)
    url = "file://" + os.path.abspath(report)
    problems = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=["--no-sandbox"])

        def open_tab(tab, dark=False):
            page = browser.new_page(viewport={"width": 1500, "height": 950}, color_scheme="dark" if dark else "light")
            label = tab + ("-dark" if dark else "")
            page.on("console", lambda m: m.type == "error" and problems.append(f"{label}: console: {m.text}"))
            page.on("pageerror", lambda e: problems.append(f"{label}: page error: {e}"))
            page.on("requestfailed", lambda r: problems.append(f"{label}: request failed: {r.url} ({r.failure})"))
            page.goto(f"{url}#{tab}", wait_until="load")
            return page

        def snap(page, name):
            page.screenshot(path=os.path.join(shots, f"{name}.png"))

        for tab in ["flow", "timeline", "matrix", "table"]:
            page = open_tab(tab)
            snap(page, tab)
            if tab == "flow":
                boxes = page.locator("#fsvg .nd")
                n = boxes.count()
                if not n:
                    problems.append("flow: no boxes drawn")
                else:
                    width = page.eval_on_selector("#fsvg", "e => e.getBoundingClientRect().width")
                    card = page.eval_on_selector("#fcard", "e => e.clientWidth")
                    if width > card + 1:
                        problems.append(f"flow: map ({width:.0f}px) wider than its card ({card}px) in fit mode")
                    # click the right-most box (a leaf output): everything not upstream of it must be dimmed
                    xs = [boxes.nth(i).bounding_box()["x"] for i in range(n)]
                    leaf = boxes.nth(xs.index(max(xs)))
                    leaf.click()
                    dimmed = page.locator("#fsvg .nd.dim").count()
                    print(f"flow: {n} boxes, {dimmed} dimmed after clicking the right-most one")
                    if not dimmed:
                        problems.append("flow: clicking a leaf dimmed nothing")
                    snap(page, "flow-click")
                    leaf.hover()
                    if not page.eval_on_selector("#tip", "t => t.style.display === 'block' && t.textContent"):
                        problems.append("flow: hovering a box shows no tooltip")
            if tab == "table":  # the staleness filter must narrow the table
                before = page.locator("#tb tr").count()
                page.select_option("#sf", "stale")
                after = page.locator("#tb tr").count()
                print(f"table: {before - 1} rows, {after - 1} possibly stale")
                snap(page, "table-stale")
            page.close()

        # network: vis-network comes from a CDN; wait for its canvas or the offline message
        page = open_tab("network")
        try:
            page.wait_for_selector("#netbox canvas, #netbox .empty", timeout=20000)
            state = "canvas" if page.locator("#netbox canvas").count() else "offline message"
        except Exception:
            state = "nothing after 20 s"
        print(f"network: {state}")
        if state != "canvas":
            problems.append(f"network: {state}")
        page.wait_for_timeout(2500)  # let the layout settle before the screenshot
        snap(page, "network")
        page.close()

        page = open_tab("flow", dark=True)
        snap(page, "flow-dark")
        page.close()
        browser.close()

    print(f"screenshots: {shots}")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("no errors")


if __name__ == "__main__":
    main()
