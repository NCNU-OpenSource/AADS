#!/usr/bin/env bash
# ============================================================================
# AADS Demo — inject service faults directly on the target node
#
# Run this ON the target machine (not the controller) as root:
#   sudo bash scripts/lab/demo-break.sh
#   sudo bash scripts/lab/demo-break.sh nginx-bad-config
#
# Each scenario is self-contained: it first restores the service to a known-good
# baseline, then injects the fault so the demo always starts from a clean state.
#
# Supported scenarios (first arg selects; omit to get an interactive menu):
#   nginx-bad-config      Corrupt nginx.conf → config test fails
#   nginx-stopped         Kill nginx → service inactive
#   mysql-bad-config      Inject invalid directive → mysql fails to start
#   mysql-stopped         Kill MySQL/MariaDB → service inactive
#   redis-bad-config      Inject invalid directive → redis fails to start
#   redis-stopped         Kill Redis → service inactive
#   pg-bad-config         Inject invalid directive → postgresql fails to start
#   pg-stopped            Kill PostgreSQL → service inactive
#   all-stopped           Stop all detected services at once
#   restore-all           Restore baselines and start all services
# ============================================================================
set -euo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Run as root: sudo bash $0 $*" >&2; exit 1; }

MARKER="demo-$(date +%s)"

log()     { printf '\n\033[1;35m[DEMO]\033[0m %s\n' "$*"; }
ok()      { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn()    { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
section() { printf '\n\033[1;36m══ %s ══\033[0m\n' "$*"; }
die()     { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

svc_active() { systemctl is-active --quiet "$1" 2>/dev/null; }
svc_exists() { systemctl list-units --all --no-legend "$1.service" 2>/dev/null | grep -q .; }

# ── nginx helpers ────────────────────────────────────────────────────────────
nginx_restore() {
  local snap=/var/lib/aads-agent/snapshots/nginx
  if [[ -f "$snap/nginx.conf" ]]; then
    cp -a "$snap/nginx.conf" /etc/nginx/nginx.conf
    [[ -d "$snap/sites-enabled" ]] && cp -a "$snap/sites-enabled/." /etc/nginx/sites-enabled/
    ok "nginx config restored from AADS snapshot"
  else
    warn "No AADS snapshot found; leaving nginx.conf as-is"
  fi
  systemctl start nginx 2>/dev/null || true
  svc_active nginx && ok "nginx active" || warn "nginx not active after restore"
}

inject_nginx_bad_config() {
  log "nginx-bad-config: injecting broken nginx.conf"
  nginx_restore
  cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak-"${MARKER}"
  # Append an unclosed block that makes nginx -t fail
  printf '\n# AADS demo fault (%s)\nevents_broken {\n' "$MARKER" >> /etc/nginx/nginx.conf
  systemctl reload nginx 2>/dev/null || true
  # Seed the error log so layer1 picks it up immediately
  printf '%s [emerg] nginx: configuration file /etc/nginx/nginx.conf test failed\n' \
    "$(date -Is)" >> /var/log/nginx/error.log
  nginx -t 2>&1 | grep -v "^$" || true
  warn "nginx config is now broken (nginx -t will fail)"
}

inject_nginx_stopped() {
  log "nginx-stopped: stopping nginx"
  nginx_restore
  systemctl stop nginx
  logger -t nginx "nginx failed to start: service stopped by AADS demo ${MARKER}"
  printf '%s [error] nginx failed to start: service stopped by AADS demo %s\n' \
    "$(date -Is)" "$MARKER" >> /var/log/nginx/error.log
  warn "nginx is stopped"
}

# ── MySQL / MariaDB helpers ──────────────────────────────────────────────────
mysql_service() {
  for s in mysql mariadb mysqld; do
    svc_exists "$s" && { echo "$s"; return; }
  done
  echo ""
}

mysql_conf_file() {
  for f in /etc/mysql/mysql.conf.d/mysqld.cnf \
            /etc/mysql/mariadb.conf.d/50-server.cnf \
            /etc/mysql/my.cnf /etc/my.cnf; do
    [[ -f "$f" ]] && { echo "$f"; return; }
  done
  echo ""
}

mysql_restore() {
  local conf snap_conf
  conf="$(mysql_conf_file)"
  snap_conf="/var/lib/aads-agent/snapshots/mysql/$(basename "${conf:-my.cnf}")"
  if [[ -n "$conf" && -f "$snap_conf" ]]; then
    cp -a "$snap_conf" "$conf"
    ok "MySQL config restored from AADS snapshot"
  elif [[ -n "$conf" ]]; then
    # Remove lines we injected (chaos_section / chaos_option markers)
    sed -i "/\[chaos_section/,/^chaos_option/d" "$conf" 2>/dev/null || true
    ok "Removed injected chaos lines from $conf"
  fi
  local svc; svc="$(mysql_service)"
  [[ -n "$svc" ]] && { systemctl start "$svc" 2>/dev/null || true; svc_active "$svc" && ok "$svc active" || warn "$svc not active after restore"; }
}

inject_mysql_bad_config() {
  local svc conf
  svc="$(mysql_service)"; conf="$(mysql_conf_file)"
  [[ -n "$svc" ]] || { warn "MySQL/MariaDB not found — skipping"; return; }
  log "mysql-bad-config: injecting invalid directive into ${conf:-unknown}"
  mysql_restore
  [[ -n "$conf" ]] || die "Cannot find MySQL config file."
  cp "$conf" "${conf}.bak-${MARKER}"
  printf '\n[chaos_section_%s]\nchaos_option = ???\n' "$MARKER" >> "$conf"
  systemctl stop "$svc" 2>/dev/null || true
  systemctl start "$svc" 2>/dev/null || true   # expected to fail
  logger -t "$svc" "mysql failed to start: bad config injected by AADS demo ${MARKER}"
  warn "MySQL config is now broken (service will fail to start)"
}

inject_mysql_stopped() {
  local svc; svc="$(mysql_service)"
  [[ -n "$svc" ]] || { warn "MySQL/MariaDB not found — skipping"; return; }
  log "mysql-stopped: stopping $svc"
  mysql_restore
  systemctl stop "$svc"
  logger -t "$svc" "mysql failed to start: service stopped by AADS demo ${MARKER}"
  warn "$svc is stopped"
}

# ── Redis helpers ────────────────────────────────────────────────────────────
redis_service() {
  for s in redis redis-server redis@6379; do
    svc_exists "$s" && { echo "$s"; return; }
  done
  echo ""
}

redis_conf_file() {
  for f in /etc/redis/redis.conf /etc/redis.conf; do
    [[ -f "$f" ]] && { echo "$f"; return; }
  done
  echo ""
}

redis_restore() {
  local conf snap_conf
  conf="$(redis_conf_file)"
  snap_conf="/var/lib/aads-agent/snapshots/redis/$(basename "${conf:-redis.conf}")"
  if [[ -n "$conf" && -f "$snap_conf" ]]; then
    cp -a "$snap_conf" "$conf"
    ok "Redis config restored from AADS snapshot"
  elif [[ -n "$conf" ]]; then
    sed -i "/^invalid_chaos_directive/d" "$conf" 2>/dev/null || true
    ok "Removed injected chaos lines from $conf"
  fi
  local svc; svc="$(redis_service)"
  [[ -n "$svc" ]] && { systemctl start "$svc" 2>/dev/null || true; svc_active "$svc" && ok "$svc active" || warn "$svc not active after restore"; }
}

inject_redis_bad_config() {
  local svc conf
  svc="$(redis_service)"; conf="$(redis_conf_file)"
  [[ -n "$svc" ]] || { warn "Redis not found — skipping"; return; }
  log "redis-bad-config: injecting invalid directive into ${conf:-unknown}"
  redis_restore
  [[ -n "$conf" ]] || die "Cannot find Redis config file."
  cp "$conf" "${conf}.bak-${MARKER}"
  printf '\ninvalid_chaos_directive_%s ???\n' "$MARKER" >> "$conf"
  systemctl stop "$svc" 2>/dev/null || true
  systemctl start "$svc" 2>/dev/null || true   # expected to fail
  logger -t "$svc" "redis failed to start: bad config injected by AADS demo ${MARKER}"
  warn "Redis config is now broken (service will fail to start)"
}

inject_redis_stopped() {
  local svc; svc="$(redis_service)"
  [[ -n "$svc" ]] || { warn "Redis not found — skipping"; return; }
  log "redis-stopped: stopping $svc"
  redis_restore
  systemctl stop "$svc"
  logger -t "$svc" "redis failed to start: service stopped by AADS demo ${MARKER}"
  warn "$svc is stopped"
}

# ── PostgreSQL helpers ───────────────────────────────────────────────────────
pg_service() {
  for s in postgresql postgresql@14-main postgresql@16-main; do
    svc_exists "$s" && { echo "$s"; return; }
  done
  echo ""
}

pg_conf_dir() {
  for d in /etc/postgresql /etc/postgresql/14/main /etc/postgresql/16/main; do
    [[ -d "$d" ]] && find "$d" -name "postgresql.conf" -print -quit 2>/dev/null | head -1
  done | head -1
}

pg_restore() {
  local conf snap
  conf="$(pg_conf_dir)"
  snap="/var/lib/aads-agent/snapshots/postgresql"
  if [[ -n "$conf" && -d "$snap" ]]; then
    local snap_conf="$snap/$(basename "$conf")"
    [[ -f "$snap_conf" ]] && { cp -a "$snap_conf" "$conf"; ok "PostgreSQL config restored from AADS snapshot"; }
  elif [[ -n "$conf" ]]; then
    sed -i "/^invalid_directive_chaos/d" "$conf" 2>/dev/null || true
    ok "Removed injected chaos lines from $conf"
  fi
  local svc; svc="$(pg_service)"
  [[ -n "$svc" ]] && { systemctl start "$svc" 2>/dev/null || true; svc_active "$svc" && ok "$svc active" || warn "$svc not active after restore"; }
}

inject_pg_bad_config() {
  local svc conf
  svc="$(pg_service)"; conf="$(pg_conf_dir)"
  [[ -n "$svc" ]] || { warn "PostgreSQL not found — skipping"; return; }
  log "pg-bad-config: injecting invalid directive into ${conf:-unknown}"
  pg_restore
  [[ -n "$conf" ]] || die "Cannot find postgresql.conf."
  cp "$conf" "${conf}.bak-${MARKER}"
  printf '\ninvalid_directive_chaos_%s = ???\n' "$MARKER" >> "$conf"
  systemctl stop "$svc" 2>/dev/null || true
  systemctl start "$svc" 2>/dev/null || true   # expected to fail
  logger -t postgresql "postgresql failed to start: bad config injected by AADS demo ${MARKER}"
  warn "PostgreSQL config is now broken (service will fail to start)"
}

inject_pg_stopped() {
  local svc; svc="$(pg_service)"
  [[ -n "$svc" ]] || { warn "PostgreSQL not found — skipping"; return; }
  log "pg-stopped: stopping $svc"
  pg_restore
  systemctl stop "$svc"
  logger -t postgresql "postgresql failed to start: service stopped by AADS demo ${MARKER}"
  warn "$svc is stopped"
}

# ── composite ────────────────────────────────────────────────────────────────
restore_all() {
  log "restore-all: bringing everything back to a healthy baseline"
  nginx_restore
  mysql_restore
  redis_restore
  pg_restore
  section "All services restored"
  systemctl is-active nginx 2>/dev/null   && ok "nginx   : active" || warn "nginx   : inactive (not installed?)"
  local msvc; msvc="$(mysql_service)"
  [[ -n "$msvc" ]] && { svc_active "$msvc"  && ok "mysql   : active" || warn "mysql   : inactive"; } || warn "mysql   : not installed"
  local rsvc; rsvc="$(redis_service)"
  [[ -n "$rsvc" ]] && { svc_active "$rsvc"  && ok "redis   : active" || warn "redis   : inactive"; } || warn "redis   : not installed"
  local psvc; psvc="$(pg_service)"
  [[ -n "$psvc" ]] && { svc_active "$psvc"  && ok "postgres: active" || warn "postgres: inactive"; } || warn "postgres: not installed"
}

inject_all_stopped() {
  log "all-stopped: stopping every detected service"
  inject_nginx_stopped
  local msvc; msvc="$(mysql_service)"; [[ -n "$msvc" ]] && inject_mysql_stopped
  local rsvc; rsvc="$(redis_service)"; [[ -n "$rsvc" ]] && inject_redis_stopped
  local psvc; psvc="$(pg_service)";   [[ -n "$psvc" ]] && inject_pg_stopped
  section "All services stopped — AADS should detect and auto-repair"
}

# ── interactive menu ─────────────────────────────────────────────────────────
show_menu() {
  section "AADS Demo — Fault Injection"
  echo "  Nginx"
  echo "    1) nginx-bad-config   (corrupt config → config test fails)"
  echo "    2) nginx-stopped      (service killed)"
  echo "  MySQL / MariaDB"
  echo "    3) mysql-bad-config   (invalid directive → fail to start)"
  echo "    4) mysql-stopped      (service killed)"
  echo "  Redis"
  echo "    5) redis-bad-config   (invalid directive → fail to start)"
  echo "    6) redis-stopped      (service killed)"
  echo "  PostgreSQL"
  echo "    7) pg-bad-config      (invalid directive → fail to start)"
  echo "    8) pg-stopped         (service killed)"
  echo "  Combined"
  echo "    9) all-stopped        (stop all services at once)"
  echo "    0) restore-all        (restore baselines + start all)"
  echo
  read -r -p "Choose scenario [0-9]: " choice < /dev/tty
  case "$choice" in
    1) SCENARIO=nginx-bad-config ;;
    2) SCENARIO=nginx-stopped ;;
    3) SCENARIO=mysql-bad-config ;;
    4) SCENARIO=mysql-stopped ;;
    5) SCENARIO=redis-bad-config ;;
    6) SCENARIO=redis-stopped ;;
    7) SCENARIO=pg-bad-config ;;
    8) SCENARIO=pg-stopped ;;
    9) SCENARIO=all-stopped ;;
    0) SCENARIO=restore-all ;;
    *) die "Invalid choice: $choice" ;;
  esac
}

# ── dispatch ─────────────────────────────────────────────────────────────────
SCENARIO="${1:-}"
[[ -z "$SCENARIO" ]] && show_menu

case "$SCENARIO" in
  nginx-bad-config)  inject_nginx_bad_config ;;
  nginx-stopped)     inject_nginx_stopped ;;
  mysql-bad-config)  inject_mysql_bad_config ;;
  mysql-stopped)     inject_mysql_stopped ;;
  redis-bad-config)  inject_redis_bad_config ;;
  redis-stopped)     inject_redis_stopped ;;
  pg-bad-config)     inject_pg_bad_config ;;
  pg-stopped)        inject_pg_stopped ;;
  all-stopped)       inject_all_stopped ;;
  restore-all)       restore_all ;;
  *) die "Unknown scenario '$SCENARIO'. Run without args to see the menu." ;;
esac

echo
section "AADS will detect the fault in the next 30-60 seconds"
echo "  Watch layer2:   docker compose logs -f layer2-analyzer   (on the server)"
echo "  Watch dashboard: http://<server>:5000"
