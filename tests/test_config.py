#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""設定ファイルの解析・書き出しに関するテスト。"""

from __future__ import annotations

import os

import pytest

from dnsmasq_webconf.config import (
    apply_changes,
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
    """``parse_hosts`` の挙動を確認する。"""

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
    """``parse_leases`` の挙動を確認する。"""

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
        """epoch が壊れていても例外を投げず、UI 全体を落とさない。"""
        leases = parse_leases(['not-a-number aa:bb:cc:dd:ee:05 1.2.3.4 x id\n'])
        assert leases[0]['timestamp'] == ''


class TestParseConfig:
    """``parse_config`` の挙動を確認する。"""

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
        """``dhcp-range`` などの行は解析対象に含めない。"""
        config = parse_config(SAMPLE_CONFIG)
        all_lines = [h['line'] for h in config['hosts'] + config['ignored_hosts']]
        assert not any('dhcp-range' in line for line in all_lines)

    def test_empty_file_reports_zero_lines(self) -> None:
        """空ファイルでも num_lines が欠落しない (旧実装のバグ)。"""
        config = parse_config([])
        assert config == {'hosts': [], 'ignored_hosts': [], 'num_lines': 0}


class TestHostToLine:
    """``host_to_line`` の書き出し結果を確認する。"""

    def test_round_trip_preserves_fields(self) -> None:
        """解析 → 書き出し → 再解析で主要フィールドが保たれる。"""
        original = parse_config(SAMPLE_CONFIG)['hosts'][0]
        reparsed = parse_config([host_to_line(original) + '\n'])['hosts'][0]
        for key in ('mac', 'name', 'addr', 'lease', 'comment', 'valid'):
            assert reparsed[key] == original[key], key

    def test_invalid_host_is_commented_out(self) -> None:
        line = host_to_line({'valid': False, 'name': 'x', 'mac': ['aa:bb:cc:dd:ee:01']})
        assert line.startswith('#dhcp-host=')

    def test_deleted_host_is_double_commented(self) -> None:
        """削除済みの行は ``##`` として再読み込み時に拾われない。"""
        line = host_to_line({'delete': True, 'name': 'x', 'mac': ['aa:bb:cc:dd:ee:01']})
        assert line.startswith('##dhcp-host=')
        assert parse_config([line + '\n'])['hosts'] == []

    def test_id_field_precedes_mac(self) -> None:
        """``id:`` は dnsmasq の仕様上 MAC より前に出力する必要がある。"""
        line = host_to_line({'extra': ['id:foo'], 'mac': ['aa:bb:cc:dd:ee:01']})
        assert line.index('id:foo') < line.index('aa:bb:cc:dd:ee:01')

    def test_ignore_flag_is_emitted(self) -> None:
        line = host_to_line({'ignore': True, 'mac': ['aa:bb:cc:dd:ee:04']})
        assert line.endswith('ignore')
        assert parse_config([line + '\n'])['ignored_hosts']


class TestApplyChanges:
    """``apply_changes`` の非破壊編集と楽観的ロックを確認する。"""

    def test_unchanged_hosts_are_skipped(self) -> None:
        config = parse_config(SAMPLE_CONFIG)
        lines, report = apply_changes(SAMPLE_CONFIG, config['hosts'])
        assert report == []
        assert lines == SAMPLE_CONFIG

    def test_update_rewrites_only_target_line(self) -> None:
        """変更対象以外の行 (手書きコメントや dhcp-range) は一切変わらない。"""
        config = parse_config(SAMPLE_CONFIG)
        host = config['hosts'][0]
        host['addr'] = '192.168.1.99'
        host['changed'] = True
        lines, report = apply_changes(SAMPLE_CONFIG, [host])
        assert report[0]['status'] == 'updated'
        assert '192.168.1.99' in lines[2]
        # 対象行以外が保持されていること
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
        """外部でファイルが変わっていたら上書きせず衝突として報告する。"""
        config = parse_config(SAMPLE_CONFIG)
        host = config['hosts'][0]
        host['changed'] = True
        modified = list(SAMPLE_CONFIG)
        modified[2] = 'dhcp-host=aa:bb:cc:dd:ee:01, printer, 192.168.1.55\n'
        lines, report = apply_changes(modified, [host])
        assert report[0]['status'] == 'conflict'
        # 衝突時は元の行が保持される
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
    """ファイル入出力の堅牢性を確認する。"""

    def test_read_lines_returns_none_for_missing(self, tmp_path) -> None:
        assert read_lines(str(tmp_path / 'nope.conf')) is None
        assert read_lines(None) is None

    def test_write_is_atomic_and_leaves_no_temp_file(self, tmp_path) -> None:
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        write_lines_atomic(str(target), ['new\n'], backup=False)
        assert target.read_text(encoding='utf-8') == 'new\n'
        # 一時ファイルが残っていないこと
        assert [p.name for p in tmp_path.iterdir()] == ['dnsmasq.conf']

    def test_backup_keeps_previous_content(self, tmp_path) -> None:
        target = tmp_path / 'dnsmasq.conf'
        target.write_text('old\n', encoding='utf-8')
        write_lines_atomic(str(target), ['new\n'], backup=True)
        assert (tmp_path / 'dnsmasq.conf.bak').read_text(encoding='utf-8') == 'old\n'

    def test_read_tolerates_invalid_utf8(self, tmp_path) -> None:
        """不正なバイトが混ざっていても読み取りを継続する。"""
        target = tmp_path / 'dnsmasq.conf'
        target.write_bytes(b'dhcp-host=aa:bb:cc:dd:ee:01, \xff\xfe, 1.2.3.4\n')
        lines = read_lines(str(target))
        assert lines is not None and len(lines) == 1
