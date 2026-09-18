#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""headless Chrome を使った描画スモークテスト。

Python 側のテスト (webtest) は JavaScript の実行結果を検証できない。
v0.2.0 ではテンプレートの JS コメント内に ``</script>`` があり、
v0.3.0 開発中には main.js の ``var config = {}`` がページ埋め込みデータを
上書きする、という「pytest では絶対に検知できない」全テーブル空描画バグが
2 回連続で発生した。本テストは実際のブラウザでページを描画し、
tbody にデータ行が現れることを確認する。

headless Chrome / Chromium が無い環境ではスキップする。
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from html.parser import HTMLParser
from typing import Iterator

import pytest
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from dnsmasq_webconf.app import Settings, create_app

CONFIG_BODY = (
    '# handwritten comment\n'
    'dhcp-range=192.168.1.100,192.168.1.200,12h\n'
    'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.10 # office printer\n'
    'dhcp-host=aa:bb:cc:dd:ee:02, nas, 192.168.1.11\n'
)
LEASES_BODY = (
    '1700000000 aa:bb:cc:dd:ee:05 192.168.1.50 laptop 01:aa:bb:cc:dd:ee:05\n'
    '1700000300 aa:bb:cc:dd:ee:06 192.168.1.51 phone 01:aa:bb:cc:dd:ee:06\n'
)
HOSTS_BODY = '127.0.0.1\tlocalhost\n192.168.1.1\trouter\n'


def _find_browser() -> str | None:
    """headless として使えるブラウザの実行パスを返す (無ければ None)。"""
    for name in (
        'google-chrome',
        'google-chrome-stable',
        'chromium',
        'chromium-browser',
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


class _QuietHandler(WSGIRequestHandler):
    """アクセスログを stderr に出さないハンドラ。"""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class _TableRowParser(HTMLParser):
    """thead / tbody の行数を数えるパーサー。"""

    def __init__(self) -> None:
        super().__init__()
        self.in_tbody = False
        self.in_script = 0
        self.tbody_rows = 0
        self.script_open = 0
        self.script_close = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'script':
            self.script_open += 1
            self.in_script += 1
        elif tag == 'tbody':
            self.in_tbody = True
        elif tag == 'tr' and self.in_tbody:
            self.tbody_rows += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == 'script':
            self.script_close += 1
            self.in_script = max(0, self.in_script - 1)
        elif tag == 'tbody':
            self.in_tbody = False


@pytest.fixture
def server(tmp_path) -> Iterator[str]:
    """テスト用アプリをエフェメラルポートで起動し、ベース URL を返す。"""
    config = tmp_path / 'dnsmasq.more.conf'
    leases = tmp_path / 'dnsmasq.leases'
    hosts = tmp_path / 'hosts'
    config.write_text(CONFIG_BODY, encoding='utf-8')
    leases.write_text(LEASES_BODY, encoding='utf-8')
    hosts.write_text(HOSTS_BODY, encoding='utf-8')

    settings = Settings(
        hosts_file=str(hosts),
        leases_file=str(leases),
        config_file=str(config),
        refresh_interval=0,
    )
    httpd = make_server('127.0.0.1', 0, create_app(settings),
                        server_class=WSGIServer, handler_class=_QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}/'
    finally:
        httpd.shutdown()


@pytest.mark.skipif(_find_browser() is None, reason='headless browser not available')
class TestBrowserSmoke:
    """実際のブラウザで描画させるスモークテスト。"""

    def test_tables_render_rows(self, server: str, tmp_path) -> None:
        """ページを開くと各テーブルの tbody にデータ行が描画される。"""
        browser = _find_browser()
        assert browser is not None
        dom = subprocess.run(
            [
                browser, '--headless', '--disable-gpu', '--no-sandbox',
                '--virtual-time-budget=5000', '--dump-dom', server,
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout

        parser = _TableRowParser()
        parser.feed(dom)

        # script タグの開始・終了が対になっていること (v0.2.0 の回帰防止)
        assert parser.script_open == parser.script_close, (
            'script ブロックの開始と終了が一致しない '
            f'(open={parser.script_open}, close={parser.script_close})'
        )
        # 埋め込みデータが JS 上書きで消えていないこと (var 初期化子の回帰防止)。
        # tbody 行数: dhcp-hosts 2 + dhcp-leases 2 + ignored 0 + system-hosts 2 = 6
        assert parser.tbody_rows >= 6, (
            f'table rows were not rendered (tbody rows = {parser.tbody_rows}); '
            'embedded data may be clobbered or scripts failed to run'
        )
        # ページ生成物は一時領域に残さないため、ダンプを検査用に保管する
        (tmp_path / 'rendered.html').write_text(dom, encoding='utf-8')