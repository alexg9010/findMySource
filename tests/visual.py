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
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "examples"))
import set_dates  # noqa: E402


def main():
    report = sys.argv[1] if len(sys.argv) > 1 else None
    if not report:
        # a copy with realistic dates: a fresh checkout dates every file alike, so nothing would be stale
        out = tempfile.mkdtemp(prefix="fms-visual-")
        proj = os.path.join(out, "r-project")
        shutil.copytree(os.path.join(REPO, "examples", "r-project"), proj)
        set_dates.main(proj)
        subprocess.run([sys.executable, "-m", "findmysource", "--out", out], check=True,
                       cwd=proj, env={**os.environ, "PYTHONPATH": REPO})
        report = os.path.join(out, "index.html")
    shots = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join(REPO, "tests", "screenshots"))
    os.makedirs(shots, exist_ok=True)
    url = "file://" + os.path.abspath(report)
    problems = []

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None, args=["--no-sandbox"])
        try:

            def open_tab(tab, dark=False, at=None):  # at: another report than the main one
                page = browser.new_page(viewport={"width": 1500, "height": 950}, color_scheme="dark" if dark else "light")
                label = tab + ("-dark" if dark else "")
                page.on("console", lambda m: m.type == "error" and problems.append(f"{label}: console: {m.text}"))
                page.on("pageerror", lambda e: problems.append(f"{label}: page error: {e}"))
                page.on("requestfailed", lambda r: problems.append(f"{label}: request failed: {r.url} ({r.failure})"))
                # the report must work offline: any request that leaves the file system fails (and is reported above)
                page.route("**/*", lambda r: r.continue_() if r.request.url.startswith("file:") else r.abort())
                page.goto(f"{at or url}#{tab}", wait_until="load")
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
                        if not page.is_visible("#det") or not page.inner_text("#det").strip():
                            problems.append("flow: clicking a box shows no details panel")
                        snap(page, "flow-click")
                        page.click("#fit")  # redraws the map: the selection must stay highlighted
                        if page.locator("#fsvg .nd.dim").count() != dimmed:
                            problems.append("flow: the highlight is lost after the 100% / Fit toggle")
                        page.click("#fit")
                        leaf = page.locator("#fsvg .nd").nth(xs.index(max(xs)))
                        leaf.hover()
                        if not page.eval_on_selector("#tip", "t => t.style.display === 'block' && t.textContent"):
                            problems.append("flow: hovering a box shows no tooltip")
                        page.select_option("#sel", "(all)")
                        grouped = boxes.count()
                        page.uncheck("#fold")
                        ungrouped = boxes.count()
                        print(f"flow (all): {grouped} boxes with folder groups, {ungrouped} without")
                        if ungrouped < grouped:
                            problems.append("flow: ungrouping folders reduced the number of boxes")
                        snap(page, "flow-ungrouped")
                if tab == "timeline":  # no two outputs of one script may share a spot, or one of them cannot be clicked
                    spots = page.eval_on_selector_all("#tsvg circle", "cs => cs.map(c => [+c.getAttribute('cy'), +c.getAttribute('cx'), +c.getAttribute('r')])")
                    for (y1, x1, r1), (y2, x2, r2) in zip(sorted(spots), sorted(spots)[1:]):
                        if y1 == y2 and x2 - x1 < r1 + r2 - 0.01:
                            problems.append(f"timeline: markers overlap at y={y1}")
                if tab == "flow":  # keyboard only: Enter on a focused box selects it
                    page.focus("#fsvg .nd >> nth=0")
                    page.keyboard.press("Enter")
                    if not page.is_visible("#det"):
                        problems.append("flow: Enter on a focused box shows no details panel")
                if tab == "table":  # the staleness filter must narrow the table
                    before = page.locator("#tb tr").count()
                    page.select_option("#sf", "stale")
                    after = page.locator("#tb tr").count()
                    print(f"table: {before - 1} rows, {after - 1} possibly stale")
                    snap(page, "table-stale")
                    page.click("#tb tr[data-o] >> nth=0")  # a row opens Flow with the file's details
                    if not page.is_visible("#flow") or not page.is_visible("#det"):
                        problems.append("table: clicking a row does not open it in Flow")
                    snap(page, "table-to-flow")
                page.close()

            page = open_tab("network")
            boxes = page.locator("#nsvg .nd")
            collapsed = boxes.count()
            snap(page, "steps")
            page.click("#nexp")
            expanded = boxes.count()
            print(f"steps: {collapsed} boxes collapsed, {expanded} expanded")
            if not collapsed:
                problems.append("steps: no boxes drawn")
            elif expanded <= collapsed:
                problems.append("steps: 'expand all' did not add boxes")
            snap(page, "steps-expanded")
            page.close()

            page = open_tab("unmatched")  # folder tree: opening a folder draws its sub-folders
            rows = page.locator("#utree summary")
            n0 = rows.count()
            if n0 and page.locator("#utree summary:not(.leaf)").count():
                page.click("#utree summary:not(.leaf) >> nth=0")
                page.wait_for_timeout(300)  # the toggle event that draws sub-folders is asynchronous
            print(f"unmatched: {n0} top folders, {rows.count()} after opening the first")
            if not n0 and "every output" not in page.inner_text("#utree"):
                problems.append("unmatched: no folders drawn")
            snap(page, "unmatched")
            page.close()

            # served over http (e.g. GitHub Pages) the report must not offer file:// links to the author's disk
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
                type("Q", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=os.path.dirname(report)))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            page.goto(f"http://127.0.0.1:{srv.server_port}/{os.path.basename(report)}#flow", wait_until="load")
            page.click("#fsvg .nd >> nth=0")
            if page.locator("#det a[href^='file:']").count() or not page.locator("#det a[data-copy]").count():
                problems.append("flow: a hosted report offers file:// links or no copy path")
            page.evaluate("delete Navigator.prototype.clipboard")  # plain http has no clipboard API
            page.click("#det a[data-copy]")
            if page.inner_text("#det a[data-copy]") != page.get_attribute("#det a[data-copy]", "data-copy"):
                problems.append("flow: without a clipboard, copy path does not show the path")
            page.close()
            srv.shutdown()

            page = open_tab("flow", dark=True)
            snap(page, "flow-dark")
            page.close()

            # a script writing >15 files into one folder gets a folder group; expanding a selected group keeps its trace
            gproj = os.path.join(tempfile.mkdtemp(prefix="fms-group-"), "p")
            os.makedirs(os.path.join(gproj, "scripts")), os.makedirs(os.path.join(gproj, "out"))
            with open(os.path.join(gproj, "fms.json"), "w") as f:
                f.write('{"scripts": ["scripts"], "outputs": ["out"]}')
            with open(os.path.join(gproj, "scripts", "make.R"), "w") as f:
                f.write("".join(f'write.csv(x, "out/f{i:02d}.csv")\n' for i in range(16)))
            with open(os.path.join(gproj, "scripts", "make.R.py"), "w") as f:  # its path contains make.R: the group must not list it
                f.write('open("out/other.csv", "w")\n')
            open(os.path.join(gproj, "out", "other.csv"), "w").close()
            for i in range(16):
                open(os.path.join(gproj, "out", f"f{i:02d}.csv"), "w").close()
            subprocess.run([sys.executable, "-m", "findmysource", "--out", os.path.join(gproj, "rep")], check=True,
                           cwd=gproj, env={**os.environ, "PYTHONPATH": REPO}, stdout=subprocess.DEVNULL)
            page = open_tab("flow", at="file://" + os.path.join(gproj, "rep", "index.html"))
            page.select_option("#sel", "(all)")
            page.click("#fsvg .nd:has-text('files)')")
            page.uncheck("#fold")
            if not page.locator("#fsvg .ed.hl").count():  # everything here is upstream/downstream, so check highlighted edges, not dimmed boxes
                problems.append("flow: the trace is lost when a selected folder group is expanded")
            page.close()
            page = open_tab("table", at="file://" + os.path.join(gproj, "rep", "index.html"))
            page.select_option("#sf", "stale")  # a filter left on (nothing is stale here) must not hide the group's outputs
            page.click("nav button[data-v=timeline]")
            page.click("#tsvg [data-s]")  # 16 outputs written at once: one marker that lists them in Table
            if page.locator("#tb tr[data-o]").count() != 16:
                problems.append("timeline: a grouped marker does not list its outputs in Table")
            page.close()

        except Exception as e:  # report what the page logged before the crash, not only the timeout
            problems.append(f"check stopped: {str(e).splitlines()[0]}")
        browser.close()

    print(f"screenshots: {shots}")
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("no errors")


if __name__ == "__main__":
    main()
