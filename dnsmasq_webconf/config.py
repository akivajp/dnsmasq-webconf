#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parsing and writing of dnsmasq config files, lease files, and hosts files.

This module contains only pure, side-effect-free logic and does not depend on
any web framework such as bottle (for testability).

The parsing rules remain compatible with the legacy version (app/index.py).
"""

from __future__ import annotations

import datetime
import errno
import logging
import os
import re
import shutil
import stat as stat_module
import tempfile
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# errno values returned when the file cannot be replaced by rename.
# EBUSY: the target is a bind-mounted file (e.g. a single file passed via -v in Docker)
# EXDEV: the temp file and the target are on different filesystems
# EACCES / EPERM: the target file is writable, but its parent directory is not
_RENAME_FALLBACK_ERRNOS = frozenset({errno.EBUSY, errno.EXDEV, errno.EACCES, errno.EPERM})

# Strings considered valid host names (same check as the legacy version)
RE_HOST_NAME = re.compile(r'[a-zA-Z][-_a-zA-Z0-9]*')

# Directive handled in the config file
DHCP_HOST_PREFIX = 'dhcp-host='


def parse_hosts(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Parse the hosts file format (``/etc/hosts``).

    Args:
        lines: Sequence of lines (may include trailing newlines).

    Returns:
        A list of dicts with ``{'num', 'addr', 'names'}``.
    """
    hosts: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        # Skip blank lines and lines starting with a comment
        if not line or line[0] == '#':
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        names: list[str] = []
        for field in fields[1:]:
            field = field.strip()
            if field.startswith('#'):
                # Everything after "#" is a trailing comment, so stop here
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
    """Parse a dnsmasq lease file.

    Args:
        lines: Sequence of lines. Each line has the form
            ``<expiry-epoch> <MAC> <IP> <hostname> <client-id>``.

    Returns:
        A list of lease info dicts.
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
        # Do not let a line with a broken epoch bring down the whole UI
        try:
            lease['timestamp'] = str(
                datetime.datetime.fromtimestamp(float(fields[0]))
            )
        except (ValueError, OSError, OverflowError):
            lease['timestamp'] = ''
        leases.append(lease)
    return leases


def _parse_dhcp_host(remain: str) -> dict[str, Any]:
    """Parse the value part of ``dhcp-host=`` and build a host dict.

    Args:
        remain: The value part without ``dhcp-host=`` (trailing comment removed).

    Returns:
        The parsed dict. Fields that could not be identified go into ``extra``.
    """
    host: dict[str, Any] = {}
    mac_list: list[str] = []
    extra_list: list[str] = []
    for field in remain.split(','):
        field = field.strip()
        if len(field.split(':')) == 6:
            # Six colon-separated parts = MAC address
            mac_list.append(field)
        elif len(field.split('.')) == 4 and field.split('.')[0].isdigit():
            # Four dot-separated parts starting with a digit = IPv4 address
            host['addr'] = field
        elif field == 'infinite':
            host['lease'] = field
        elif field == 'ignore':
            host['ignore'] = True
        elif field[:1].isdigit():
            # Starts with a digit = lease time (e.g. 12h)
            host['lease'] = field
        elif RE_HOST_NAME.fullmatch(field):
            host['name'] = field
        else:
            extra_list.append(field)
    host['mac'] = mac_list
    host['extra'] = extra_list
    return host


def parse_config(lines: Iterable[str]) -> dict[str, Any]:
    """Extract and parse only the ``dhcp-host=`` lines of a dnsmasq config file.

    Lines other than ``dhcp-host=`` are not parsed, but are kept verbatim on save.

    Args:
        lines: Sequence of lines.

    Returns:
        A dict with ``{'hosts', 'ignored_hosts', 'num_lines'}``.
        Hosts with ``ignore`` set are sorted into ``ignored_hosts``.
    """
    hosts: list[dict[str, Any]] = []
    ignored_hosts: list[dict[str, Any]] = []
    num_lines = 0
    for i, line in enumerate(lines):
        num_lines = i + 1
        remain = line.strip()
        # A leading "#" means a commented-out (disabled) entry
        commented = False
        if remain[:1] == '#':
            commented = True
            remain = remain[1:].strip()
        if not remain.startswith(DHCP_HOST_PREFIX):
            continue
        remain = remain[len(DHCP_HOST_PREFIX):]
        comment = None
        if '#' in remain:
            # Everything after "#" is a trailing comment (round-tripped as a UI note)
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
    """Convert a host dict into a ``dhcp-host=`` line string.

    Args:
        host: A host dict in the form returned by :func:`parse_config`.
            Prefixed with ``##`` if ``delete`` is true, or ``#`` if ``valid`` is false.

    Returns:
        A single-line string without a newline.
    """
    fields: list[str] = []
    # dnsmasq requires "id:..." to come before the MAC
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
        # Deletion double-comments with "##" so it is not picked up on reload
        head += '##'
    elif not host.get('valid', True) or not fields:
        head += '#'
    comment = host.get('comment') or ''
    if comment:
        comment = ' # ' + comment
    return head + DHCP_HOST_PREFIX + ', '.join(fields) + comment


def read_lines(path: str | None) -> list[str] | None:
    """Read a file as a list of lines.

    Args:
        path: Path of the file to read. Returns ``None`` if ``None`` or nonexistent.

    Returns:
        A list of lines, or ``None`` if it cannot be read.
    """
    if not path or not os.path.isfile(path):
        return None
    # surrogateescape preserves bytes that are invalid UTF-8 without loss.
    # Reading with errors='replace' would turn bytes in unchanged lines into
    # U+FFFD as well, since all lines are rewritten on save.
    with open(path, encoding='utf-8', errors='surrogateescape') as fobj:
        return fobj.readlines()


def stage_temp(path: str, lines: list[str]) -> str:
    """Write a list of lines to a temp file in the same directory as ``path``.

    Writing and replacing are separate steps so that validation before saving
    (``dnsmasq --test`` etc.) can run in between. The caller must dispose of
    the temp file with :func:`promote_staged` or ``os.unlink``.

    Args:
        path: Final destination path (the temp file is created in its directory).
        lines: Lines to write (each line includes its newline).

    Returns:
        Path of the created temp file.
    """
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.dnsmasq-webconf-')
    except PermissionError:
        # Some setups allow writing the target file but not its directory
        # (e.g. a single file bind-mounted in Docker and run with --user).
        # In that case create it in the system temp directory; on promotion,
        # promote_staged falls back to overwriting in place.
        fd, tmp_path = tempfile.mkstemp(prefix='.dnsmasq-webconf-')
    with os.fdopen(fd, 'w', encoding='utf-8', errors='surrogateescape') as fobj:
        fobj.writelines(lines)
        fobj.flush()
        os.fsync(fobj.fileno())
    # mkstemp creates files with 0600, so carry over the existing file's mode/owner.
    # Otherwise, in setups where dnsmasq runs as a different user, the config
    # file would become unreadable after the first save.
    if os.path.isfile(path):
        st = os.stat(path)
        os.chmod(tmp_path, stat_module.S_IMODE(st.st_mode))
        try:
            os.chown(tmp_path, st.st_uid, st.st_gid)
        except (PermissionError, OSError):
            # chown fails when not root, so leave it owned by the current user
            pass
    return tmp_path


def promote_staged(path: str, tmp_path: str, backup: bool = True) -> None:
    """Replace the actual destination with the validated temp file.

    Normally swaps it in atomically with ``os.replace``. When rename is not
    possible (e.g. a bind-mounted file or an unwritable parent directory),
    falls back to overwriting the existing file in place (the inode is kept).
    This path is not atomic, but there is no other way to save.

    Args:
        path: Destination path.
        tmp_path: Path of the temp file returned by :func:`stage_temp`.
        backup: If true, back up the existing file as ``<path>.bak``.
    """
    if backup and os.path.isfile(path):
        # Keep one generation of the previous content (copy2 keeps metadata)
        try:
            shutil.copy2(path, path + '.bak')
        except PermissionError as exc:
            # A backup cannot be created when the directory is not writable.
            # Saving itself is still possible, so just warn and continue.
            logger.warning('Could not create backup %s.bak: %s', path, exc)
    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        if exc.errno not in _RENAME_FALLBACK_ERRNOS:
            raise
        logger.info(
            'Cannot replace %s by rename (%s); overwriting it in place',
            path, errno.errorcode.get(exc.errno, exc.errno),
        )
        _overwrite_in_place(tmp_path, path)
        os.unlink(tmp_path)


def _overwrite_in_place(src_path: str, dst_path: str) -> None:
    """Overwrite ``dst_path`` with the content of ``src_path`` (keeps inode/mode).

    Args:
        src_path: File holding the content to write.
        dst_path: File to overwrite.
    """
    with open(src_path, 'rb') as src, open(dst_path, 'wb') as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def write_lines_atomic(path: str, lines: list[str], backup: bool = True) -> None:
    """Atomically write a list of lines to a file.

    Writes to a temp file in the same directory and swaps it in with
    ``os.replace``, so an interrupted write does not corrupt the config file.

    Args:
        path: Destination path.
        lines: Lines to write (each line includes its newline).
        backup: If true, back up the existing file as ``<path>.bak``.
    """
    tmp_path = stage_temp(path, lines)
    try:
        promote_staged(path, tmp_path, backup=backup)
    except BaseException:
        # Do not leave the temp file behind if promotion fails
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def apply_changes(
    lines: list[str],
    hosts: Iterable[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Apply changed hosts to the config file lines.

    Never rewrites non-``dhcp-host=`` lines or unchanged lines (non-destructive).
    When replacing an existing line, checks that it still matches the original
    the UI loaded; if not, it is reported as a conflict, not overwritten
    (optimistic locking).

    Args:
        lines: All lines of the current config file.
        hosts: Sequence of host dicts sent from the UI.

    Returns:
        A tuple ``(updated lines, report)``. The report is a sequence of
        ``{'status': 'appended'|'updated'|'conflict'|'out_of_range', ...}``.
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
        # Compare with the original at load time to detect external modification
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
