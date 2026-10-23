#!/usr/bin/env bash
# Update the bundled frontend assets and regenerate MANIFEST.txt.
#
# Usage: scripts/update-vendor.sh
set -euo pipefail

JQUERY_VERSION="3.7.1"
BOOTSTRAP_VERSION="4.6.2"

VENDOR_DIR="$(cd "$(dirname "$0")/.." && pwd)/dnsmasq_webconf/static/vendor"
mkdir -p "$VENDOR_DIR"
cd "$VENDOR_DIR"

JQUERY_URL="https://code.jquery.com/jquery-${JQUERY_VERSION}.min.js"
BOOTSTRAP_CSS_URL="https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP_VERSION}/dist/css/bootstrap.min.css"
BOOTSTRAP_JS_URL="https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP_VERSION}/dist/js/bootstrap.bundle.min.js"

echo "Fetching assets..."
curl -sSfL --max-time 60 -o jquery.min.js "$JQUERY_URL"
curl -sSfL --max-time 60 -o bootstrap.min.css "$BOOTSTRAP_CSS_URL"
curl -sSfL --max-time 60 -o bootstrap.bundle.min.js "$BOOTSTRAP_JS_URL"

# Record the source and sha384 (same format as SRI) so authenticity can be verified later
{
  echo "# Bundled third-party assets"
  echo "#"
  echo "# Source URL and sha384 of each file (same format as Subresource Integrity)."
  echo "# To update, run scripts/update-vendor.sh and commit this file as well."
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

echo "Done. Review the diff of MANIFEST.txt."
