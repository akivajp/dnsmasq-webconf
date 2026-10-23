#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for authentication and CSRF checks."""

from __future__ import annotations

import pytest

from dnsmasq_webconf.auth import Credentials, is_same_origin


class TestCredentials:
    """Check parsing and verification of ``Credentials``."""

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
        """compare_digest does not raise even for non-ASCII passwords."""
        creds = Credentials('管理者', 'ひみつ')
        assert creds.verify('管理者', 'ひみつ') is True
        assert creds.verify('管理者', 'ちがう') is False


class TestSameOrigin:
    """Check CSRF decisions made by ``is_same_origin``."""

    def test_matching_origin_is_allowed(self) -> None:
        assert is_same_origin('http://192.168.1.1:8080', '192.168.1.1:8080') is True

    def test_cross_origin_is_rejected(self) -> None:
        assert is_same_origin('http://evil.example', '192.168.1.1:8080') is False

    def test_missing_origin_is_allowed_for_cli(self) -> None:
        """Do not block non-browser clients such as curl."""
        assert is_same_origin(None, '192.168.1.1:8080') is True
        assert is_same_origin('', '192.168.1.1:8080') is True

    def test_missing_host_with_origin_is_rejected(self) -> None:
        assert is_same_origin('http://evil.example', None) is False

    def test_scheme_difference_alone_does_not_matter(self) -> None:
        """Since netloc is compared, an http/https difference alone is not rejected."""
        assert is_same_origin('https://host:8080', 'host:8080') is True
