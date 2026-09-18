# Dnsmasq WebConf

[![CI](https://github.com/akivajp/dnsmasq-webconf/actions/workflows/ci.yml/badge.svg)](https://github.com/akivajp/dnsmasq-webconf/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dnsmasq-webconf)](https://pypi.org/project/dnsmasq-webconf/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

dnsmasq の**静的 DHCP 予約**を管理する軽量 Web UI です。
設定ファイルの所有権を奪わないことを最大の特徴としています。

[English README](README.md)

![スクリーンショット](https://raw.githubusercontent.com/akivajp/dnsmasq-webconf/master/docs/screenshot.png)

## このツールの位置づけ

dnsmasq のフロントエンドの多く (Pi-hole、ルーター用ファームウェア、各種設定ジェネレーター) は、
設定ファイルを**自ら生成・所有**します。`dnsmasq.conf` は独自データベースから出力されるため、
手書きしたディレクティブは別の場所へ移動させられるか、失われます。

`dnsmasq-webconf` は逆のアプローチを取ります。既存の設定ファイルをそのまま読み込み、
保存時には**実際に変更した `dhcp-host=` 行だけを書き換えます**。
それ以外の行 — `dhcp-range=`、`dhcp-option=`、コメント、行の並び順 — は
1 バイトも変更されません。

そのため、次のような状況に向いています。

> すでに dnsmasq を運用していて、今後もテキストファイルとして管理したい。
> ただし、新しい端末が増えるたびに MAC アドレスを手で書き足すのは避けたい。

DNS・DHCP・広告ブロックを一体で提供するアプライアンスが欲しい場合は、
[Pi-hole](https://pi-hole.net/) を使ってください。機能面では比較にならないほど高機能であり、
本ツールはそこと competing する意図を持ちません。[代替ツール](#代替ツール)も参照してください。

## 機能

- **静的 DHCP 予約の管理** — `dhcp-host=` エントリの追加・編集・並べ替え・
  コメントアウト・削除
- **リースからワンクリックで予約化** — DHCP リース一覧に現れた端末の *Add Static* を押すだけで
  固定予約に変換
- **ブロックリスト管理** — MAC を `ignore` 指定し、dnsmasq に応答させないようにする
- **ホストごとのメモ** — コメントは設定ファイル中の `#` コメントとして往復するため、
  手で開いたときにも読める
- **閲覧専用ビュー** — DHCP リース一覧とシステムの `hosts` ファイル
- **保存後のリロード** — `systemctl reload dnsmasq` などを任意に実行可能
- **保存前の検証** — 確定前の設定に対して `dnsmasq --test` を実行し、
  合格しなければ書き込み自体を行わない (既存ファイルは無傷)
- **リース一覧の自動更新** — 閲覧専用のリース表を定期的に再取得するため、
  新しい端末が接続されたらすぐ予約化できる
- **絞り込み検索** — ホスト名・IP・MAC・コメントの断片を入力すると全テーブルを一括で絞り込み。
  未保存の変更があるままページを離れようとすると確認ダイアログを出す
- **データベース・ビルド手順・JS ツールチェーン不要** — Python の依存は 2 つだけ。
  CSS/JS はすべて同梱しているため、インターネットに接続できない閉域ネットワークでも動作します

## インストール

### PyPI から (推奨)

```shell
pipx install dnsmasq-webconf
# または: uv tool install dnsmasq-webconf
# 仮想環境内なら: pip install dnsmasq-webconf
```

> 近年の Debian / Ubuntu / Raspberry Pi OS では
> [PEP 668](https://peps.python.org/pep-0668/) により `pip install --user` が拒否されます。
> `pipx` または `uv tool` を利用してください。

### Docker を使う場合

ビルド済みイメージを GHCR で公開しています:

```shell
docker run --rm -p 8080:8080 \
    --user "$(id -u):$(id -g)" \
    -e DNSMASQ_WEBCONF_AUTH='admin:secret' \
    -v /etc/dnsmasq.more.conf:/etc/dnsmasq.more.conf \
    -v /var/lib/misc/dnsmasq.leases:/var/lib/misc/dnsmasq.leases:ro \
    ghcr.io/akivajp/dnsmasq-webconf:latest
```

自分でビルドする場合:

```shell
docker build -t dnsmasq-webconf .
docker run --rm -p 8080:8080 \
    --user "$(id -u):$(id -g)" \
    -e DNSMASQ_WEBCONF_AUTH='admin:secret' \
    -v /etc/dnsmasq.more.conf:/etc/dnsmasq.more.conf \
    -v /var/lib/misc/dnsmasq.leases:/var/lib/misc/dnsmasq.leases:ro \
    dnsmasq-webconf
```

### ソースから

```shell
git clone https://github.com/akivajp/dnsmasq-webconf.git
cd dnsmasq-webconf
pip install -e .
```

## 使い方

```shell
dnsmasq-webconf --config /etc/dnsmasq.more.conf
```

ブラウザで <http://127.0.0.1:8080> を開きます。

既定では**ループバックのみ**を待ち受けます。LAN へ公開する場合は認証の設定が必須です。

```shell
dnsmasq-webconf --host 0.0.0.0 --auth admin:secret \
    --config /etc/dnsmasq.more.conf \
    --test-command 'dnsmasq --test -C "{path}"' \
    --reload "systemctl reload dnsmasq"
```

`--test-command` は、確定前の設定を dnsmasq 本体で検証するオプションです。
`{path}` は検証対象ファイルのパスに置き換えられ (例のように引用符で囲むこと)、
検証が非 0 で終了した場合は**書き込みを行わず既存の設定をそのまま残します**。
`--reload-command` と併用する場合は必ず設定することを推奨します
(MAC アドレスのタイポが動作中の dnsmasq に到達するのを防げるため)。

リース一覧は既定で 30 秒ごとに自動更新されます。`--refresh-interval 0` で無効化できます。

シェル履歴や `ps` の出力にパスワードを残さないため、`--auth` の代わりに
環境変数を使うか、ユーザー名だけを渡してパスワードをターミナルで入力する方法があります。

```shell
DNSMASQ_WEBCONF_AUTH='admin:secret' dnsmasq-webconf --host 0.0.0.0 ...
# または:
dnsmasq-webconf --host 0.0.0.0 --auth admin --config /etc/dnsmasq.more.conf
```

既定では Python 標準の `wsgiref` (シングルスレッド) を使用します。任意の
`server` extra を導入するとマルチスレッドのサーバーが使われ、
reload コマンドの実行中に UI が待たされなくなります。

```shell
pipx install dnsmasq-webconf[server]
```

全オプションは `dnsmasq-webconf --help` で確認できます。

### 推奨する dnsmasq 側の構成

メインの `dnsmasq.conf` ではなく、専用の include ファイルを対象にすることを推奨します。
万一設定を壊しても名前解決そのものを止めずに済みます。

```conf
# /etc/dnsmasq.conf
conf-file=/etc/dnsmasq.more.conf
```

```shell
sudo touch /etc/dnsmasq.more.conf
dnsmasq-webconf --config /etc/dnsmasq.more.conf
```

### サービスとして常駐させる

```ini
# /etc/systemd/system/dnsmasq-webconf.service
[Unit]
Description=Dnsmasq WebConf
After=network.target

[Service]
# 設定ファイルへの書き込み権限が必要。環境に合わせて調整すること。
User=root
Environment=DNSMASQ_WEBCONF_AUTH=admin:secret
ExecStart=/usr/local/bin/dnsmasq-webconf 8080 \
    --host 0.0.0.0 \
    --config /etc/dnsmasq.more.conf \
    --reload "systemctl reload dnsmasq"
# 任意のハードニング: 設定ファイル以外への書き込みを禁止する
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/etc/dnsmasq.more.conf
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## セキュリティ

本ツールはネットワークの DHCP サーバーの設定を書き換えます。
書き込み権限を得た者は、LAN 上のあらゆる端末に任意の IP アドレス・ゲートウェイ・
DNS サーバーを配布できます。アクセス権はそれに見合った扱いをしてください。

- **認証もネットワーク公開も既定では無効です。** `--host` を指定しない限り
  `127.0.0.1` を待ち受けます。`--auth` なしで非ループバックアドレスへ公開しようとすると
  起動を拒否します (他の手段でポートを保護している場合のみ `--allow-no-auth` で解除可能)。
- **BASIC 認証は暗号化されません。** 信頼できないネットワークでは TLS 終端する
  リバースプロキシの背後に置くか、VPN / SSH トンネル経由で利用してください。
  例: `ssh -L 8080:127.0.0.1:8080 your-server`
- **書き込みは CSRF 対策済みです。** ブラウザが BASIC 認証の資格情報を自動送信するため、
  `Origin` ヘッダによる同一オリジン検証を併用しています。
- **`--reload-command` はシェル経由で実行されます。** 実行されるのは管理者が
  コマンドラインで指定した文字列のみですが、信頼できない入力から組み立てないでください。
- ダッシュボードとしてのみ使う場合は `--read-only` を利用してください。

### 脆弱性の報告

公開 Issue ではなく
[セキュリティアドバイザリ](https://github.com/akivajp/dnsmasq-webconf/security/advisories/new)
からご連絡ください。

> **v0.1.x からの移行についての注意**
> 既定の待ち受けアドレスが `0.0.0.0` から `127.0.0.1` に変更されました。
> また v0.1.x の保存 API には**認証が一切ありませんでした**。
> LAN 上で運用していた場合、そのポートに到達できる者は誰でも設定を書き換えられた状態でした。
> 詳細は [CHANGELOG.md](CHANGELOG.md) を参照してください。

## 保存処理の仕組み

1. ブラウザは変更したエントリのみを、**取得元の行番号**と**その行の原文**を添えて送信します。
2. サーバーは設定ファイルを読み直し、変更対象の各エントリについて、
   該当行がブラウザの見ていた内容と一致するかを検証します。
3. 一致すればその 1 行だけを置き換えます。一致しない場合 (手動で編集した、
   別のセッションが先に保存した等) は、**黙って上書きせず、スキップして結果を通知**します。
4. 書き込みは同一ディレクトリの一時ファイルへ行い、`os.replace()` で差し替えます。
   そのため書き込みが中断されても設定ファイルは破損しません。
   直前の内容は `<config>.bak` として保存されます (`--no-backup` で無効化可)。

削除したエントリは行を消すのではなく `##dhcp-host=...` として残すため、
後から手作業で復元できます。

## 対応範囲と制限

本ツールは意図的に対象を絞っており、`dhcp-host=` ディレクティブ**のみ**を解析・編集します。

非対応: `dhcp-range`、`dhcp-option`、DNS レコード (`address=`、`cname=`)、
IPv6 予約、クエリログ、統計、複数ユーザーのアクセス制御。

解釈できない行はそのまま保持されるため、それらは手作業で併用管理できます。

## 代替ツール

| プロジェクト | 向いている用途 |
|---|---|
| [Pi-hole](https://pi-hole.net/) | 広告ブロック込みの DNS/DHCP 統合アプライアンス。ただし v6 以降は `dnsmasq.conf` を自ら生成するため、手書きの設定ファイルは読みません |
| [OpenWrt LuCI](https://openwrt.org/docs/guide-user/luci/luci.essentials) / pfSense / OPNsense | すでにルーター OS を運用している場合。標準機能として内蔵されています |
| [dnsmasq-manager](https://github.com/gringolito/dnsmasq-manager) | UI ではなく REST API で静的リースを自動化したい場合。JWT 認証とディストリビューション向けパッケージあり |
| [dnsmasq-leases-ui](https://github.com/fschlag/dnsmasq-leases-ui) | リース一覧の閲覧のみをコンテナで行いたい場合 |
| [nexus-dnsmasq-mgr](https://github.com/brainchillz/nexus-dnsmasq-mgr) | DNS オーバーライド、PXE ブート、`dnsmasq --test` による検証など、より広範な機能が必要な場合 |

## 開発

```shell
git clone https://github.com/akivajp/dnsmasq-webconf.git
cd dnsmasq-webconf
uv venv && uv pip install -e '.[dev]'
uv run pytest
```

同梱している jQuery / Bootstrap とチェックサムのマニフェストを更新する場合:

```shell
scripts/update-vendor.sh
```

コントリビューションを歓迎します。
「ユーザーが変更していない行は書き換えない」という保証を壊さないようにし、
その点を検証するテストを添えてください。

## ライセンス

MIT — [LICENSE](LICENSE) を参照してください。

同梱しているサードパーティアセット (jQuery、Bootstrap) は MIT ライセンスです。
バージョン・取得元・チェックサムは
[`dnsmasq_webconf/static/vendor/MANIFEST.txt`](dnsmasq_webconf/static/vendor/MANIFEST.txt)
に記録しています。
