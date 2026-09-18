#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""アクセス制御 (BASIC 認証と CSRF 対策)。

BASIC 認証の資格情報はブラウザが自動送信するため、認証だけでは
クロスサイトからの書き込みを防げない。そのため書き込み系エンドポイントでは
``Origin`` ヘッダによる同一オリジン検証を併用する。
"""

from __future__ import annotations

import functools
import hmac
from typing import Any, Callable
from urllib.parse import urlsplit


class Credentials:
    """BASIC 認証の資格情報を保持する。"""

    def __init__(self, user: str, password: str) -> None:
        """資格情報を初期化する。

        Args:
            user: ユーザー名。
            password: パスワード。
        """
        self.user = user
        self.password = password

    @classmethod
    def parse(cls, spec: str) -> 'Credentials':
        """``user:password`` 形式の文字列を解析する。

        Args:
            spec: ``user:password`` 形式の文字列。
                パスワードに ``:`` を含めてもよい (最初の ``:`` で分割する)。

        Returns:
            解析済みの :class:`Credentials`。

        Raises:
            ValueError: 形式が不正、またはユーザー名かパスワードが空の場合。
        """
        if ':' not in spec:
            raise ValueError('認証情報は "user:password" 形式で指定してください')
        user, password = spec.split(':', 1)
        if not user or not password:
            raise ValueError('ユーザー名とパスワードは空にできません')
        return cls(user, password)

    def verify(self, user: str | None, password: str | None) -> bool:
        """資格情報が一致するかを検証する。

        タイミング攻撃を避けるため :func:`hmac.compare_digest` を用いる。

        Args:
            user: 検証するユーザー名。
            password: 検証するパスワード。

        Returns:
            一致すれば ``True``。
        """
        if user is None or password is None:
            return False
        # compare_digest は非 ASCII の str を受け付けないためバイト列で比較する。
        # 短絡評価による情報漏洩を避けるため、両方を必ず評価する。
        user_ok = hmac.compare_digest(user.encode('utf-8'), self.user.encode('utf-8'))
        password_ok = hmac.compare_digest(
            password.encode('utf-8'), self.password.encode('utf-8')
        )
        return user_ok and password_ok


def is_same_origin(origin: str | None, host: str | None) -> bool:
    """``Origin`` ヘッダが自ホストと一致するかを判定する。

    Args:
        origin: ``Origin`` ヘッダの値。``None`` や空文字ならブラウザ以外からの
            リクエスト (curl 等) と見なして許可する。
        host: ``Host`` ヘッダの値。

    Returns:
        同一オリジンと見なせる場合に ``True``。
    """
    if not origin:
        # ブラウザはクロスオリジンの書き込みで必ず Origin を送るため、
        # 欠落している場合は CLI からの利用と判断して通す。
        return True
    if not host:
        return False
    # "https://example.com:8080" から "example.com:8080" を取り出して比較する
    return urlsplit(origin).netloc == host


def make_auth_plugin(
    credentials: Credentials | None,
    realm: str = 'dnsmasq-webconf',
) -> Callable[..., Any]:
    """bottle 用の認証デコレータを生成する。

    Args:
        credentials: 要求する資格情報。``None`` なら認証を行わない。
        realm: BASIC 認証のレルム名。

    Returns:
        ルート関数を包むデコレータ。
    """
    # bottle は import 時に副作用があるため、利用箇所でのみ読み込む
    import bottle

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if credentials is not None:
                auth = bottle.request.auth  # (user, password) または None
                if not credentials.verify(*(auth or (None, None))):
                    # WWW-Authenticate を返してブラウザに認証を促す
                    raise bottle.HTTPError(
                        401,
                        '認証が必要です',
                        **{'WWW-Authenticate': f'Basic realm="{realm}"'},
                    )
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_same_origin() -> None:
    """現在のリクエストが同一オリジンであることを確認する。

    Raises:
        bottle.HTTPError: クロスオリジンからの要求だった場合 (403)。
    """
    import bottle

    origin = bottle.request.get_header('Origin')
    host = bottle.request.get_header('Host')
    if not is_same_origin(origin, host):
        raise bottle.HTTPError(403, 'クロスオリジンからの要求は拒否されました')
