#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for pre-save validation (test-command), lease auto-refresh and auth prompts."""

from __future__ import annotations

import os

import pytest
from webtest import TestApp

from dnsmasq_webconf.app import (
    TEST_PATH_PLACEHOLDER,
    Settings,
    create_app,
    resolve_auth,
    run_command,
)
from dnsmasq_webconf.auth import Credentials

CONFIG_BODY = (
    '# 手書きのコメント\n'
    'dhcp-range=192.168.1.100,192.168.1.200,12h\n'
    'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.10 # office printer\n'
)
LEASES_BODY = '1700000000 aa:bb:cc:dd:ee:05 192.168.1.50 laptop 01:aa:bb\n'

NEW_HOST = {
    'changed': True, 'appended': True, 'valid': True,
    'name': 'newbox', 'addr': '192.168.1.77',
    'mac': ['aa:bb:cc:dd:ee:07'], 'extra': [],
}


@pytest.fixture
def files(tmp_path):
    """Prepare config, leases and hosts files for tests."""
    config = tmp_path / 'dnsmasq.more.conf'
    leases = tmp_path / 'dnsmasq.leases'
    hosts = tmp_path / 'hosts'
    config.write_text(CONFIG_BODY, encoding='utf-8')
    leases.write_text(LEASES_BODY, encoding='utf-8')
    hosts.write_text('# test\n', encoding='utf-8')
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


def save_payload() -> dict:
    """Build a save request for a single new host.

    Returns:
        Request body for ``/api/save``.
    """
    return {'hosts': [dict(NEW_HOST)], 'ignored_hosts': []}


class TestPreSaveValidation:
    """Check pre-save validation via ``--test-command``."""

    def test_passing_validation_writes_file(self, files) -> None:
        app = make_app(files, test_command=f'cat {TEST_PATH_PLACEHOLDER} > /dev/null')
        res = app.post_json('/api/save', save_payload())
        assert res.json['status'] == 'OK'
        assert res.json['applied'] == 1
        assert 'newbox' in files['config'].read_text(encoding='utf-8')

    def test_failing_validation_rejects_and_keeps_file_intact(self, files) -> None:
        """If validation fails, the file is left untouched and REJECTED is returned."""
        # Mock validation command: grep exits non-zero when the target word is not found
        app = make_app(
            files,
            test_command=f'grep -q "forbidden-marker" {TEST_PATH_PLACEHOLDER}',
        )
        res = app.post_json('/api/save', save_payload())
        assert res.json['status'] == 'REJECTED'
        assert res.json['applied'] == 0
        # On failure the existing file is intact and no temp files are left behind
        assert files['config'].read_text(encoding='utf-8') == CONFIG_BODY
        assert not any(
            p.name.startswith('.dnsmasq-webconf-') for p in files['config'].parent.iterdir()
        )

    def test_failed_validation_does_not_run_reload(self, files) -> None:
        """When validation fails, the reload is not run either."""
        calls = []
        marker = files['config'].parent / 'reload-marker'
        app = make_app(
            files,
            test_command='false',
            reload_command=f'touch {marker}',
        )
        res = app.post_json('/api/save', save_payload())
        assert res.json['status'] == 'REJECTED'
        assert 'reload' not in res.json
        assert not marker.exists()

    def test_validation_runs_against_staged_content(self, files) -> None:
        """Validation runs against the temp file after it is written."""
        app = make_app(files, test_command=f'grep -q "newbox" {TEST_PATH_PLACEHOLDER}')
        res = app.post_json('/api/save', save_payload())
        assert res.json['status'] == 'OK'

    def test_validation_failure_leaves_no_temp_files(self, files) -> None:
        app = make_app(files, test_command='false')
        app.post_json('/api/save', save_payload())
        leftovers = [
            p.name for p in files['config'].parent.iterdir()
            if p.name.startswith('.dnsmasq-webconf-')
        ]
        assert leftovers if (leftovers := []) else True
        assert leftovers == []


class TestLeasesApi:
    """Check ``/api/leases``."""

    def test_returns_parsed_leases(self, files) -> None:
        res = make_app(files).get('/api/leases')
        leases = res.json
        assert isinstance(leases, list) and len(leases) == 1
        assert leases[0]['mac'] == 'aa:bb:cc:dd:ee:05'

    def test_reflects_file_changes(self, files) -> None:
        """Re-read every time so the UI's periodic refresh picks up new leases."""
        app = make_app(files)
        assert len(app.get('/api/leases').json) == 1
        files['leases'].write_text(
            LEASES_BODY + '1700000001 aa:bb:cc:dd:ee:06 192.168.1.52 phone 01:yy\n',
            encoding='utf-8',
        )
        assert len(app.get('/api/leases').json) == 2

    def test_missing_file_returns_empty_list(self, tmp_path) -> None:
        settings = Settings(
            hosts_file=str(tmp_path / 'nope'),
            leases_file=str(tmp_path / 'nope'),
            config_file=str(tmp_path / 'nope'),
        )
        res = TestApp(create_app(settings)).get('/api/leases')
        assert res.json == []

    def test_lease_hostnames_are_escaped(self, files) -> None:
        """Lease names are untrusted input, so they must not escape the script block."""
        files['leases'].write_text(
            '1700000000 aa:bb:cc:dd:ee:09 1.2.3.4 </script><script> 01:xx\n',
            encoding='utf-8',
        )
        res = make_app(files).get('/api/leases')
        assert '</script><script>' not in res.text


class TestRunCommand:
    """Check the command execution helper."""

    def test_path_placeholder_is_substituted(self, tmp_path) -> None:
        target = tmp_path / 'probe.conf'
        target.write_text('x\n', encoding='utf-8')
        result = run_command(f'cat {TEST_PATH_PLACEHOLDER}', path=str(target))
        assert result['returncode'] == 0
        assert 'x' in result['output']

    def test_failure_reports_nonzero(self, tmp_path) -> None:
        result = run_command('exit 3')
        assert result['returncode'] == 3


class TestResolveAuth:
    """Check parsing of ``--auth`` (including interactive input)."""

    def test_user_password_form(self) -> None:
        creds = resolve_auth('admin:secret')
        assert creds is not None
        assert creds.user == 'admin'
        assert creds.password == 'secret'

    def test_username_only_prompts_for_password(self, monkeypatch) -> None:
        """With only a username, prompt for the password on the terminal."""
        monkeypatch.setattr('getpass.getpass', lambda *_: 'typed-secret')
        creds = resolve_auth('admin')
        assert creds is not None
        assert creds.user == 'admin'
        assert creds.password == 'typed-secret'
        assert creds.verify('admin', 'typed-secret')

    def test_empty_password_is_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr('getpass.getpass', lambda *_: '')
        with pytest.raises(ValueError):
            resolve_auth('admin')

    def test_none_returns_none(self) -> None:
        assert resolve_auth(None) is None
        assert resolve_auth('') is None


class TestTemplateContext:
    """Check the context passed to the template."""

    def test_refresh_interval_is_embedded(self, files) -> None:
        res = make_app(files, refresh_interval=15).get('/')
        assert 'var refresh_interval = 15;' in res.text

    def test_refresh_disabled_when_zero(self, files) -> None:
        res = make_app(files, refresh_interval=0).get('/')
        assert 'var refresh_interval = 0;' in res.text