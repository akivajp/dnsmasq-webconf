#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""認証・CSRF 判定のテスト。"""

from __future__ import annotations

import pytest

from dnsmasq_webconf.auth import Credentials, is_same_origin


class TestCredentials:
    """``Credentials`` の解析と検証を確認する。"""

    def test_parse_user_and_password(self) -> None:
        creds = Credentials.parse('admin:secret')
        assert creds.user == 'admin'
        assert creds.password == 'secret'

    def test_password_may_contain_colon(self) -> None:
        creds = Credentials.parse('admin:a:b:c')
        assert creds.password == 'a:b:c'

    @pytest.mark.parametrize('spec', ['admin', '', ':secret', 'admin:'])
    def test_invalid_specs_are_rejected(self, spec: str) -> None:
        with pytest.raises(ValueError):
            Credentials.parse(spec)

    def test_verify_accepts_matching_pair(self) -> None:
        assert Credentials('admin', 'secret').verify('admin', 'secret') is True

    @pytest.mark.parametrize(
        'user,password',
        [('admin', 'wrong'), ('wrong', 'secret'), (None, None), ('admin', None)],
    )
    def test_verify_rejects_mismatch(self, user, password) -> None:
        assert Credentials('admin', 'secret').verify(user, password) is False

    def test_verify_handles_non_ascii(self) -> None:
        """非 ASCII のパスワードでも compare_digest が例外を投げない。"""
        creds = Credentials('管理者', 'ひみつ')
        assert creds.verify('管理者', 'ひみつ') is True
        assert creds.verify('管理者', 'ちがう') is False


class TestSameOrigin:
    """``is_same_origin`` による CSRF 判定を確認する。"""

    def test_matching_origin_is_allowed(self) -> None:
        assert is_same_origin('http://192.168.1.1:8080', '192.168.1.1:8080') is True

    def test_cross_origin_is_rejected(self) -> None:
        assert is_same_origin('http://evil.example', '192.168.1.1:8080') is False

    def test_missing_origin_is_allowed_for_cli(self) -> None:
        """curl などブラウザ以外からの利用を妨げない。"""
        assert is_same_origin(None, '192.168.1.1:8080') is True
        assert is_same_origin('', '192.168.1.1:8080') is True

    def test_missing_host_with_origin_is_rejected(self) -> None:
        assert is_same_origin('http://evil.example', None) is False

    def test_scheme_difference_alone_does_not_matter(self) -> None:
        """netloc 比較のため、http/https の違いだけでは拒否しない。"""
        assert is_same_origin('https://host:8080', 'host:8080') is True
