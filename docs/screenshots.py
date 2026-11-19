# /// script
# requires-python = ">=3.9"
# dependencies = ["playwright==1.64.1"]
# ///
"""Screenshots of the report for the README, from examples/r-project.

    uv run docs/screenshots.py          # -> docs/img/*.png

Same browser setup as tests/visual.py (CHROME=... or `uvx playwright@1.64.1 install chromium`).
"""
import os
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "examples"))
import set_dates  # noqa: E402

OUT = os.path.join(REPO, "docs", "img")
W = 1600  # wide enough that Flow fits the whole map next to the details panel (it scrolls below 45% zoom)


def main():
    tmp = tempfile.mkdtemp(prefix="fms-docs-")
    proj = os.path.join(tmp, "r-project")
    shutil.copytree(os.path.join(REPO, "examples", "r-project"), proj)
    set_dates.main(proj)  # realistic dates, so one output is possibly stale
    subprocess.run([sys.executable, "-m", "findmysource", "--out", "fms-report"], cwd=proj, check=True,
                   env={**os.environ, "PYTHONPATH": REPO}, stdout=subprocess.DEVNULL)
    url = "file://" + os.path.join(proj, "fms-report", "index.html")
    os.makedirs(OUT, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=["--no-sandbox"])

        def open_tab(tab):
            page = browser.new_page(viewport={"width": W, "height": 900}, device_scale_factor=1.6)
            page.goto(f"{url}#{tab}", wait_until="load")
            # show the project as it is in the repo, not the temporary copy
            page.evaluate("s => { const e = document.getElementById('sub'); e.textContent = e.textContent.replace(s, 'examples/r-project') }", proj)
            return page

        def shot(page, name, bottom):
            """Header to the bottom edge of `bottom`."""
            box = page.locator(bottom).bounding_box()
            page.mouse.move(0, 0)  # no tooltip
            page.screenshot(path=os.path.join(OUT, f"{name}.png"),
                            clip={"x": 0, "y": 0, "width": W, "height": box["y"] + box["height"] + 16})

        page = open_tab("flow")  # click a stale figure: its upstream chain lights up, details on the right
        page.locator("#fsvg .nd", has_text="pca.png").click()
        shot(page, "flow", ".fwrap")
        page.close()

        page = open_tab("timeline")
        shot(page, "timeline", "#timeline .card")
        page.close()

        page = open_tab("table")
        shot(page, "table", "#tb")
        page.close()
        # ponytail: no Steps shot, the example has one script folder, so it repeats Flow; add one with a bigger example
        browser.close()
    shutil.rmtree(tmp)
    print(f"screenshots: {OUT}")


if __name__ == "__main__":
    main()
