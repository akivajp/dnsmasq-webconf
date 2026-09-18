# dnsmasq-webconf の実行イメージ
FROM python:3.12-slim

# 依存を先に解決してレイヤキャッシュを効かせる
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY dnsmasq_webconf ./dnsmasq_webconf
RUN pip install --no-cache-dir ".[server]"

# 設定ファイル書き換えのため、ホスト側の uid に合わせて起動することを想定する
# (例: docker run --user "$(id -u):$(id -g)")
EXPOSE 8080

# コンテナ内では外部から到達できる必要があるため 0.0.0.0 を既定とする。
# 認証は必須 (DNSMASQ_WEBCONF_AUTH 環境変数で渡す)。
ENTRYPOINT ["dnsmasq-webconf"]
CMD ["8080", "--host", "0.0.0.0"]
