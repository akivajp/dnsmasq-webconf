#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Access control (BASIC authentication and CSRF protection).

Browsers send BASIC auth credentials automatically, so authentication alone
cannot prevent cross-site writes. Write endpoints therefore also verify
same-origin via the ``Origin`` header.
"""

from __future__ import annotations

import functools
import hmac
from typing import Any, Callable
from urllib.parse import urlsplit


class Credentials:
    """Holds BASIC authentication credentials."""

    def __init__(self, user: str, password: str) -> None:
        """Initialize the credentials.

        Args:
            user: User name.
            password: Password.
        """
        self.user = user
        self.password = password

    @classmethod
    def parse(cls, spec: str) -> 'Credentials':
        """Parse a string of the form ``user:password``.

        Args:
            spec: A string of the form ``user:password``.
                The password may contain ``:`` (split at the first ``:``).

        Returns:
            The parsed :class:`Credentials`.

        Raises:
            ValueError: If the format is invalid, or the user name or password is empty.
        """
        if ':' not in spec:
            raise ValueError('認証情報は "user:password" 形式で指定してください')
        user, password = spec.split(':', 1)
        if not user or not password:
            raise ValueError('ユーザー名とパスワードは空にできません')
        return cls(user, password)

    def verify(self, user: str | None, password: str | None) -> bool:
        """Verify whether the given credentials match.

        Uses :func:`hmac.compare_digest` to avoid timing attacks.

        Args:
            user: User name to verify.
            password: Password to verify.

        Returns:
            ``True`` if they match.
        """
        if user is None or password is None:
            return False
        # compare_digest rejects non-ASCII str, so compare as bytes.
        # Always evaluate both to avoid leaking information via short-circuiting.
        user_ok = hmac.compare_digest(user.encode('utf-8'), self.user.encode('utf-8'))
        password_ok = hmac.compare_digest(
            password.encode('utf-8'), self.password.encode('utf-8')
        )
        return user_ok and password_ok


def is_same_origin(origin: str | None, host: str | None) -> bool:
    """Determine whether the ``Origin`` header matches this host.

    Args:
        origin: Value of the ``Origin`` header. If ``None`` or empty, the request
            is treated as coming from a non-browser client (curl, etc.) and allowed.
        host: Value of the ``Host`` header.

    Returns:
        ``True`` if the request can be considered same-origin.
    """
    if not origin:
        # Browsers always send Origin on cross-origin writes, so when it is
        # missing, treat the request as CLI usage and let it through.
        return True
    if not host:
        return False
    # Extract "example.com:8080" from "https://example.com:8080" and compare
    return urlsplit(origin).netloc == host


def make_auth_plugin(
    credentials: Credentials | None,
    realm: str = 'dnsmasq-webconf',
) -> Callable[..., Any]:
    """Create an authentication decorator for bottle.

    Args:
        credentials: Required credentials. If ``None``, no authentication is done.
        realm: Realm name for BASIC authentication.

    Returns:
        A decorator that wraps route functions.
    """
    # bottle has side effects on import, so import it only where it is used
    import bottle

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if credentials is not None:
                auth = bottle.request.auth  # (user, password) or None
                if not credentials.verify(*(auth or (None, None))):
                    # Return WWW-Authenticate to prompt the browser to authenticate
                    raise bottle.HTTPError(
                        401,
                        '認証が必要です',
                        **{'WWW-Authenticate': f'Basic realm="{realm}"'},
                    )
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_same_origin() -> None:
    """Ensure the current request is same-origin.

    Raises:
        bottle.HTTPError: If the request is cross-origin (403).
    """
    import bottle

    origin = bottle.request.get_header('Origin')
    host = bottle.request.get_header('Host')
    if not is_same_origin(origin, host):
        raise bottle.HTTPError(403, 'クロスオリジンからの要求は拒否されました')
