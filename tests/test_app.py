#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web アプリケーション層 (ルーティング・認証・CSRF・エスケープ) のテスト。"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest
from webtest import TestApp

import dnsmasq_webconf
from dnsmasq_webconf.app import Settings, create_app, is_loopback, to_embedded_json
from dnsmasq_webconf.auth import Credentials

CONFIG_BODY = (
    '# 手書きのコメント\n'
    'dhcp-range=192.168.1.100,192.168.1.200,12h\n'
    'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.10 # office printer\n'
)
LEASES_BODY = '1700000000 aa:bb:cc:dd:ee:05 192.168.1.50 laptop 01:aa:bb\n'
HOSTS_BODY = '127.0.0.1\tlocalhost\n'


def basic_auth_header(user: str, password: str) -> dict[str, str]:
    """BASIC 認証ヘッダを組み立てる。

    Args:
        user: ユーザー名。
        password: パスワード。

    Returns:
        ``Authorization`` ヘッダを含む辞書。
    """
    token = base64.b64encode(f'{user}:{password}'.encode('utf-8')).decode('ascii')
    return {'Authorization': f'Basic {token}'}


@pytest.fixture
def files(tmp_path):
    """テスト用の設定・リース・hosts ファイルを用意する。"""
    config = tmp_path / 'dnsmasq.more.conf'
    leases = tmp_path / 'dnsmasq.leases'
    hosts = tmp_path / 'hosts'
    config.write_text(CONFIG_BODY, encoding='utf-8')
    leases.write_text(LEASES_BODY, encoding='utf-8')
    hosts.write_text(HOSTS_BODY, encoding='utf-8')
    return {'config': config, 'leases': leases, 'hosts': hosts}


def make_app(files, **kwargs) -> TestApp:
    """テスト用の WSGI アプリを生成する。

    Args:
        files: :func:`files` フィクスチャの戻り値。
        **kwargs: :class:`Settings` への追加引数。

    Returns:
        webtest の :class:`TestApp`。
    """
    settings = Settings(
        hosts_file=str(files['hosts']),
        leases_file=str(files['leases']),
        config_file=str(files['config']),
        **kwargs,
    )
    return TestApp(create_app(settings))


class TestIndex:
    """メイン画面の描画を確認する。"""

    def test_renders_successfully(self, files) -> None:
        res = make_app(files).get('/')
        assert res.status_code == 200
        assert 'Dnsmasq Configurator' in res.text

    def test_serves_bundled_assets_not_cdn(self, files) -> None:
        """閉域 LAN で動作させるため、外部 CDN を参照しない。"""
        res = make_app(files).get('/')
        assert '/static/vendor/jquery.min.js' in res.text
        assert 'cdn' not in res.text.lower()
        assert 'http://code.jquery.com' not in res.text

    def test_bundled_assets_are_reachable(self, files) -> None:
        app = make_app(files)
        assert app.get('/static/vendor/jquery.min.js').status_code == 200
        assert app.get('/static/vendor/bootstrap.min.css').status_code == 200
        assert app.get('/static/main.js').status_code == 200

    def test_missing_files_do_not_break_page(self, tmp_path) -> None:
        """対象ファイルが無くても 500 にならず、該当セクションを描画しない。"""
        settings = Settings(
            hosts_file=str(tmp_path / 'nope'),
            leases_file=str(tmp_path / 'nope'),
            config_file=str(tmp_path / 'nope'),
        )
        res = TestApp(create_app(settings)).get('/')
        assert res.status_code == 200
        # 文字列 "null" が真と評価されてセクションが出てしまう旧バグの回帰テスト
        assert 'DHCP Leases' not in res.text
        assert "System's hosts file" not in res.text

    def test_read_only_hides_save_controls(self, files) -> None:
        res = make_app(files, read_only=True).get('/')
        assert 'save-hosts' not in res.text
        assert 'add-host' not in res.text


class TestEscaping:
    """信頼できない入力の埋め込み時のエスケープを確認する。"""

    def test_script_tag_in_lease_name_cannot_escape(self, files) -> None:
        """DHCP クライアントが申告するホスト名は信頼できない入力である。"""
        files['leases'].write_text(
            '1700000000 aa:bb:cc:dd:ee:09 192.168.1.51 '
            '</script><script>alert(1)</script> 01:xx\n',
            encoding='utf-8',
        )
        res = make_app(files).get('/')
        # script ブロックを閉じる文字列がそのまま出力されていないこと
        assert '</script><script>alert(1)' not in res.text
        assert '\\u003c/script\\u003e' in res.text

    def test_inline_json_is_inside_script_block(self, files) -> None:
        """テンプレート自身のコメントに script 終端文字列があってはならない。

        v0.2.0 では JS コメント中に "</script>" という文字列があり、
        ブラウザで script ブロックがそこで閉じて全テーブルが空になった。
        """
        res = make_app(files).get('/')
        assert res.text.count('<script') == res.text.count('</script>')
        # インライン (src なし) の script ブロック内に終端タグが混入していないこと
        inline_open = res.text.rindex('<script>')
        json_pos = res.text.index('var system_hosts = ')
        assert '</script>' not in res.text[inline_open:json_pos]

    def test_main_js_does_not_clobber_embedded_data(self) -> None:
        """main.js がページ埋め込みデータのグローバルを上書きしないこと。

        インラインスクリプトは main.js の前に読み込まれる。main.js 側に
        "var config = {}" のような初期化子付き宣言があると、埋め込んだ
        データが空のデフォルト値で上書きされ全テーブルが空になる。
        """
        js = (Path(dnsmasq_webconf.__file__).parent / 'static' / 'main.js').read_text(
            encoding='utf-8'
        )
        for name in ('system_hosts', 'leases', 'config', 'read_only', 'refresh_interval'):
            assert not re.search(rf'var\s+{name}\s*=', js), name

    def test_to_embedded_json_escapes_dangerous_characters(self) -> None:
        text = to_embedded_json({'name': '</script>&<>'})
        for char in ('<', '>', '&'):
            assert char not in text

    def test_to_embedded_json_escapes_line_separators(self) -> None:
        """U+2028/U+2029 は JS では行終端として解釈されるため潰す。"""
        text = to_embedded_json({'name': 'a\u2028b\u2029c'})
        assert '\u2028' not in text and '\u2029' not in text

    def test_to_embedded_json_survives_lone_surrogates(self) -> None:
        """不正バイト由来の単独サロゲートでレスポンス生成が 500 にならない。"""
        text = to_embedded_json({'line': 'dhcp-host=x, \udcff, 1.2.3.4\n'})
        assert '\udcff' not in text  # \uXXXX にエスケープされている
        assert json.loads(text)['line'].endswith('1.2.3.4\n')

    def test_to_embedded_json_round_trips(self) -> None:
        original = {'name': 'テスト</script>', 'n': 1}
        assert json.loads(to_embedded_json(original)) == original


class TestAuthentication:
    """BASIC 認証の適用範囲を確認する。"""

    @pytest.fixture
    def auth_app(self, files) -> TestApp:
        return make_app(files, credentials=Credentials('admin', 'secret'))

    def test_index_requires_auth(self, auth_app) -> None:
        assert auth_app.get('/', status=401).status_code == 401

    def test_save_requires_auth(self, auth_app) -> None:
        """無認証で設定を書き換えられた旧実装の回帰テスト。"""
        res = auth_app.post_json('/api/save', {'hosts': [], 'ignored_hosts': []},
                                 status=401)
        assert res.status_code == 401

    def test_valid_credentials_are_accepted(self, auth_app) -> None:
        res = auth_app.get('/', headers=basic_auth_header('admin', 'secret'))
        assert res.status_code == 200

    def test_wrong_credentials_are_rejected(self, auth_app) -> None:
        auth_app.get('/', headers=basic_auth_header('admin', 'wrong'), status=401)

    def test_challenge_header_is_returned(self, auth_app) -> None:
        res = auth_app.get('/', status=401)
        assert 'Basic' in res.headers.get('WWW-Authenticate', '')

    def test_no_auth_configured_allows_access(self, files) -> None:
        assert make_app(files).get('/').status_code == 200


class TestCsrf:
    """CSRF 対策 (Origin 検証・メソッド制限) を確認する。"""

    def test_cross_origin_post_is_rejected(self, files) -> None:
        """BASIC 認証はブラウザが自動送信するため、Origin 検証が必要。"""
        app = make_app(files)
        res = app.post_json(
            '/api/save', {'hosts': [], 'ignored_hosts': []},
            headers={'Origin': 'http://evil.example'}, status=403,
        )
        assert res.status_code == 403

    def test_same_origin_post_is_accepted(self, files) -> None:
        app = make_app(files)
        res = app.post_json(
            '/api/save', {'hosts': [], 'ignored_hosts': []},
            headers={'Origin': 'http://localhost:80'},
        )
        assert res.status_code == 200

    def test_get_is_not_allowed_for_save(self, files) -> None:
        """旧実装は GET を受理しており CSRF が成立していた。"""
        make_app(files).get('/api/save', status=405)


class TestSave:
    """保存 API の動作を確認する。"""

    def test_appends_new_host(self, files) -> None:
        app = make_app(files)
        res = app.post_json('/api/save', {
            'hosts': [{
                'changed': True, 'appended': True, 'valid': True,
                'name': 'newbox', 'addr': '192.168.1.77',
                'mac': ['aa:bb:cc:dd:ee:07'], 'extra': [],
            }],
            'ignored_hosts': [],
        })
        assert res.json['status'] == 'OK'
        assert res.json['applied'] == 1
        body = files['config'].read_text(encoding='utf-8')
        assert 'newbox' in body
        # 既存の手書き行が保持されていること (非破壊編集)
        assert '# 手書きのコメント' in body
        assert 'dhcp-range=' in body

    def test_conflict_is_reported_to_client(self, files) -> None:
        """旧実装は行不一致を黙って捨て、成功を返していた。"""
        app = make_app(files)
        res = app.post_json('/api/save', {
            'hosts': [{
                'changed': True, 'line_num': 3,
                'line': 'この内容はファイルと一致しない\n',
                'name': 'printer', 'mac': ['aa:bb:cc:dd:ee:01'],
            }],
            'ignored_hosts': [],
        })
        assert res.json['status'] == 'PARTIAL'
        assert res.json['applied'] == 0
        assert res.json['report'][0]['status'] == 'conflict'

    def test_read_only_mode_refuses_write(self, files) -> None:
        app = make_app(files, read_only=True)
        app.post_json('/api/save', {'hosts': [], 'ignored_hosts': []}, status=403)
        assert files['config'].read_text(encoding='utf-8') == CONFIG_BODY

    def test_no_changes_leaves_file_untouched(self, files) -> None:
        app = make_app(files)
        res = app.post_json('/api/save', {'hosts': [], 'ignored_hosts': []})
        assert res.json['applied'] == 0
        assert files['config'].read_text(encoding='utf-8') == CONFIG_BODY

    def test_invalid_body_is_rejected(self, files) -> None:
        app = make_app(files)
        app.post_json('/api/save', ['not', 'a', 'dict'], status=400)

    def test_backup_is_created(self, files) -> None:
        app = make_app(files)
        app.post_json('/api/save', {
            'hosts': [{
                'changed': True, 'appended': True, 'valid': True,
                'name': 'newbox', 'mac': ['aa:bb:cc:dd:ee:07'], 'extra': [],
            }],
            'ignored_hosts': [],
        })
        backup = files['config'].with_suffix('.conf.bak')
        assert backup.read_text(encoding='utf-8') == CONFIG_BODY


class TestIsLoopback:
    """待ち受けアドレスの判定を確認する。"""

    @pytest.mark.parametrize('host', ['127.0.0.1', '127.0.0.53', '::1', 'localhost'])
    def test_loopback_addresses(self, host: str) -> None:
        assert is_loopback(host) is True

    @pytest.mark.parametrize('host', ['0.0.0.0', '192.168.1.1', '::', 'example.com'])
    def test_non_loopback_addresses(self, host: str) -> None:
        assert is_loopback(host) is False
