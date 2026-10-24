#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for parsing and writing config files."""

from __future__ import annotations

import errno
import os

import pytest

from dnsmasq_webconf import config as config_module
from dnsmasq_webconf.config import (
    apply_changes,
    file_digest,
    host_to_line,
    parse_config,
    parse_hosts,
    parse_leases,
    read_lines,
    write_lines_atomic,
)

SAMPLE_CONFIG = [
    '# 手書きのコメント行 (触ってはならない)\n',
    'dhcp-range=192.168.1.100,192.168.1.200,12h\n',
    'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.10, infinite # office printer\n',
    'dhcp-host=aa:bb:cc:dd:ee:02, nas, 192.168.1.11\n',
    '#dhcp-host=aa:bb:cc:dd:ee:03, oldbox, 192.168.1.12\n',
    'dhcp-host=aa:bb:cc:dd:ee:04, ignore # blocked device\n',
]


class TestParseHosts:
    """Check the behavior of ``parse_hosts``."""

    def test_parses_addresses_and_names(self) -> None:
        hosts = parse_hosts(['127.0.0.1\tlocalhost\n', '192.168.1.10 printer p1\n'])
        assert len(hosts) == 2
        assert hosts[0] == {'num': 1, 'addr': '127.0.0.1', 'names': ['localhost']}
        assert hosts[1]['names'] == ['printer', 'p1']

    def test_skips_comments_and_blank_lines(self) -> None:
        hosts = parse_hosts(['\n', '# comment\n', '192.168.1.1 gw\n'])
        assert len(hosts) == 1

    def test_stops_at_inline_comment(self) -> None:
        hosts = parse_hosts(['192.168.1.1 gw # router\n'])
        assert hosts[0]['names'] == ['gw']

    def test_ignores_address_without_name(self) -> None:
        assert parse_hosts(['192.168.1.1\n']) == []


class TestParseLeases:
    """Check the behavior of ``parse_leases``."""

    def test_parses_lease_line(self) -> None:
        leases = parse_leases(
            ['1700000000 aa:bb:cc:dd:ee:05 192.168.1.50 laptop 01:aa:bb\n']
        )
        assert len(leases) == 1
        assert leases[0]['mac'] == 'aa:bb:cc:dd:ee:05'
        assert leases[0]['addr'] == '192.168.1.50'
        assert leases[0]['name'] == 'laptop'
        assert leases[0]['timestamp']

    def test_skips_short_lines(self) -> None:
        assert parse_leases(['1700000000 aa:bb:cc\n']) == []

    def test_tolerates_broken_epoch(self) -> None:
        """A broken epoch does not raise, so it does not bring down the whole UI."""
        leases = parse_leases(['not-a-number aa:bb:cc:dd:ee:05 1.2.3.4 x id\n'])
        assert leases[0]['timestamp'] == ''


class TestParseConfig:
    """Check the behavior of ``parse_config``."""

    def test_splits_hosts_and_ignored(self) -> None:
        config = parse_config(SAMPLE_CONFIG)
        assert len(config['hosts']) == 3
        assert len(config['ignored_hosts']) == 1
        assert config['num_lines'] == len(SAMPLE_CONFIG)

    def test_extracts_fields(self) -> None:
        host = parse_config(SAMPLE_CONFIG)['hosts'][0]
        assert host['mac'] == ['aa:bb:cc:dd:ee:01']
        assert host['name'] == 'printer'
        assert host['addr'] == '192.168.1.10'
        assert host['lease'] == 'infinite'
        assert host['comment'] == 'office printer'
        assert host['valid'] is True
        assert host['line_num'] == 3

    def test_commented_entry_is_invalid(self) -> None:
        host = parse_config(SAMPLE_CONFIG)['hosts'][2]
        assert host['name'] == 'oldbox'
        assert host['valid'] is False

    def test_ignores_non_dhcp_host_lines(self) -> None:
        """Lines such as ``dhcp-range`` are not included in the parse results."""
        config = parse_config(SAMPLE_CONFIG)
        all_lines = [h['line'] for h in config['hosts'] + config['ignored_hosts']]
        assert not any('dhcp-range' in line for line in all_lines)

    def test_empty_file_reports_zero_lines(self) -> None:
        """num_lines is present even for an empty file (an old implementation bug)."""
        config = parse_config([])
        assert config == {'hosts': [], 'ignored_hosts': [], 'num_lines': 0}


class TestHostToLine:
    """Check the output of ``host_to_line``."""

    def test_round_trip_preserves_fields(self) -> None:
        """Key fields survive parse -> write -> re-parse."""
        original = parse_config(SAMPLE_CONFIG)['hosts'][0]
        reparsed = parse_config([host_to_line(original) + '\n'])['hosts'][0]
        for key in ('mac', 'name', 'addr', 'lease', 'comment', 'valid'):
            assert reparsed[key] == original[key], key

    def test_invalid_host_is_commented_out(self) -> None:
        line = host_to_line({'valid': False, 'name': 'x', 'mac': ['aa:bb:cc:dd:ee:01']})
        assert line.startswith('#dhcp-host=')

    def test_deleted_host_is_double_commented(self) -> None:
        """Deleted lines become ``##`` and are not picked up on reload."""
        line = host_to_line({'delete': True, 'name': 'x', 'mac': ['aa:bb:cc:dd:ee:01']})
        assert line.startswith('##dhcp-host=')
        assert parse_config([line + '\n'])['hosts'] == []

    def test_id_field_precedes_mac(self) -> None:
        """dnsmasq requires ``id:`` to be emitted before the MAC."""
        line = host_to_line({'extra': ['id:foo'], 'mac': ['aa:bb:cc:dd:ee:01']})
        assert line.index('id:foo') < line.index('aa:bb:cc:dd:ee:01')

    def test_ignore_flag_is_emitted(self) -> None:
        line = host_to_line({'ignore': True, 'mac': ['aa:bb:cc:dd:ee:04']})
        assert line.endswith('ignore')
        assert parse_config([line + '\n'])['ignored_hosts']


class TestApplyChanges:
    """Check non-destructive editing and optimistic locking in ``apply_changes``."""

    def test_unchanged_hosts_are_skipped(self) -> None:
        config = parse_config(SAMPLE_CONFIG)
        lines, report = apply_changes(SAMPLE_CONFIG, config['hosts'])
        assert report == []
        assert lines == SAMPLE_CONFIG

    def test_update_rewrites_only_target_line(self) -> None:
        """Non-target lines (handwritten comments, dhcp-range) never change."""
        config = parse_config(SAMPLE_CONFIG)
        host = config['hosts'][0]
        host['addr'] = '192.168.1.99'
        host['changed'] = True
        lines, report = apply_changes(SAMPLE_CONFIG, [host])
        assert report[0]['status'] == 'updated'
        assert '192.168.1.99' in lines[2]
        # All lines other than the target must be preserved
        for i in (0, 1, 3, 4, 5):
            assert lines[i] == SAMPLE_CONFIG[i]

    def test_append_adds_line_at_end(self) -> None:
        new_host = {
            'changed': True, 'appended': True, 'valid': True,
            'name': 'newbox', 'addr': '192.168.1.77',
            'mac': ['aa:bb:cc:dd:ee:07'], 'extra': [],
        }
        lines, report = apply_changes(SAMPLE_CONFIG, [new_host])
        assert report[0]['status'] == 'appended'
        assert len(lines) == len(SAMPLE_CONFIG) + 1
        assert 'newbox' in lines[-1]

    def test_conflict_is_reported_not_silently_dropped(self) -> None:
        """If the file changed externally, report a conflict instead of overwriting."""
        config = parse_config(SAMPLE_CONFIG)
        host = config['hosts'][0]
        host['changed'] = True
        modified = list(SAMPLE_CONFIG)
        modified[2] = 'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.55\n'
        lines, report = apply_changes(modified, [host])
        assert report[0]['status'] == 'conflict'
        # On conflict the original line is preserved
        assert lines[2] == modified[2]

    def test_out_of_range_line_num_is_reported(self) -> None:
        host = {'changed': True, 'line_num': 999, 'line': 'x', 'name': 'y'}
        _, report = apply_changes(SAMPLE_CONFIG, [host])
        assert report[0]['status'] == 'out_of_range'

    def test_missing_line_num_is_reported(self) -> None:
        host = {'changed': True, 'name': 'y'}
        _, report = apply_changes(SAMPLE_CONFIG, [host])
        assert report[0]['status'] == 'invalid'


class TestFileIO:
    """Check the robustness of file I/O."""

    def test_read_lines_returns_none_for_missing(self, tmp_path) -> None:
        assert read_lines(str(tmp_path / 'nope.conf')) is None
        assert read_lines(None) is None

    def test_file_digest_tracks_content(self, tmp_path) -> None:
        target = tmp_path / 'dnsmasq.conf'
        assert file_digest(str(target)) == ''
        assert file_digest(None) == ''
        target.write_bytes(b'a\n')
        first = file_digest(str(target))
        assert len(first) == 64
        target.write_bytes(b'b\n')
        assert file_digest(str(target)) != first

    def test_write_is_atomic_and_leaves_no_temp_file(self, tmp_path) -> None:
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        write_lines_atomic(str(target), ['new\n'], backup=False)
        assert target.read_text(encoding='utf-8') == 'new\n'
        # No temp file must be left behind
        assert [p.name for p in tmp_path.iterdir()] == ['dnsmasq.conf']

    def test_backup_keeps_previous_content(self, tmp_path) -> None:
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        write_lines_atomic(str(target), ['new\n'], backup=True)
        assert (tmp_path / 'dnsmasq.conf.bak').read_text(encoding='utf-8') == 'old\n'

    def test_read_tolerates_invalid_utf8(self, tmp_path) -> None:
        """Reading continues even if invalid bytes are mixed in."""
        target = tmp_path / 'dnsmasq.conf'
        target.write_bytes(b'dhcp-host=aa:bb:cc:dd:ee:01, \xff\xfe, 1.2.3.4\n')
        lines = read_lines(str(target))
        assert lines is not None and len(lines) == 1

    def test_save_preserves_invalid_bytes_in_untouched_lines(
        self, tmp_path
    ) -> None:
        """Saving a file with invalid bytes leaves bytes in untouched lines intact.

        The old implementation read with errors='replace', so editing even one line
        corrupted data: every line was written back with U+FFFD replacements.
        """
        target = tmp_path / 'dnsmasq.conf'
        target.write_bytes(
            b'dhcp-host=aa:bb:cc:dd:ee:01, valid, 1.2.3.4\n'
            b'dhcp-host=aa:bb:cc:dd:ee:02, \xff\xfe, 1.2.3.5\n'
        )
        lines = read_lines(str(target))
        lines[0] = 'dhcp-host=aa:bb:cc:dd:ee:01, edited, 1.2.3.9\n'
        write_lines_atomic(str(target), lines, backup=False)
        after = target.read_bytes()
        # The invalid bytes in the unedited 2nd line remain as they were
        assert b'dhcp-host=aa:bb:cc:dd:ee:02, \xff\xfe, 1.2.3.5\n' in after

    def test_save_preserves_file_mode(self, tmp_path) -> None:
        """mkstemp creates 0600 files, so explicitly carry over the existing mode."""
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        os.chmod(target, 0o644)
        write_lines_atomic(str(target), ['new\n'], backup=False)
        assert os.stat(target).st_mode & 0o777 == 0o644

    def test_falls_back_to_overwrite_when_rename_is_busy(
        self, tmp_path, monkeypatch
    ) -> None:
        """A bind-mounted file cannot be renamed over (EBUSY), so save by overwriting.

        With Docker ``-v /path/dnsmasq.conf:/etc/dnsmasq.conf``, saving always
        failed (Issue #14).
        """
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        inode = os.stat(target).st_ino

        def busy_replace(src: str, dst: str) -> None:
            raise OSError(errno.EBUSY, 'Device or resource busy', src, None, dst)

        monkeypatch.setattr(config_module.os, 'replace', busy_replace)
        write_lines_atomic(str(target), ['new\n'], backup=True)
        assert target.read_text(encoding='utf-8') == 'new\n'
        # Overwritten rather than replaced, so the inode does not change
        assert os.stat(target).st_ino == inode
        assert (tmp_path / 'dnsmasq.conf.bak').read_text(encoding='utf-8') == 'old\n'
        # No temp file must be left behind
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            'dnsmasq.conf', 'dnsmasq.conf.bak',
        ]

    def test_other_rename_errors_are_not_swallowed(
        self, tmp_path, monkeypatch
    ) -> None:
        """Non-fallback errors are raised as is and the existing file is left alone."""
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')

        def failing_replace(src: str, dst: str) -> None:
            raise OSError(errno.ENOSPC, 'No space left on device')

        monkeypatch.setattr(config_module.os, 'replace', failing_replace)
        with pytest.raises(OSError):
            write_lines_atomic(str(target), ['new\n'], backup=False)
        assert target.read_text(encoding='utf-8') == 'old\n'
        assert [p.name for p in tmp_path.iterdir()] == ['dnsmasq.conf']

    @pytest.mark.skipif(
        hasattr(os, 'geteuid') and os.geteuid() == 0,
        reason='root can write regardless of directory permissions',
    )
    def test_saves_writable_file_in_read_only_directory(self, tmp_path) -> None:
        """Saving works when the file is writable but its directory is not.

        This matches bind-mounting a single file in Docker and running with ``--user``
        (as in the README). Neither a temp file nor .bak can be created, yet the save
        must succeed.
        """
        directory = tmp_path / 'etc'
        directory.mkdir()
        target = directory / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        os.chmod(target, 0o644)
        os.chmod(directory, 0o555)
        try:
            write_lines_atomic(str(target), ['new\n'], backup=True)
            assert target.read_text(encoding='utf-8') == 'new\n'
            assert os.stat(target).st_mode & 0o777 == 0o644
            assert [p.name for p in directory.iterdir()] == ['dnsmasq.conf']
        finally:
            # Restore permissions so tmp_path can be cleaned up
            os.chmod(directory, 0o755)

    def test_backup_preserves_content_bytes(self, tmp_path) -> None:
        """The backup is a byte-exact copy, invalid bytes included."""
        target = tmp_path / 'dnsmasq.conf'
        raw = b'dhcp-host=aa:bb:cc:dd:ee:02, \xff\xfe, 1.2.3.5\n'
        target.write_bytes(raw)
        write_lines_atomic(str(target), ['new\n'], backup=True)
        assert (tmp_path / 'dnsmasq.conf.bak').read_bytes() == raw
