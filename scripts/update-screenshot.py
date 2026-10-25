#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate the README screenshots from demo data with headless Chrome.

Starts the app on an ephemeral loopback port with a made-up config, lease and
hosts file, then captures the main page and the text editor page (#19).
Run it after a UI change, or let the release workflow do it on each tag.

Usage: python scripts/update-screenshot.py [--output-dir docs]
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zlib
from pathlib import Path
from typing import Any, Callable
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from dnsmasq_webconf.app import Settings, create_app

REPO_ROOT = Path(__file__).resolve().parent.parent

# Demo data: a few reservations (some with comments, one commented out),
# an ignored MAC, live leases, and a hosts file, so every table has rows
CONFIG_BODY = """\
# Hand-written lines like these are kept exactly as they are
dhcp-range=192.168.1.100,192.168.1.200,12h
dhcp-option=option:router,192.168.1.1

# Static reservations
dhcp-host=aa:bb:cc:01:5a:10, printer, 192.168.1.10, infinite # office printer
dhcp-host=aa:bb:cc:01:5a:11, nas, 192.168.1.11, 12h # nightly backups
dhcp-host=aa:bb:cc:01:5a:12, workbench, 192.168.1.12
#dhcp-host=aa:bb:cc:01:5a:13, oldbox, 192.168.1.13 # retired
dhcp-host=aa:bb:cc:01:5a:66, ignore # unknown device
"""
LEASES_BODY = """\
1792908800 aa:bb:cc:01:5a:30 192.168.1.150 laptop 01:aa:bb:cc:01:5a:30
1792909100 aa:bb:cc:01:5a:31 192.168.1.151 phone 01:aa:bb:cc:01:5a:31
1792909400 aa:bb:cc:01:5a:32 192.168.1.152 tablet 01:aa:bb:cc:01:5a:32
"""
HOSTS_BODY = """\
127.0.0.1\tlocalhost
192.168.1.1\trouter gateway
"""

# Loads the page in a frame of the capture width and writes the height of its
# content into the DOM, for --dump-dom to read back. The frame is kept short
# so that scrollHeight is the content height, not the frame height. It also
# reports its own viewport height (see PROBE_WINDOW_HEIGHT).
PROBE_PATH = '/__screenshot-probe'
PROBE_HTML = """\
<!doctype html>
<iframe id="page" src="{url}" style="width:{width}px;height:100px;border:0"></iframe>
<script>
  document.body.setAttribute('data-viewport', String(window.innerHeight));
  document.getElementById('page').onload = function () {{
    var root = this.contentDocument.documentElement;
    root.style.overflow = 'hidden';  // a scrollbar would narrow the layout
    // jQuery 3 runs ready handlers asynchronously, so the tables may still be
    // empty at load time; measure once they are filled
    setTimeout(function () {{
      document.body.setAttribute('data-height', String(root.scrollHeight));
    }}, 1000);
  }};
</script>
"""
PROBE_HEIGHT = re.compile(r'data-height="(\d+)"')
PROBE_VIEWPORT = re.compile(r'data-viewport="(\d+)"')
# The new headless Chrome lays pages out in a viewport shorter than
# --window-size (by 87px in Chrome 144) but still writes a window-sized image,
# blank at the bottom. The probe window has this height, so the difference
# from its viewport tells how much taller to make the capture window.
PROBE_WINDOW_HEIGHT = 1000

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
# Number of samples per pixel for each PNG color type
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

BROWSERS = ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser')


class _QuietHandler(WSGIRequestHandler):
    """Handler that does not write access logs to stderr."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def find_browser() -> str | None:
    """Return the path of a browser usable in headless mode (None if none)."""
    for name in BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    return None


def with_probe(app: Callable[..., Any], width: int) -> Callable[..., Any]:
    """Wrap a WSGI app so that ``PROBE_PATH?url=...`` serves the probe page.

    Args:
        app: The application under capture.
        width: Width of the frame the page is measured in.

    Returns:
        A WSGI application that serves the probe page and defers to ``app`` otherwise.
    """
    def wrapped(environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        if environ.get('PATH_INFO') != PROBE_PATH:
            return app(environ, start_response)
        query = urllib.parse.parse_qs(environ.get('QUERY_STRING', ''))
        url = html.escape(query.get('url', ['/'])[0])
        body = PROBE_HTML.format(url=url, width=width).encode('utf-8')
        start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
        return [body]

    return wrapped


def measure(browser: str, base: str, path: str) -> tuple[int, int]:
    """Measure a page and the browser's viewport, as laid out by the browser.

    Args:
        browser: Path of the Chrome / Chromium executable.
        base: Base URL of the server (ending in ``/``).
        path: Path of the page to measure (``/`` for the main page).

    Returns:
        The content height of ``path``, and how much shorter the viewport is
        than the window, both in pixels.
    """
    probe = f'{base.rstrip("/")}{PROBE_PATH}?{urllib.parse.urlencode({"url": path})}'
    dom = subprocess.run(
        [
            browser, '--headless', '--disable-gpu', '--no-sandbox',
            '--virtual-time-budget=5000',
            f'--window-size=800,{PROBE_WINDOW_HEIGHT}',
            '--dump-dom', probe,
        ],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    height = PROBE_HEIGHT.search(dom)
    viewport = PROBE_VIEWPORT.search(dom)
    if height is None or viewport is None:
        raise RuntimeError(f'could not measure the height of {path}')
    return int(height.group(1)), PROBE_WINDOW_HEIGHT - int(viewport.group(1))


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    """Encode one PNG chunk (length, type, data, CRC)."""
    return (struct.pack('>I', len(body)) + kind + body
            + struct.pack('>I', zlib.crc32(kind + body)))


def crop_png_height(path: Path, height: int) -> None:
    """Cut a PNG file down to its top ``height`` rows, in place.

    Scanline filters only refer to the rows above, so the kept rows can be
    copied as they are without decoding the pixels. This avoids depending on
    an image library just to drop the blank band headless Chrome leaves.

    Args:
        path: PNG file to crop (non-interlaced, as Chrome writes them).
        height: Number of rows to keep. Nothing is done if the image is not taller.
    """
    data = path.read_bytes()
    if data[:8] != PNG_SIGNATURE:
        raise ValueError(f'{path} is not a PNG file')
    chunks: list[tuple[bytes, bytes]] = []
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        chunks.append((data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]))
        pos += 12 + length

    ihdr = chunks[0][1]
    width, old_height, depth, color, compression, filtering, interlace = (
        struct.unpack('>IIBBBBB', ihdr))
    if height >= old_height:
        return
    if interlace:
        raise ValueError(f'{path} is interlaced; cannot crop it row by row')
    # One filter-type byte, then the samples of each row
    stride = 1 + (width * PNG_CHANNELS[color] * depth + 7) // 8
    pixels = zlib.decompress(b''.join(body for kind, body in chunks if kind == b'IDAT'))

    out = [PNG_SIGNATURE, _png_chunk(b'IHDR', struct.pack(
        '>IIBBBBB', width, height, depth, color, compression, filtering, interlace))]
    # Keep the ancillary chunks (color space, resolution, ...) that precede the image data
    for kind, body in chunks[1:]:
        if kind == b'IDAT':
            break
        out.append(_png_chunk(kind, body))
    out.append(_png_chunk(b'IDAT', zlib.compress(pixels[:stride * height], 9)))
    out.append(_png_chunk(b'IEND', b''))
    path.write_bytes(b''.join(out))


def capture(browser: str, url: str, output: Path, width: int, height: int,
            chrome_height: int) -> None:
    """Save a screenshot of ``url`` as a PNG of ``width`` x ``height`` pixels.

    Args:
        browser: Path of the Chrome / Chromium executable.
        url: Page to capture.
        output: Destination PNG file.
        width: Viewport width (at least 500; headless Chrome lays out narrower
            windows at 500px and crops the image).
        height: Viewport height. Content below it is cut off.
        chrome_height: How much shorter the viewport is than the window
            (from :func:`measure`); the blank band it leaves is cropped off.
    """
    subprocess.run(
        [
            browser, '--headless', '--disable-gpu', '--no-sandbox',
            '--hide-scrollbars', '--force-device-scale-factor=1',
            # Give main.js time to fill the tables before the capture
            '--virtual-time-budget=5000',
            f'--window-size={width},{height + chrome_height}',
            f'--screenshot={output}',
            url,
        ],
        # Chrome prints its own noise on stderr; keep it out of the way
        capture_output=True, timeout=60, check=True,
    )
    if not output.is_file():
        raise RuntimeError(f'browser did not write {output}')
    crop_png_height(output, height)


def main(argv: list[str] | None = None) -> int:
    """Entry point of the screenshot generator."""
    parser = argparse.ArgumentParser(
        description='Regenerate the README screenshots from demo data with headless Chrome.',
    )
    parser.add_argument(
        '--output-dir', type=Path, default=REPO_ROOT / 'docs',
        help='directory to write screenshot.png and screenshot-editor.png (default: docs/)',
    )
    parser.add_argument('--browser', help='Chrome / Chromium executable (default: search PATH)')
    parser.add_argument('--width', type=int, default=1400, help='viewport width (default: 1400)')
    parser.add_argument('--height', type=int,
                        help='viewport height of the main page (default: fit the whole page)')
    parser.add_argument('--editor-height', type=int, default=420,
                        help='viewport height of the text editor page (default: 420)')
    args = parser.parse_args(argv)

    browser = args.browser or find_browser()
    if browser is None:
        parser.error('no Chrome / Chromium found; pass --browser')
    output_dir: Path = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Lease times are formatted in the server's local time zone; pin it so the
    # image does not depend on where it was generated
    os.environ['TZ'] = 'UTC'
    time.tzset()

    with tempfile.TemporaryDirectory() as workdir:
        # The page shows the file paths as given, so use short relative names
        # instead of the temporary directory's path
        os.chdir(workdir)
        Path('dnsmasq.more.conf').write_text(CONFIG_BODY, encoding='utf-8')
        Path('dnsmasq.leases').write_text(LEASES_BODY, encoding='utf-8')
        Path('hosts').write_text(HOSTS_BODY, encoding='utf-8')
        settings = Settings(
            hosts_file='hosts',
            leases_file='dnsmasq.leases',
            config_file='dnsmasq.more.conf',
            refresh_interval=0,
        )
        httpd = make_server('127.0.0.1', 0, with_probe(create_app(settings), args.width),
                            server_class=WSGIServer, handler_class=_QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{httpd.server_port}/'
        try:
            # The main page grows with its tables; the editor fills the window
            page_height, chrome_height = measure(browser, base, '/')
            height = args.height or page_height
            shots = [
                (base, output_dir / 'screenshot.png', height),
                (base + 'edit', output_dir / 'screenshot-editor.png', args.editor_height),
            ]
            for url, output, shot_height in shots:
                capture(browser, url, output, args.width, shot_height, chrome_height)
                print(f'wrote {output}')
        finally:
            httpd.shutdown()
            # Leave the temporary directory so it can be removed
            os.chdir(REPO_ROOT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
