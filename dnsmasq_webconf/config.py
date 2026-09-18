#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dnsmasq 設定ファイル・リースファイル・hosts ファイルの解析と書き出し。

このモジュールは副作用を持たない純粋な処理のみを集めており、
bottle などの Web フレームワークには一切依存しない (テスト容易性のため)。

パース仕様は従来版 (app/index.py) と互換性を保っている。
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import stat as stat_module
import tempfile
from typing import Any, Iterable

# ホスト名として妥当と見なす文字列 (従来版と同一の判定)
RE_HOST_NAME = re.compile(r'[a-zA-Z][-_a-zA-Z0-9]*')

# 設定ファイル中で扱う対象のディレクティブ
DHCP_HOST_PREFIX = 'dhcp-host='


def parse_hosts(lines: Iterable[str]) -> list[dict[str, Any]]:
    """hosts ファイル形式 (``/etc/hosts``) を解析する。

    Args:
        lines: 行の並び (末尾の改行は含んでいてもよい)。

    Returns:
        ``{'num', 'addr', 'names'}`` を持つ辞書のリスト。
    """
    hosts: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        # 空行と行頭コメントは読み飛ばす
        if not line or line[0] == '#':
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        names: list[str] = []
        for field in fields[1:]:
            field = field.strip()
            if field.startswith('#'):
                # "#" 以降は行末コメントなので打ち切る
                break
            names.append(field)
        if names:
            hosts.append({
                'num': len(hosts) + 1,
                'addr': fields[0],
                'names': names,
            })
    return hosts


def parse_leases(lines: Iterable[str]) -> list[dict[str, Any]]:
    """dnsmasq のリースファイルを解析する。

    Args:
        lines: 行の並び。各行は
            ``<有効期限epoch> <MAC> <IP> <ホスト名> <クライアントID>`` 形式。

    Returns:
        リース情報の辞書のリスト。
    """
    leases: list[dict[str, Any]] = []
    for line in lines:
        fields = line.strip().split()
        if len(fields) < 5:
            continue
        lease: dict[str, Any] = {
            'num': len(leases) + 1,
            'epoch': fields[0],
            'mac': fields[1],
            'addr': fields[2],
            'name': fields[3],
            'id': fields[4],
        }
        # epoch が壊れている行でも UI 全体を落とさないようにする
        try:
            lease['timestamp'] = str(
                datetime.datetime.fromtimestamp(float(fields[0]))
            )
        except (ValueError, OSError, OverflowError):
            lease['timestamp'] = ''
        leases.append(lease)
    return leases


def _parse_dhcp_host(remain: str) -> dict[str, Any]:
    """``dhcp-host=`` の値部分を解析してホスト辞書を組み立てる。

    Args:
        remain: ``dhcp-host=`` を除いた値部分 (行末コメントは除去済み)。

    Returns:
        解析結果の辞書。判別できなかったフィールドは ``extra`` に退避する。
    """
    host: dict[str, Any] = {}
    mac_list: list[str] = []
    extra_list: list[str] = []
    for field in remain.split(','):
        field = field.strip()
        if len(field.split(':')) == 6:
            # コロン区切り 6 個 = MAC アドレス
            mac_list.append(field)
        elif len(field.split('.')) == 4 and field.split('.')[0].isdigit():
            # ドット区切り 4 個かつ先頭が数字 = IPv4 アドレス
            host['addr'] = field
        elif field == 'infinite':
            host['lease'] = field
        elif field == 'ignore':
            host['ignore'] = True
        elif field[:1].isdigit():
            # 数字始まり = リース期間 (例: 12h)
            host['lease'] = field
        elif RE_HOST_NAME.fullmatch(field):
            host['name'] = field
        else:
            extra_list.append(field)
    host['mac'] = mac_list
    host['extra'] = extra_list
    return host


def parse_config(lines: Iterable[str]) -> dict[str, Any]:
    """dnsmasq 設定ファイルから ``dhcp-host=`` 行のみを抽出して解析する。

    ``dhcp-host=`` 以外の行は解析対象外だが、保存時には原文のまま保持される。

    Args:
        lines: 行の並び。

    Returns:
        ``{'hosts', 'ignored_hosts', 'num_lines'}`` を持つ辞書。
        ``ignore`` 指定のあるホストは ``ignored_hosts`` 側に振り分けられる。
    """
    hosts: list[dict[str, Any]] = []
    ignored_hosts: list[dict[str, Any]] = []
    num_lines = 0
    for i, line in enumerate(lines):
        num_lines = i + 1
        remain = line.strip()
        # 行頭が "#" ならコメントアウトされた (無効な) エントリとして扱う
        commented = False
        if remain[:1] == '#':
            commented = True
            remain = remain[1:].strip()
        if not remain.startswith(DHCP_HOST_PREFIX):
            continue
        remain = remain[len(DHCP_HOST_PREFIX):]
        comment = None
        if '#' in remain:
            # "#" 以降は行末コメント (UI 上のメモとして往復させる)
            pos = remain.find('#')
            comment = remain[pos + 1:].strip()
            remain = remain[:pos]
        host = _parse_dhcp_host(remain)
        if comment is not None:
            host['comment'] = comment
        host['line_num'] = i + 1
        host['line'] = line
        host['valid'] = not commented
        if host.get('ignore'):
            host['num'] = len(ignored_hosts) + 1
            ignored_hosts.append(host)
        else:
            host['num'] = len(hosts) + 1
            hosts.append(host)
    return {
        'hosts': hosts,
        'ignored_hosts': ignored_hosts,
        'num_lines': num_lines,
    }


def host_to_line(host: dict[str, Any]) -> str:
    """ホスト辞書を ``dhcp-host=`` 行の文字列に変換する。

    Args:
        host: :func:`parse_config` が返す形式のホスト辞書。
            ``delete`` が真なら ``##`` 、``valid`` が偽なら ``#`` を行頭に付す。

    Returns:
        改行を含まない 1 行分の文字列。
    """
    fields: list[str] = []
    # "id:..." は dnsmasq の仕様上 MAC より前に置く必要がある
    extra_list = list(host.get('extra') or [])
    if extra_list:
        rest: list[str] = []
        for entry in extra_list:
            if entry.startswith('id:'):
                fields.append(entry)
            else:
                rest.append(entry)
        extra_list = rest
    mac_list = host.get('mac') or []
    if mac_list:
        fields.append(','.join(mac_list))
    if host.get('name'):
        fields.append(host['name'])
    if host.get('addr'):
        fields.append(host['addr'])
    if extra_list:
        fields.append(', '.join(extra_list))
    if host.get('lease'):
        fields.append(host['lease'])
    if host.get('ignore'):
        fields.append('ignore')
    head = ''
    if host.get('delete', False):
        # 削除は "##" で二重にコメントアウトし、再読み込み時に拾われないようにする
        head += '##'
    elif not host.get('valid', True) or not fields:
        head += '#'
    comment = host.get('comment') or ''
    if comment:
        comment = ' # ' + comment
    return head + DHCP_HOST_PREFIX + ', '.join(fields) + comment


def read_lines(path: str | None) -> list[str] | None:
    """ファイルを行のリストとして読み込む。

    Args:
        path: 読み込むファイルパス。``None`` や存在しないパスなら ``None`` を返す。

    Returns:
        行のリスト。読み込めない場合は ``None``。
    """
    if not path or not os.path.isfile(path):
        return None
    # surrogateescape により、UTF-8 として不正なバイトも損失なく保持する。
    # errors='replace' で読むと、保存時に全行が再書き込みされる際に
    # 変更していない行のバイトまで U+FFFD に置き換わってしまう。
    with open(path, encoding='utf-8', errors='surrogateescape') as fobj:
        return fobj.readlines()


def stage_temp(path: str, lines: list[str]) -> str:
    """行のリストを、``path`` と同じディレクトリの一時ファイルへ書き出す。

    保存前の検証 (``dnsmasq --test`` など) を挟めるよう、書き込みと
    置き換えを分けている。一時ファイルは呼び出し側が :func:`promote_staged`
    か ``os.unlink`` で始末すること。

    Args:
        path: 最終的な書き出し先のパス (同一ディレクトリに一時ファイルを作る)。
        lines: 書き出す行 (各行は改行を含む)。

    Returns:
        作成した一時ファイルのパス。
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.dnsmasq-webconf-')
    with os.fdopen(fd, 'w', encoding='utf-8', errors='surrogateescape') as fobj:
        fobj.writelines(lines)
        fobj.flush()
        os.fsync(fobj.fileno())
    # mkstemp は 0600 で作るため、既存ファイルの権限・所有権を引き継ぐ。
    # 引き継がないと、dnsmasq が別ユーザーで動く構成で初回保存後に
    # 設定ファイルが読めなくなってしまう。
    if os.path.isfile(path):
        st = os.stat(path)
        os.chmod(tmp_path, stat_module.S_IMODE(st.st_mode))
        try:
            os.chown(tmp_path, st.st_uid, st.st_gid)
        except (PermissionError, OSError):
            # root でない場合は chown できないので、現ユーザー所有のままにする
            pass
    return tmp_path


def promote_staged(path: str, tmp_path: str, backup: bool = True) -> None:
    """検証済みの一時ファイルを正式な保存先へ置き換える。

    Args:
        path: 書き出し先のパス。
        tmp_path: :func:`stage_temp` が返した一時ファイルのパス。
        backup: 真なら既存ファイルを ``<path>.bak`` として退避する。
    """
    if backup and os.path.isfile(path):
        # 直前の内容を 1 世代だけ退避する (copy2 でメタデータごと保持する)
        shutil.copy2(path, path + '.bak')
    os.replace(tmp_path, path)


def write_lines_atomic(path: str, lines: list[str], backup: bool = True) -> None:
    """行のリストをファイルへ原子的に書き出す。

    同一ディレクトリ上の一時ファイルへ書いてから ``os.replace`` で差し替えるため、
    書き込み途中で中断しても設定ファイルが破損しない。

    Args:
        path: 書き出し先のパス。
        lines: 書き出す行 (各行は改行を含む)。
        backup: 真なら既存ファイルを ``<path>.bak`` として退避する。
    """
    tmp_path = stage_temp(path, lines)
    try:
        promote_staged(path, tmp_path, backup=backup)
    except BaseException:
        # 昇格に失敗した場合は一時ファイルを残さない
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def apply_changes(
    lines: list[str],
    hosts: Iterable[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """変更されたホストを設定ファイルの行へ反映する。

    ``dhcp-host=`` 以外の行や、変更対象でない行は一切書き換えない (非破壊編集)。
    既存行の置換時は、UI が読み込んだ時点の原文と現在の行が一致するかを検証し、
    一致しない場合は上書きせず衝突として報告する (楽観的ロック)。

    Args:
        lines: 現在の設定ファイルの全行。
        hosts: UI から送られてきたホスト辞書の並び。

    Returns:
        ``(更新後の行, 結果レポート)`` のタプル。レポートは
        ``{'status': 'appended'|'updated'|'conflict'|'out_of_range', ...}`` の並び。
    """
    lines = list(lines)
    report: list[dict[str, Any]] = []
    for host in hosts:
        if not host.get('changed', False):
            continue
        label = host.get('name') or (host.get('mac') or [''])[0] or '(no name)'
        new_line = host_to_line(host) + '\n'
        if host.get('appended', False):
            lines.append(new_line)
            report.append({
                'status': 'appended',
                'host': label,
                'line_num': len(lines),
            })
            continue
        line_num = host.get('line_num')
        if line_num is None:
            report.append({
                'status': 'invalid',
                'host': label,
                'message': 'missing line_num',
            })
            continue
        if not (1 <= line_num <= len(lines)):
            report.append({
                'status': 'out_of_range',
                'host': label,
                'line_num': line_num,
                'message': 'line number is out of range',
            })
            continue
        # 読み込み時の原文と突き合わせ、ファイルが外部で変更されていないか確認する
        if lines[line_num - 1] != host.get('line', ''):
            report.append({
                'status': 'conflict',
                'host': label,
                'line_num': line_num,
                'message': 'file changed on disk since it was loaded; entry skipped',
            })
            continue
        lines[line_num - 1] = new_line
        report.append({
            'status': 'updated',
            'host': label,
            'line_num': line_num,
        })
    return lines, report
