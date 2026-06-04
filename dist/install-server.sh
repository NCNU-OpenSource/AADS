#!/usr/bin/env bash
# ============================================================================
# AADS Server — one-shot bootstrap from GitHub (curl | bash)
#
#   curl -fsSL https://raw.githubusercontent.com/bs10081/AADS/main/dist/install-server.sh | bash
#
# Downloads the prebuilt server bundle from GitHub Releases, extracts it to
# AADS_INSTALL_DIR, then runs dist/bootstrap-server.sh interactively.
#
# Pre-answer any prompt with an environment variable (fully non-interactive):
#   AADS_LITELLM_UPSTREAM_API_KEY  upstream LLM API key   [required]
#   LITELLM_MODEL                  default: gpt-5.5
#   PI_AGENT_TOKEN                 default: auto-generated
#   AADS_ADMIN_API_KEY             default: auto-generated
#   TIMESCALEDB_PASSWORD           default: auto-generated
#   LITELLM_MASTER_KEY             default: auto-generated
#   AADS_SERVER_IP                 default: auto-detected
#   AADS_INSTALL_DIR               where to extract the bundle
#                                  (default: $HOME/aads  or  /opt/aads if root)
#   AADS_RELEASE_BASE_URL          GitHub Releases base URL
#                                  (default: https://github.com/bs10081/AADS/releases/latest/download)
# ============================================================================
set -euo pipefail

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

RELEASE_BASE="${AADS_RELEASE_BASE_URL:-https://github.com/bs10081/AADS/releases/latest/download}"

# Default install dir: ~/aads for regular users, /opt/aads for root
if [[ -z "${AADS_INSTALL_DIR:-}" ]]; then
  if [[ "${EUID}" -eq 0 ]]; then
    AADS_INSTALL_DIR=/opt/aads
  else
    AADS_INSTALL_DIR="${HOME}/aads"
  fi
fi

command -v curl >/dev/null 2>&1 || die "curl is required — install it first (apt install curl)."
command -v tar  >/dev/null 2>&1 || die "tar is required."

log "AADS Server installer"
echo "  Release base : $RELEASE_BASE"
echo "  Install dir  : $AADS_INSTALL_DIR"

# ── Download bundle ──────────────────────────────────────────────────────────
BUNDLE_URL="$RELEASE_BASE/aads-server.tgz"
TMP_BUNDLE="$(mktemp /tmp/aads-server.XXXXXX.tgz)"
cleanup() { rm -f "$TMP_BUNDLE"; }
trap cleanup EXIT

log "Downloading server bundle"
curl -fsSL "$BUNDLE_URL" -o "$TMP_BUNDLE" \
  || die "Failed to download $BUNDLE_URL — check the release exists and the URL is correct."
ok "Downloaded $(du -h "$TMP_BUNDLE" | cut -f1)"

# ── Extract ──────────────────────────────────────────────────────────────────
log "Extracting to $AADS_INSTALL_DIR"
mkdir -p "$AADS_INSTALL_DIR"
tar -xzf "$TMP_BUNDLE" -C "$AADS_INSTALL_DIR"
chmod +x \
  "$AADS_INSTALL_DIR/dist/bootstrap-server.sh" \
  "$AADS_INSTALL_DIR/dist/build-agent-payload.sh" \
  "$AADS_INSTALL_DIR/dist/build-server-bundle.sh" \
  "$AADS_INSTALL_DIR/dist/install-agent.sh" \
  "$AADS_INSTALL_DIR/dist/install-server.sh" \
  2>/dev/null || true
ok "Extracted"

# ── Run bootstrap ────────────────────────────────────────────────────────────
log "Running server bootstrap"
cd "$AADS_INSTALL_DIR"
exec bash dist/bootstrap-server.sh
