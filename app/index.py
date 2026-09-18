#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""後方互換のための起動スクリプト。

v0.1 系では ``python app/index.py`` が唯一の起動方法だったため、
既存の systemd ユニットや手順書を壊さないよう互換シムとして残している。

新規に導入する場合は以下を推奨する::

    pip install dnsmasq-webconf
    dnsmasq-webconf --help

注意: v0.2 以降、既定の待ち受けアドレスは 0.0.0.0 から 127.0.0.1 に変更された。
LAN へ公開する場合は ``--host 0.0.0.0 --auth USER:PASSWORD`` を指定すること。
"""

import os
import sys

# リポジトリを clone しただけで実行された場合でもパッケージを解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dnsmasq_webconf.app import main  # noqa: E402

if __name__ == '__main__':
    sys.stderr.write(
        '[注意] app/index.py は後方互換のために残されています。'
        '今後は `dnsmasq-webconf` コマンドを使用してください。\n'
    )
    sys.exit(main())
