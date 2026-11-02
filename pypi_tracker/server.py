"""A local preview server that keeps the dashboard up to date. Not meant for publishing a site."""

import sys
import time
from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

from .errors import TrackerError
from .reports import _SITE_FILES
from .tracker import Tracker

_CHECK_EVERY_SECONDS = 1
_RELOAD_PATH = "/__reload"
# Added only to pages served here, so published files never contain it.
_RELOAD_SCRIPT = """<script>
setInterval(async () => {
  try {
    const response = await fetch("%s");
    if ((await response.text()) !== "%s") location.reload();
  } catch {
    // the server stopped; start reloading again if it comes back
  }
}, 1000);
</script>
"""


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == _RELOAD_PATH:
            self._send_text(str(self.server.version), "text/plain")
        elif path in ("/", "/index.html"):
            page = (Path(self.directory) / "index.html").read_text(encoding="utf-8")
            script = _RELOAD_SCRIPT % (_RELOAD_PATH, self.server.version)
            self._send_text(page.replace("</body>", f"{script}</body>"), "text/html; charset=utf-8")
        else:
            super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # so a reload always shows the newest files
        super().end_headers()

    def log_request(self, code="-", size="-"):
        # Successful requests would flood the terminal with the page's reload checks.
        if int(code) >= 400:
            super().log_request(code, size)

    def log_error(self, format, *args):
        pass  # log_request already reports the failure, and it names the path

    def _send_text(self, text: str, content_type: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Watcher:
    """Regenerates the site when its inputs change, and tells open pages to reload."""

    def __init__(
        self, server: ThreadingHTTPServer, load: Callable[[], Tracker], tracker: Tracker, config: Path
    ):
        self.server = server
        self.load = load
        self.tracker = tracker
        self.config = config
        self.seen = self._inputs()

    def run(self) -> None:
        while True:
            time.sleep(_CHECK_EVERY_SECONDS)
            self.check()

    def check(self) -> None:
        current = self._inputs()
        if current == self.seen:
            return
        self.seen = current
        try:
            # Reloaded each time, so edits to the config take effect without a restart.
            self.tracker = self.load()
            changed = self.tracker.dashboard()
        except (TrackerError, OSError) as error:
            # Usually a half-finished edit, so keep serving the last good site.
            print(f"error: {error}", file=sys.stderr, flush=True)
            return
        self.seen = self._inputs()  # the new config may name other files to watch
        if changed:
            self.server.version += 1
            print(f"regenerated, {len(changed)} file(s) changed", flush=True)

    def _inputs(self) -> dict[Path, int]:
        """When each file the site is generated from last changed."""
        site = self.tracker.site
        paths = [self.config, *self.tracker.data_dir.glob("*.csv"), *_SITE_FILES.iterdir()]
        if site.logo is not None:
            paths.append(Path(site.logo))
        times = {}
        for path in paths:
            try:
                times[path] = path.stat().st_mtime_ns
            except OSError:
                pass  # a missing file counts as a change only if it was there before
        return times


def make_server(port: int, output_dir: Path) -> ThreadingHTTPServer:
    # Only this computer can connect: this is a preview, not a public web server.
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(_Handler, directory=str(output_dir)))
    server.version = 0
    return server


def serve(load: Callable[[], Tracker], config: Path, port: int) -> None:
    """Generate the site, serve it, and keep it up to date until interrupted."""
    tracker = load()
    tracker.dashboard()
    # shortcut: changing the output directory while running isn't followed, restart to pick it up.
    server = make_server(port, tracker.output_dir)
    Thread(target=_Watcher(server, load, tracker, config).run, daemon=True).start()
    print(f"serving {tracker.output_dir} at http://localhost:{server.server_address[1]}/", flush=True)
    print("watching for changes, press Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
