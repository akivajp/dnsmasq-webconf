#!/usr/bin/env bash
# 同梱するフロントエンドアセットを更新し、MANIFEST.txt を再生成する。
#
# 使い方: scripts/update-vendor.sh
set -euo pipefail

JQUERY_VERSION="3.7.1"
BOOTSTRAP_VERSION="4.6.2"

VENDOR_DIR="$(cd "$(dirname "$0")/.." && pwd)/dnsmasq_webconf/static/vendor"
mkdir -p "$VENDOR_DIR"
cd "$VENDOR_DIR"

JQUERY_URL="https://code.jquery.com/jquery-${JQUERY_VERSION}.min.js"
BOOTSTRAP_CSS_URL="https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP_VERSION}/dist/css/bootstrap.min.css"
BOOTSTRAP_JS_URL="https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP_VERSION}/dist/js/bootstrap.bundle.min.js"

echo "アセットを取得しています..."
curl -sSfL --max-time 60 -o jquery.min.js "$JQUERY_URL"
curl -sSfL --max-time 60 -o bootstrap.min.css "$BOOTSTRAP_CSS_URL"
curl -sSfL --max-time 60 -o bootstrap.bundle.min.js "$BOOTSTRAP_JS_URL"

# 取得元と sha384 (SRI と同形式) を記録し、後から真正性を検証できるようにする
{
  echo "# 同梱サードパーティアセット"
  echo "#"
  echo "# 各ファイルの取得元と sha384 (Subresource Integrity と同形式)。"
  echo "# 更新時は scripts/update-vendor.sh を実行し、本ファイルも更新すること。"
  echo
  for entry in \
    "jquery.min.js|$JQUERY_URL|MIT (OpenJS Foundation)" \
    "bootstrap.min.css|$BOOTSTRAP_CSS_URL|MIT (The Bootstrap Authors)" \
    "bootstrap.bundle.min.js|$BOOTSTRAP_JS_URL|MIT (The Bootstrap Authors)"
  do
    IFS='|' read -r file url license <<< "$entry"
    hash="$(openssl dgst -sha384 -binary "$file" | openssl base64 -A)"
    printf '%s\n  url: %s\n  license: %s\n  sha384-%s\n\n' "$file" "$url" "$license" "$hash"
  done
} > MANIFEST.txt

echo "完了しました。MANIFEST.txt の差分を確認してください。"
