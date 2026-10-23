#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight web UI for editing dnsmasq static DHCP reservations.

Performs "non-destructive editing": instead of regenerating the existing
dnsmasq config file, it replaces only the ``dhcp-host=`` lines that changed.
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import json
import logging
import os
import subprocess
import sys
from typing import Any

import bottle

from . import __version__
from .auth import Credentials, make_auth_plugin, require_same_origin
from .config import (
    apply_changes,
    parse_config,
    parse_hosts,
    parse_leases,
    promote_staged,
    read_lines,
    stage_temp,
)

logger = logging.getLogger(__name__)

# Location of the templates/static files bundled with the package
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
VIEW_DIR = os.path.join(PACKAGE_DIR, 'views')
STATIC_DIR = os.path.join(PACKAGE_DIR, 'static')

DEFAULT_HOSTS = '/etc/hosts'
DEFAULT_LEASES = '/var/lib/misc/dnsmasq.leases'
DEFAULT_CONFIG = '/etc/dnsmasq.more.conf'
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8080

# Upper limit (seconds) so a runaway reload/test command does not hang the UI
COMMAND_TIMEOUT = 30

# Placeholder replaced with the file path in the pre-save validation command
TEST_PATH_PLACEHOLDER = '{path}'


class Settings:
    """Holds the server's runtime settings."""

    def __init__(
        self,
        hosts_file: str | None = None,
        leases_file: str | None = None,
        config_file: str | None = None,
        reload_command: str | None = None,
        test_command: str | None = None,
        credentials: Credentials | None = None,
        read_only: bool = False,
        backup: bool = True,
        refresh_interval: int = 30,
    ) -> None:
        """Initialize the settings.

        Args:
            hosts_file: Path to the hosts file (view only).
            leases_file: Path to the dnsmasq leases file (view only).
            config_file: Path to the dnsmasq config file to edit.
            reload_command: Shell command to run after saving.
            test_command: Validation command to run before saving. ``{path}``
                is replaced with the path of the target file.
            credentials: BASIC auth credentials. ``None`` means no auth.
            read_only: If true, the save API is disabled.
            backup: Whether to keep a ``.bak`` file when saving.
            refresh_interval: Auto-refresh interval of the lease list
                (seconds). 0 disables it.
        """
        self.hosts_file = hosts_file
        self.leases_file = leases_file
        self.config_file = config_file
        self.reload_command = reload_command
        self.test_command = test_command
        self.credentials = credentials
        self.read_only = read_only
        self.backup = backup
        self.refresh_interval = refresh_interval


def to_embedded_json(obj: Any) -> str:
    """Build a JSON string that can be safely embedded in an HTML ``<script>``.

    Escaping ``<`` and similar characters ensures that a ``</script>`` in the
    config or leases file cannot break out of the script block
    (hostnames reported by DHCP clients are untrusted input).

    Args:
        obj: Object to serialize as JSON.

    Returns:
        The escaped JSON string.
    """
    # ensure_ascii=True escapes non-ASCII characters as \uXXXX.
    # This prevents a 500 when a string containing invalid bytes read via
    # surrogateescape (lone surrogates) is UTF-8 encoded in the response.
    text = json.dumps(obj, ensure_ascii=True)
    return (
        text.replace('<', '\\u003c')
            .replace('>', '\\u003e')
            .replace('&', '\\u0026')
            # Escape raw U+2028/U+2029, which JS treats as line terminators
            .replace(' ', '\\u2028')
            .replace(' ', '\\u2029')
    )


def run_command(
    command: str, path: str | None = None
) -> dict[str, Any]:
    """Run the post-save reload command or the pre-save validation command.

    Args:
        command: Shell command to run (specified by the server admin at startup).
        path: File path that replaces the ``{path}`` placeholder. Pass the temp
            file path for the validation command and ``None`` for the reload.

    Returns:
        Result containing ``{'command', 'returncode', 'output'}``.
    """
    if path is not None:
        # Plain substitution only, so the admin can quote the path in the command
        command = command.replace(TEST_PATH_PLACEHOLDER, path)
    try:
        completed = subprocess.run(
            command,
            shell=True,  # Only runs the string the admin specified at startup
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error('コマンドがタイムアウトしました: %s', command)
        return {'command': command, 'returncode': None, 'output': 'timeout'}
    output = (completed.stdout + completed.stderr).strip()[:2000]
    if completed.returncode != 0:
        logger.error(
            'コマンドが失敗しました (code=%s): %s', completed.returncode, output
        )
    return {
        'command': command,
        'returncode': completed.returncode,
        'output': output,
    }


def create_app(settings: Settings) -> bottle.Bottle:
    """Create a bottle application from the settings.

    Uses a factory so the settings are enclosed without global variables
    (tests can instantiate it with multiple settings).

    Args:
        settings: The server's runtime settings.

    Returns:
        A bottle application with its routes registered.
    """
    app = bottle.Bottle()
    require_auth = make_auth_plugin(settings.credentials)

    @app.route('/')
    @require_auth
    def index() -> str:
        """Render the main page."""
        hosts_lines = read_lines(settings.hosts_file)
        leases_lines = read_lines(settings.leases_file)
        config_lines = read_lines(settings.config_file)

        hosts = parse_hosts(hosts_lines) if hosts_lines is not None else None
        leases = parse_leases(leases_lines) if leases_lines is not None else None
        config = parse_config(config_lines) if config_lines is not None else None

        context = dict(
            hosts_json=to_embedded_json(hosts),
            leases_json=to_embedded_json(leases),
            config_json=to_embedded_json(config),
            hosts_file=settings.hosts_file,
            leases_file=settings.leases_file,
            config_file=settings.config_file,
            # Pass explicit booleans for template branches, since the
            # string "null" would otherwise evaluate as true
            has_hosts=hosts is not None,
            has_leases=leases is not None,
            has_config=config is not None,
            read_only=settings.read_only,
            refresh_interval=settings.refresh_interval,
            version=__version__,
            timestamp=datetime.datetime.now().strftime('%y/%m/%d-%H:%M:%S'),
        )
        return bottle.jinja2_template(
            'main.html.j2',
            template_lookup=[VIEW_DIR],
            # Enable Jinja2 autoescaping as defense in depth.
            # JSON variables get | safe, as to_embedded_json() already escaped them.
            template_settings={'autoescape': True},
            **context,
        )

    @app.route('/static/<path:path>')
    @require_auth
    def static(path: str) -> Any:
        """Serve the bundled static files."""
        # Use the path filter to also serve files under vendor/, not just the top level.
        # static_file() prevents access outside root.
        return bottle.static_file(path, root=STATIC_DIR)

    @app.route('/api/leases')
    @require_auth
    def leases_api() -> str:
        """Return the lease list as JSON (for periodic refresh by the UI)."""
        bottle.response.headers['Content-Type'] = 'application/json'
        bottle.response.headers['Cache-Control'] = 'no-store'
        bottle.response.headers['X-Content-Type-Options'] = 'nosniff'
        lines = read_lines(settings.leases_file)
        leases = parse_leases(lines) if lines is not None else []
        # Lease names are untrusted input, so escape "<" etc. in the JSON API too
        return to_embedded_json(leases)

    @app.route('/api/save', method='POST')
    @require_auth
    def save() -> str:
        """Apply the changed hosts to the config file."""
        # Browsers send BASIC auth automatically, so CSRF needs separate protection
        require_same_origin()
        bottle.response.headers['Content-Type'] = 'application/json'
        bottle.response.headers['Cache-Control'] = 'no-store'

        if settings.read_only:
            raise bottle.HTTPError(403, '読み取り専用モードで動作しています')
        if not settings.config_file:
            raise bottle.HTTPError(400, '設定ファイルが指定されていません')

        data = bottle.request.json
        if not isinstance(data, dict):
            raise bottle.HTTPError(400, 'JSON 形式の本文が必要です')

        lines = read_lines(settings.config_file) or []
        targets = list(data.get('hosts') or []) + list(data.get('ignored_hosts') or [])
        lines, report = apply_changes(lines, targets)

        # Don't touch the file if nothing changed
        if report:
            conflicts = [
                r for r in report if r['status'] not in ('appended', 'updated')
            ]
            applied = len(report) - len(conflicts)
            result: dict[str, Any] = {'applied': applied, 'report': report}

            # If a validation command is set, first write to a temp file and
            # validate that path. The real file is not touched until it passes,
            # so no rollback is needed on failure.
            tmp_path: str | None = stage_temp(settings.config_file, lines) if report else None
            try:
                if settings.test_command and tmp_path:
                    validation = run_command(settings.test_command, path=tmp_path)
                    if validation['returncode'] != 0:
                        # Abort the write if validation fails (existing file is untouched)
                        return json.dumps({
                            'status': 'REJECTED',
                            'applied': 0,
                            'report': report,
                            'validation': validation,
                        }, ensure_ascii=False)
                if tmp_path:
                    promote_staged(
                        settings.config_file, tmp_path, backup=settings.backup
                    )
                    tmp_path = None
            finally:
                # Always clean up a temp file that was not promoted
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            result['status'] = 'OK' if applied else 'PARTIAL'
            # Reload dnsmasq only if changes were actually written
            if settings.reload_command and applied > 0:
                result['reload'] = run_command(settings.reload_command)
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({
            'status': 'OK', 'applied': 0, 'report': [],
        }, ensure_ascii=False)

    return app


def resolve_auth(spec: str | None) -> Credentials | None:
    """Convert the CLI ``--auth`` value into credentials.

    Args:
        spec: A ``user:password`` string. Without ``:`` it is treated as a
            username only, and the password is prompted for on the terminal
            (to keep the password out of shell history and ``ps`` output).

    Returns:
        The parsed credentials, or ``None`` if ``spec`` is empty.

    Raises:
        ValueError: If the format is invalid or the input is empty.
    """
    if not spec:
        return None
    if ':' in spec:
        return Credentials.parse(spec)
    import getpass
    password = getpass.getpass(f'BASIC 認証のパスワード ({spec}): ')
    if not password:
        raise ValueError('パスワードが入力されませんでした')
    return Credentials(spec, password)


def is_loopback(host: str) -> bool:
    """Determine whether the listen address is loopback only.

    Args:
        host: Listen address string.

    Returns:
        ``True`` for a loopback address; ``False`` if undeterminable (e.g. a hostname).
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Treat "localhost" as loopback without resolving it
        return host == 'localhost'


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        A configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog='dnsmasq-webconf',
        description='dnsmasq の静的 DHCP 予約を編集する軽量 Web UI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '例:\n'
            '  # ローカルからのみ利用する (既定)\n'
            '  dnsmasq-webconf --config /etc/dnsmasq.more.conf\n'
            '\n'
            '  # LAN へ公開する (認証が必須)\n'
            '  dnsmasq-webconf --host 0.0.0.0 --auth admin:secret \\\n'
            '      --config /etc/dnsmasq.more.conf \\\n'
            '      --test-command \'dnsmasq --test -C "{path}"\' \\\n'
            '      --reload "systemctl reload dnsmasq"\n'
        ),
    )
    parser.add_argument(
        'port', nargs='?', type=int, default=DEFAULT_PORT,
        help=f'待ち受けポート番号 (既定: {DEFAULT_PORT})',
    )
    parser.add_argument(
        '--host', type=str, default=DEFAULT_HOST,
        help=f'待ち受けアドレス (既定: {DEFAULT_HOST})',
    )
    parser.add_argument(
        '--hosts', '-H', type=str, default=DEFAULT_HOSTS,
        help=f'hosts ファイルのパス。空文字 (\'\') で一覧を非表示 (既定: {DEFAULT_HOSTS})',
    )
    parser.add_argument(
        '--leases', '-L', type=str, default=DEFAULT_LEASES,
        help=f'dnsmasq リースファイルのパス (既定: {DEFAULT_LEASES})',
    )
    parser.add_argument(
        '--config', '-C', type=str, default=DEFAULT_CONFIG,
        help=f'編集対象の dnsmasq 設定ファイル (既定: {DEFAULT_CONFIG})',
    )
    parser.add_argument(
        '--reload-command', '--reload', '-R', type=str, default=None,
        help='保存後に実行するコマンド (例: "systemctl reload dnsmasq")',
    )
    parser.add_argument(
        '--test-command', '-T', type=str, default=None,
        help=(
            '保存前に実行する検証コマンド。{path} が対象ファイルのパスに置き換わる '
            '(例: \'dnsmasq --test -C "{path}"\')。合格しなければ書き込みを行わない'
        ),
    )
    parser.add_argument(
        '--refresh-interval', type=int, default=30, metavar='SECONDS',
        help='リース一覧の自動更新間隔 (秒)。0 で無効化 (既定: 30)',
    )
    parser.add_argument(
        '--auth', '-A', type=str, default=None, metavar='USER:PASSWORD',
        help='BASIC 認証を有効にする。環境変数 DNSMASQ_WEBCONF_AUTH でも指定可',
    )
    parser.add_argument(
        '--allow-no-auth', action='store_true',
        help='認証なしで外部アドレスへ公開することを明示的に許可する (非推奨)',
    )
    parser.add_argument(
        '--read-only', action='store_true',
        help='閲覧専用で起動し、保存 API を無効化する',
    )
    parser.add_argument(
        '--no-backup', action='store_true',
        help='保存時に .bak ファイルを作成しない',
    )
    parser.add_argument(
        '--debug', action='store_true',
        help='デバッグモードで起動する (詳細ログと自動リロード。公開環境では使用しないこと)',
    )
    parser.add_argument(
        '--version', action='version', version=f'%(prog)s {__version__}',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments. ``None`` means ``sys.argv[1:]``.

    Returns:
        Exit code (0 on success).
    """
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        format='[%(levelname)s] %(asctime)s %(message)s',
        level=logging.DEBUG if args.debug else logging.INFO,
    )

    # For credentials, the environment variable does not override the command line
    # (using the environment variable is recommended, so they are hidden from ps)
    try:
        credentials = resolve_auth(
            args.auth or os.environ.get('DNSMASQ_WEBCONF_AUTH')
        )
    except ValueError as exc:
        logger.error('--auth の指定が不正です: %s', exc)
        return 2

    # Exposing without auth would let anyone rewrite the DHCP reservations
    if credentials is None and not is_loopback(args.host) and not args.read_only:
        if not args.allow_no_auth:
            logger.error(
                '認証なしで %s へ公開しようとしています。'
                '--auth USER:PASSWORD を指定するか、'
                '意図的であれば --allow-no-auth を付けてください。',
                args.host,
            )
            return 2
        logger.warning(
            '認証なしで %s へ公開しています。'
            'ネットワーク上の誰でも dnsmasq の設定を書き換えられます。',
            args.host,
        )

    settings = Settings(
        hosts_file=args.hosts,
        leases_file=args.leases,
        config_file=args.config,
        reload_command=args.reload_command,
        test_command=args.test_command,
        credentials=credentials,
        read_only=args.read_only,
        backup=not args.no_backup,
        refresh_interval=args.refresh_interval,
    )

    # Show which files can actually be read at startup (to catch misconfiguration early)
    for label, path in (
        ('config', args.config), ('leases', args.leases), ('hosts', args.hosts),
    ):
        state = 'OK' if path and os.path.isfile(path) else '見つかりません'
        logger.info('%-7s %s (%s)', label, path, state)
    logger.info('認証: %s', '有効' if credentials else '無効')
    if args.read_only:
        logger.info('読み取り専用モードで起動します')

    app = create_app(settings)

    # Use multithreaded waitress only if that dependency is installed.
    # wsgiref (the default) is single-threaded, so other requests wait while
    # the reload command runs. With --debug, keep the default server because
    # the reloader cannot be used together with waitress.
    server = 'wsgiref'
    if not args.debug:
        try:
            import waitress  # noqa: F401
            server = 'waitress'
            logger.info('HTTP サーバー: waitress (マルチスレッド)')
        except ImportError:
            logger.info('HTTP サーバー: wsgiref ( waitress を導入するとマルチスレッドになります)')
    bottle.run(
        app=app,
        host=args.host,
        port=args.port,
        server=server,
        quiet=not args.debug,
        debug=args.debug,
        reloader=args.debug,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
