"""Serve the page a run wrote, so the operator can look at it in a browser.

Opening generated code in a browser is a boundary decision, the same way running it
is, so this stays off until the operator turns it on with AXIOM_ALLOW_PREVIEW=1.

Two things make it narrower than it looks. It serves one task's workspace at a time
on 127.0.0.1, with directory listing refused, so it hands out the page the run wrote
and not the disk around it. And it listens on its own port, which puts the generated
page on a different origin from this API: a script inside it cannot read this
backend's responses, and a JSON POST to it fails the CORS preflight.

None of that is a sandbox. The page runs in the operator's browser with whatever
access that browser gives it.
"""

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PREVIEW_PORT = 8100


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, request, client_address, server, *args, **kwargs):
        # The root is read per request from the server, so selecting another task
        # switches what is served without restarting anything. It has to come from the
        # server argument: self.server is not set until the base class runs.
        super().__init__(request, client_address, server, *args,
                         directory=str(server.preview_root), **kwargs)

    def log_message(self, *args):
        pass

    def list_directory(self, path):
        self.send_error(403, "Directory listing is off.")
        return None


class Preview:
    """One loopback static server, pointed at whichever task asked for it."""

    def __init__(self, port=DEFAULT_PREVIEW_PORT):
        self.port = port
        self.server = None

    @property
    def running(self):
        return self.server is not None

    def _start(self):
        if self.server is not None:
            return
        for candidate in (self.port, 0):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", candidate), _Handler)
            except OSError:
                continue
            server.preview_root = Path.cwd()
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.server = server
            return
        raise ValueError("No loopback port was free for the preview server.")

    def select(self, root):
        """Point the server at a workspace and return the address to open."""
        root = Path(root)
        self._start()
        self.server.preview_root = root
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def stop(self):
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.server = None
