#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dnsmasq の静的 DHCP 予約を編集する軽量 Web UI。

既存の dnsmasq 設定ファイルを再生成せず、``dhcp-host=`` 行のうち
変更された行だけを差し替える「非破壊編集」を行う。
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

# パッケージ同梱のテンプレート/静的ファイルの所在
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
VIEW_DIR = os.path.join(PACKAGE_DIR, 'views')
STATIC_DIR = os.path.join(PACKAGE_DIR, 'static')

DEFAULT_HOSTS = '/etc/hosts'
DEFAULT_LEASES = '/var/lib/misc/dnsmasq.leases'
DEFAULT_CONFIG = '/etc/dnsmasq.more.conf'
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8080

# reload / test コマンドが暴走しても UI を巻き込まないための上限 (秒)
COMMAND_TIMEOUT = 30

# 保存前検証コマンド内のパス置き換えに使うプレースホルダ
TEST_PATH_PLACEHOLDER = '{path}'


class Settings:
    """サーバーの動作設定をまとめて保持する。"""

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
        """設定を初期化する。

        Args:
            hosts_file: hosts ファイルのパス (閲覧のみ)。
            leases_file: dnsmasq リースファイルのパス (閲覧のみ)。
            config_file: 編集対象の dnsmasq 設定ファイルのパス。
            reload_command: 保存後に実行するシェルコマンド。
            test_command: 保存前に実行する検証コマンド。``{path}`` を
                対象ファイルのパスへ置き換えて実行する。
            credentials: BASIC 認証の資格情報。``None`` なら認証なし。
            read_only: 真なら保存 API を無効化する。
            backup: 保存時に ``.bak`` を残すかどうか。
            refresh_interval: リース一覧の自動更新間隔 (秒)。0 で無効。
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
    """HTML の ``<script>`` 内へ安全に埋め込める JSON 文字列を生成する。

    ``<`` などをエスケープすることで、設定ファイルやリースファイルに
    ``</script>`` が含まれていても script ブロックから脱出できないようにする
    (DHCP クライアントが申告するホスト名は信頼できない入力である)。

    Args:
        obj: JSON 化する対象。

    Returns:
        エスケープ済みの JSON 文字列。
    """
    # ensure_ascii=True で非 ASCII 文字を \uXXXX にエスケープする。
    # surrogateescape で読み込んだ不正バイト (単独サロゲート) を含む文字列が
    # レスポンスの UTF-8 エンコード時に 500 になるのを防ぐため。
    text = json.dumps(obj, ensure_ascii=True)
    return (
        text.replace('<', '\\u003c')
            .replace('>', '\\u003e')
            .replace('&', '\\u0026')
            # JS では生の U+2028/U+2029 が行終端として解釈されるため潰す
            .replace(' ', '\\u2028')
            .replace(' ', '\\u2029')
    )


def run_command(
    command: str, path: str | None = None
) -> dict[str, Any]:
    """保存後のリロード / 保存前の検証コマンドを実行する。

    Args:
        command: 実行するシェルコマンド (サーバー管理者が起動時に指定したもの)。
        path: ``{path}`` プレースホルダを置き換えるファイルパス。検証コマンドで
            は一時ファイルのパスを渡し、リロードコマンドでは ``None`` を渡す。

    Returns:
        ``{'command', 'returncode', 'output'}`` を含む実行結果。
    """
    if path is not None:
        # 管理者がコマンドを組むときにパスをクォートできるよう、単純置換のみ行う
        command = command.replace(TEST_PATH_PLACEHOLDER, path)
    try:
        completed = subprocess.run(
            command,
            shell=True,  # 管理者が起動時に指定した文字列のみを実行する
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
    """設定から bottle アプリケーションを生成する。

    グローバル変数を使わずに設定を閉じ込めるため、ファクトリ形式としている
    (テストから複数の設定でインスタンス化できる)。

    Args:
        settings: サーバーの動作設定。

    Returns:
        ルート登録済みの bottle アプリケーション。
    """
    app = bottle.Bottle()
    require_auth = make_auth_plugin(settings.credentials)

    @app.route('/')
    @require_auth
    def index() -> str:
        """メイン画面を描画する。"""
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
            # 文字列 "null" が真と評価されてしまう問題を避けるため、
            # テンプレートの分岐には真偽値を明示的に渡す
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
            # 多層防御として Jinja2 の自動エスケープを有効にする。
            # JSON 変数は to_embedded_json() でエスケープ済みのため | safe を付す。
            template_settings={'autoescape': True},
            **context,
        )

    @app.route('/static/<path:path>')
    @require_auth
    def static(path: str) -> Any:
        """同梱の静的ファイルを配信する。"""
        # path 直下だけでなく vendor/ 以下も配信するため path フィルタを使う。
        # static_file() が root 外へのアクセスを防ぐ。
        return bottle.static_file(path, root=STATIC_DIR)

    @app.route('/api/leases')
    @require_auth
    def leases_api() -> str:
        """リース一覧を JSON で返す (UI による定期更新用)。"""
        bottle.response.headers['Content-Type'] = 'application/json'
        bottle.response.headers['Cache-Control'] = 'no-store'
        bottle.response.headers['X-Content-Type-Options'] = 'nosniff'
        lines = read_lines(settings.leases_file)
        leases = parse_leases(lines) if lines is not None else []
        # リース名は信頼できない入力のため、JSON API でも "<" 等をエスケープする
        return to_embedded_json(leases)

    @app.route('/api/save', method='POST')
    @require_auth
    def save() -> str:
        """変更されたホストを設定ファイルへ反映する。"""
        # BASIC 認証はブラウザが自動送信するため、CSRF 対策を別途行う
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

        # 1 件も変更が無い場合はファイルに触れない
        if report:
            conflicts = [
                r for r in report if r['status'] not in ('appended', 'updated')
            ]
            applied = len(report) - len(conflicts)
            result: dict[str, Any] = {'applied': applied, 'report': report}

            # 検証コマンドが指定されている場合、まず一時ファイルへ書き出し、
            # そのパスで検証する。合格するまで正式なファイルには触れないため、
            # 失敗時のロールバックが不要になる。
            tmp_path: str | None = stage_temp(settings.config_file, lines) if report else None
            try:
                if settings.test_command and tmp_path:
                    validation = run_command(settings.test_command, path=tmp_path)
                    if validation['returncode'] != 0:
                        # 検証に失敗した場合は書き込みを取りやめる (既存ファイルは無傷)
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
                # 未昇格の一時ファイルを必ず始末する
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

            result['status'] = 'OK' if applied else 'PARTIAL'
            # 変更が実際に書き込まれた場合のみ dnsmasq を再読み込みする
            if settings.reload_command and applied > 0:
                result['reload'] = run_command(settings.reload_command)
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({
            'status': 'OK', 'applied': 0, 'report': [],
        }, ensure_ascii=False)

    return app


def resolve_auth(spec: str | None) -> Credentials | None:
    """CLI の ``--auth`` 値を資格情報へ変換する。

    Args:
        spec: ``user:password`` 形式の文字列。``:`` が無い場合はユーザー名のみ
            と見なし、ターミナルからパスワードを対話入力する (シェル履歴や
            ``ps`` の出力にパスワードが残るのを避けるため)。

    Returns:
        解析済みの資格情報。``spec`` が空なら ``None``。

    Raises:
        ValueError: 形式が不正、または入力が空の場合。
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
    """待ち受けアドレスがループバックのみかを判定する。

    Args:
        host: 待ち受けアドレス文字列。

    Returns:
        ループバックアドレスなら ``True``。ホスト名など判定できない場合は ``False``。
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # "localhost" は名前解決せずにループバックと見なす
        return host == 'localhost'


def build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数のパーサを構築する。

    Returns:
        設定済みの :class:`argparse.ArgumentParser`。
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
        help=f'hosts ファイルのパス (既定: {DEFAULT_HOSTS})',
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
    """CLI のエントリポイント。

    Args:
        argv: コマンドライン引数。``None`` なら ``sys.argv[1:]`` を使う。

    Returns:
        終了コード (0 が正常)。
    """
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        format='[%(levelname)s] %(asctime)s %(message)s',
        level=logging.DEBUG if args.debug else logging.INFO,
    )

    # 認証情報はコマンドラインより環境変数を後置きで上書きしない
    # (ps から見えないよう、環境変数の利用を推奨する)
    try:
        credentials = resolve_auth(
            args.auth or os.environ.get('DNSMASQ_WEBCONF_AUTH')
        )
    except ValueError as exc:
        logger.error('--auth の指定が不正です: %s', exc)
        return 2

    # 認証なしで外部に公開すると、誰でも DHCP 予約を書き換えられてしまう
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

    # 起動時に実際に読み込める対象を表示しておく (設定ミスの早期発見のため)
    for label, path in (
        ('config', args.config), ('leases', args.leases), ('hosts', args.hosts),
    ):
        state = 'OK' if path and os.path.isfile(path) else '見つかりません'
        logger.info('%-7s %s (%s)', label, path, state)
    logger.info('認証: %s', '有効' if credentials else '無効')
    if args.read_only:
        logger.info('読み取り専用モードで起動します')

    app = create_app(settings)

    # 依存を追加した場合のみマルチスレッドの waitress を使う。
    # wsgiref (既定) はシングルスレッドなので、reload コマンドの実行中は
    # 他のリクエストが待たされてしまう。--debug 時は reloader が
    # waitress と併用できないため既定サーバーのままにする。
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
