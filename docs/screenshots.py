# /// script
# requires-python = ">=3.9"
# dependencies = ["playwright==1.64.1", "imageio-ffmpeg==0.6.0"]
# ///
"""Screenshots of the report for the README, from examples/r-project.

    uv run docs/screenshots.py          # -> docs/img/*.png, docs/img/demo.gif and docs/example-report/

Same browser setup as tests/visual.py (CHROME=... or `uvx playwright@1.64.1 install chromium`).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

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
    for f in ("index.html", "graph.json", "manifest.tsv"):  # the live example on GitHub Pages
        txt = open(os.path.join(proj, "fms-report", f)).read().replace(os.path.realpath(proj), "examples/r-project")
        open(os.path.join(REPO, "docs", "example-report", f), "w").write(txt)
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
        demo(browser, url, proj)
        # ponytail: no Steps shot, the example has one script folder, so it repeats Flow; add one with a bigger example
        browser.close()
    shutil.rmtree(tmp)
    print(f"screenshots: {OUT}")


# a visible pointer: Playwright's video has none
CURSOR = """addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:99999;left:-50px;top:0;width:22px;height:22px;pointer-events:none;' +
    'background:url("data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 22 22"><path d="M2 2v16l4.5-4 3 7 3-1.3-3-6.8h6z" fill="#111" stroke="#fff" stroke-width="1.5"/></svg>') + '")';
  document.body.appendChild(c);
  addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
});"""


def demo(browser, url, proj):
    """docs/img/demo.gif: click a stale figure, then its script, then the Timeline"""
    import imageio_ffmpeg
    vids = tempfile.mkdtemp(prefix="fms-video-")
    size = {"width": W, "height": 720}  # narrower and Flow scrolls once the details panel opens
    ctx = browser.new_context(viewport=size, record_video_dir=vids, record_video_size=size)
    ctx.add_init_script(CURSOR)
    page = ctx.new_page()
    t0 = time.monotonic()
    page.goto(f"{url}#flow", wait_until="load")
    page.evaluate("s => { const e = document.getElementById('sub'); e.textContent = e.textContent.replace(s, 'examples/r-project') }", proj)
    page.mouse.move(W / 2, 450)
    page.wait_for_timeout(300)
    start = time.monotonic() - t0  # cut the blank frames before the page was drawn

    def click(loc, pause):
        b = loc.bounding_box()
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=25)
        page.wait_for_timeout(250)
        page.mouse.down(); page.mouse.up()
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] + 60, steps=8)  # off the box: no tooltip over the panel
        page.wait_for_timeout(pause)

    page.wait_for_timeout(1000)
    click(page.locator("#fsvg .nd", has_text="pca.png"), 3000)       # its upstream chain, the code line, stale
    click(page.locator("#fsvg .nd", has_text="02_model.Rmd"), 2500)  # what a script reads and writes
    click(page.locator("nav button[data-v=timeline]"), 2500)         # the stale output in red
    video = page.video.path()
    ctx.close()
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", video,
                    "-vf", "fps=10,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];"
                           "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                    os.path.join(OUT, "demo.gif")], check=True)
    shutil.rmtree(vids)


if __name__ == "__main__":
    main()
