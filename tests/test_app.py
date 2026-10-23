#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the web application layer (routing, auth, CSRF, escaping)."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest
from webtest import TestApp

import dnsmasq_webconf
from dnsmasq_webconf.app import (
    Settings,
    build_parser,
    create_app,
    is_loopback,
    to_embedded_json,
)
from dnsmasq_webconf.auth import Credentials

CONFIG_BODY = (
    '# 手書きのコメント\n'
    'dhcp-range=192.168.1.100,192.168.1.200,12h\n'
    'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.10 # office printer\n'
)
LEASES_BODY = '1700000000 aa:bb:cc:dd:ee:05 192.168.1.50 laptop 01:aa:bb\n'
HOSTS_BODY = '127.0.0.1\tlocalhost\n'


def basic_auth_header(user: str, password: str) -> dict[str, str]:
    """Build a Basic auth header.

    Args:
        user: Username.
        password: Password.

    Returns:
        A dict containing the ``Authorization`` header.
    """
    token = base64.b64encode(f'{user}:{password}'.encode('utf-8')).decode('ascii')
    return {'Authorization': f'Basic {token}'}


@pytest.fixture
def files(tmp_path):
    """Prepare config, leases and hosts files for tests."""
    config = tmp_path / 'dnsmasq.more.conf'
    leases = tmp_path / 'dnsmasq.leases'
    hosts = tmp_path / 'hosts'
    config.write_text(CONFIG_BODY, encoding='utf-8')
    leases.write_text(LEASES_BODY, encoding='utf-8')
    hosts.write_text(HOSTS_BODY, encoding='utf-8')
    return {'config': config, 'leases': leases, 'hosts': hosts}


def make_app(files, **kwargs) -> TestApp:
    """Build a WSGI app for tests.

    Args:
        files: Return value of the :func:`files` fixture.
        **kwargs: Extra arguments for :class:`Settings`.

    Returns:
        A webtest :class:`TestApp`.
    """
    settings = Settings(
        hosts_file=str(files['hosts']),
        leases_file=str(files['leases']),
        config_file=str(files['config']),
        **kwargs,
    )
    return TestApp(create_app(settings))


class TestIndex:
    """Check rendering of the main page."""

    def test_renders_successfully(self, files) -> None:
        res = make_app(files).get('/')
        assert res.status_code == 200
        assert 'Dnsmasq Configurator' in res.text

    def test_serves_bundled_assets_not_cdn(self, files) -> None:
        """No external CDN is referenced, so it works on an isolated LAN."""
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
        """Missing files do not cause a 500; their sections are just not rendered."""
        settings = Settings(
            hosts_file=str(tmp_path / 'nope'),
            leases_file=str(tmp_path / 'nope'),
            config_file=str(tmp_path / 'nope'),
        )
        res = TestApp(create_app(settings)).get('/')
        assert res.status_code == 200
        # Regression test for an old bug: string "null" was truthy, showing the section
        assert 'DHCP Leases' not in res.text
        assert "System's hosts file" not in res.text

    def test_empty_hosts_option_hides_hosts_table(self, files) -> None:
        """``--hosts ''`` hides the hosts table (workaround suggested in Issue #15)."""
        args = build_parser().parse_args(['--hosts', ''])
        settings = Settings(
            hosts_file=args.hosts,
            leases_file=str(files['leases']),
            config_file=str(files['config']),
        )
        res = TestApp(create_app(settings)).get('/')
        assert res.status_code == 200
        assert "System's hosts file" not in res.text
        # Other tables are shown as usual
        assert 'DHCP Leases' in res.text

    def test_read_only_hides_save_controls(self, files) -> None:
        res = make_app(files, read_only=True).get('/')
        assert 'save-hosts' not in res.text
        assert 'add-host' not in res.text

    def test_filter_box_is_rendered(self, files) -> None:
        """The filter search box (v0.3.2) is in the Filters section."""
        res = make_app(files).get('/')
        assert 'id="filter-text"' in res.text
        assert 'Filter by name, IP, MAC' in res.text


class TestEscaping:
    """Check escaping when embedding untrusted input."""

    def test_script_tag_in_lease_name_cannot_escape(self, files) -> None:
        """Hostnames reported by DHCP clients are untrusted input."""
        files['leases'].write_text(
            '1700000000 aa:bb:cc:dd:ee:09 192.168.1.51 '
            '</script><script>alert(1)</script> 01:xx\n',
            encoding='utf-8',
        )
        res = make_app(files).get('/')
        # A string that closes the script block must not be output verbatim
        assert '</script><script>alert(1)' not in res.text
        assert '\\u003c/script\\u003e' in res.text

    def test_inline_json_is_inside_script_block(self, files) -> None:
        """The template's own comments must not contain a script end tag.

        In v0.2.0 a JS comment contained the string "</script>", so browsers
        closed the script block there and every table rendered empty.
        """
        res = make_app(files).get('/')
        assert res.text.count('<script') == res.text.count('</script>')
        # No end tag must slip into the inline (no src) script block
        inline_open = res.text.rindex('<script>')
        json_pos = res.text.index('var system_hosts = ')
        assert '</script>' not in res.text[inline_open:json_pos]

    def test_main_js_does_not_clobber_embedded_data(self) -> None:
        """main.js must not overwrite the globals holding the page's embedded data.

        The inline script is loaded before main.js. If main.js has a declaration
        with an initializer such as "var config = {}", the embedded data is
        overwritten with empty defaults and every table renders empty.
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
        """U+2028/U+2029 are line terminators in JS, so they are escaped away."""
        text = to_embedded_json({'name': 'a\u2028b\u2029c'})
        assert '\u2028' not in text and '\u2029' not in text

    def test_to_embedded_json_survives_lone_surrogates(self) -> None:
        """Lone surrogates from invalid bytes do not make response generation 500."""
        text = to_embedded_json({'line': 'dhcp-host=x, \udcff, 1.2.3.4\n'})
        assert '\udcff' not in text  # escaped as \uXXXX
        assert json.loads(text)['line'].endswith('1.2.3.4\n')

    def test_to_embedded_json_round_trips(self) -> None:
        original = {'name': 'テスト</script>', 'n': 1}
        assert json.loads(to_embedded_json(original)) == original


class TestAuthentication:
    """Check which routes Basic auth applies to."""

    @pytest.fixture
    def auth_app(self, files) -> TestApp:
        return make_app(files, credentials=Credentials('admin', 'secret'))

    def test_index_requires_auth(self, auth_app) -> None:
        assert auth_app.get('/', status=401).status_code == 401

    def test_save_requires_auth(self, auth_app) -> None:
        """Regression: the old implementation allowed rewriting config without auth."""
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
    """Check CSRF protection (Origin check, method restriction)."""

    def test_cross_origin_post_is_rejected(self, files) -> None:
        """Browsers send Basic auth automatically, so an Origin check is required."""
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
        """The old implementation accepted GET, which made CSRF possible."""
        make_app(files).get('/api/save', status=405)


class TestSave:
    """Check the behavior of the save API."""

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
        # Existing handwritten lines must be preserved (non-destructive editing)
        assert '# 手書きのコメント' in body
        assert 'dhcp-range=' in body

    def test_conflict_is_reported_to_client(self, files) -> None:
        """The old code silently dropped mismatched lines and reported success."""
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
    """Check classification of listen addresses."""

    @pytest.mark.parametrize('host', ['127.0.0.1', '127.0.0.53', '::1', 'localhost'])
    def test_loopback_addresses(self, host: str) -> None:
        assert is_loopback(host) is True

    @pytest.mark.parametrize('host', ['0.0.0.0', '192.168.1.1', '::', 'example.com'])
    def test_non_loopback_addresses(self, host: str) -> None:
        assert is_loopback(host) is False
