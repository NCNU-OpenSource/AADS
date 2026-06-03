/* ============================================================
   AADS Gate Console — shared atoms + helpers (exported to window)
   ============================================================ */
const { useState, useEffect, useRef, useMemo, useCallback } = React;

/* ---- status → visual system (the fixed contract from §1) ---- */
const STATUS_META = {
  pending_approval:               { label: "pending approval", tone: "amber", bucket: "awaiting" },
  approved:                       { label: "approved",         tone: "blue",  bucket: "approved" },
  queued:                         { label: "queued",           tone: "blue",  bucket: "inflight" },
  executing:                      { label: "executing",        tone: "blue",  bucket: "inflight" },
  final_verifying:                { label: "final verifying",  tone: "blue",  bucket: "inflight" },
  final_verified:                 { label: "final verified",   tone: "green", bucket: "history" },
  kb_imported:                    { label: "kb imported",      tone: "green", bucket: "history" },
  kb_skipped:                     { label: "kb skipped",       tone: "green", bucket: "history" },
  kb_import_failed:               { label: "kb import failed", tone: "green", bucket: "history" },
  rejected:                       { label: "rejected",         tone: "grey",  bucket: "history" },
  failed_retryable:               { label: "failed retryable", tone: "amber", bucket: "attention" },
  blocked:                        { label: "blocked",          tone: "red",   bucket: "attention" },
  execution_failed:               { label: "execution failed", tone: "red",   bucket: "attention" },
  execution_failed_unknown_state: { label: "failed · unknown", tone: "red",   bucket: "attention" },
  needs_triage:                   { label: "needs triage",     tone: "grey",  bucket: "attention" },
};
function statusMeta(status) { return STATUS_META[status] || { label: status || "pending", tone: "grey", bucket: "attention" }; }

const RESOLVED = new Set(["final_verified", "kb_imported", "kb_skipped", "kb_import_failed"]);
const INFLIGHT = new Set(["queued", "executing", "final_verifying"]);
const TERMINAL_FAIL = new Set(["blocked", "execution_failed", "execution_failed_unknown_state"]);

function bucketOf(report) { return statusMeta(report.plan_status).bucket; }
// V2 runner plans are schema 3.0 (kept the helper name to limit churn).
function isSchemaV2(report) { return (report.action_plan || {}).schema_version === "3.0"; }

function severityTone(sev) {
  return { critical: "red", high: "amber", medium: "blue", low: "green" }[sev] || "grey";
}

/* ---- formatting ---- */
function shortId(v) {
  const t = String(v || "");
  if (t.length <= 18) return t;
  return `${t.slice(0, 10)}…${t.slice(-4)}`;
}
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
function fmtClock(ms) {
  if (ms == null) return "—";
  const s = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}
function fmtPct(v) {
  const n = Number(v);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : "—";
}

/* ---- tiny atoms ---- */
function Led({ tone = "grey", pulse, glow, style }) {
  const cls = ["led", tone, pulse ? "pulse" : "", glow ? "glow" : ""].filter(Boolean).join(" ");
  return <span className={cls} style={style} />;
}
function Pill({ tone, mono, sev, lg, plain, led, pulse, className = "", children }) {
  const cls = ["pill", tone || "", mono ? "mono" : "", sev ? "sev" : "", lg ? "lg" : "", plain ? "plain" : "", className].filter(Boolean).join(" ");
  return <span className={cls}>{led && <Led tone={tone} pulse={pulse} />}{children}</span>;
}
function Tag({ k, children }) {
  return <span className="tag">{k && <span className="k">{k}</span>}{children}</span>;
}
function Btn({ variant, size, disabled, onClick, icon, className = "", children, title }) {
  const cls = ["btn", variant || "", size || "", disabled ? "dim" : "", className].filter(Boolean).join(" ");
  return (
    <button className={cls} disabled={disabled} onClick={disabled ? undefined : onClick} title={title}>
      {icon && <i className={`bi bi-${icon}`} />}{children}
    </button>
  );
}
function StatusPill({ status, lg }) {
  const m = statusMeta(status);
  const live = INFLIGHT.has(status);
  return <Pill tone={m.tone} led pulse={live} lg={lg}>{m.label}</Pill>;
}
function SevPill({ severity }) {
  return <Pill tone={severityTone(severity)} sev>{(severity || "—").toUpperCase()}</Pill>;
}
function Icon({ name, className = "" }) {
  const inner = ICONS[name] || ICONS._dot;
  return (
    <svg className={`ic ${className}`} viewBox="0 0 24 24" width="1em" height="1em" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
      style={{ verticalAlign: "-0.14em", flex: "0 0 auto" }} dangerouslySetInnerHTML={{ __html: inner }} />
  );
}
const ICONS = {
  _dot: '<circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/>',
  search: '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/>',
  "x-lg": '<line x1="5" y1="5" x2="19" y2="19"/><line x1="19" y1="5" x2="5" y2="19"/>',
  "x-circle": '<circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>',
  "check-lg": '<polyline points="20 6 9 17 4 12"/>',
  "check2-circle": '<circle cx="12" cy="12" r="9"/><polyline points="16.5 9 10.5 15.5 7.5 12.5"/>',
  "check-circle": '<circle cx="12" cy="12" r="9"/><polyline points="16.5 9 10.5 15.5 7.5 12.5"/>',
  "chevron-left": '<polyline points="15 5 8 12 15 19"/>',
  "chevron-right": '<polyline points="9 5 16 12 9 19"/>',
  clock: '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 16 14"/>',
  "clock-history": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 16 14"/>',
  stopwatch: '<circle cx="12" cy="13.5" r="7.5"/><line x1="12" y1="13.5" x2="12" y2="9"/><line x1="9.5" y1="2.5" x2="14.5" y2="2.5"/><line x1="12" y1="2.5" x2="12" y2="6"/>',
  "lock-fill": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  "unlock-fill": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
  unlock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2"/>',
  "shield-lock": '<path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><rect x="9.5" y="11.5" width="5" height="4" rx="1"/><path d="M10.5 11.5v-1a1.5 1.5 0 0 1 3 0v1"/>',
  "shield-check": '<path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><polyline points="9 12 11 14 15 9.5"/>',
  key: '<circle cx="8" cy="14" r="4"/><line x1="10.8" y1="11.2" x2="20" y2="2"/><line x1="17" y1="5" x2="20" y2="8"/><line x1="14" y1="8" x2="16.5" y2="10.5"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  "eye-slash": '<path d="M4 5l16 14"/><path d="M9.5 5.4A9.6 9.6 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3 3.6M6 7.8A17 17 0 0 0 2 12s4 7 10 7a9.6 9.6 0 0 0 3-.5"/>',
  "arrow-clockwise": '<path d="M20 11a8 8 0 1 0-1.6 5"/><polyline points="20 4 20 11 13.5 11"/>',
  "arrow-repeat": '<path d="M20 11a8 8 0 1 0-1.6 5"/><polyline points="20 4 20 11 13.5 11"/>',
  "hdd-network": '<rect x="3" y="3.5" width="18" height="7" rx="2"/><circle cx="7" cy="7" r="1" fill="currentColor" stroke="none"/><line x1="12" y1="10.5" x2="12" y2="14.5"/><rect x="8" y="14.5" width="8" height="5.5" rx="1.5"/>',
  sun: '<circle cx="12" cy="12" r="4.3"/><line x1="12" y1="2.5" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="21.5"/><line x1="2.5" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="21.5" y2="12"/><line x1="5.2" y1="5.2" x2="6.9" y2="6.9"/><line x1="17.1" y1="17.1" x2="18.8" y2="18.8"/><line x1="5.2" y1="18.8" x2="6.9" y2="17.1"/><line x1="17.1" y1="6.9" x2="18.8" y2="5.2"/>',
  "moon-stars": '<path d="M20 14a8 8 0 1 1-10-10 7 7 0 0 0 10 10z"/><path d="M18 3l.6 1.6L20 5l-1.4.4L18 7l-.6-1.6L16 5l1.4-.4z" fill="currentColor" stroke="none"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><polyline points="7 9 10 12 7 15"/><line x1="12.5" y1="15" x2="16" y2="15"/>',
  "play-fill": '<polygon points="7 5 19 12 7 19" fill="currentColor" stroke="none"/>',
  "hourglass-split": '<path d="M7 4h10M7 20h10M8 4c0 4 8 4 8 8s-8 4-8 8M16 4c0 4-8 4-8 8s8 4 8 8"/>',
  "diagram-3": '<rect x="9" y="3" width="6" height="4" rx="1"/><rect x="3" y="16" width="6" height="4" rx="1"/><rect x="15" y="16" width="6" height="4" rx="1"/><path d="M12 7v4M6 16v-2h12v2"/>',
  "exclamation-diamond": '<path d="M12 2.5l9.5 9.5L12 21.5 2.5 12z"/><line x1="12" y1="8" x2="12" y2="13"/><circle cx="12" cy="16.3" r="0.7" fill="currentColor" stroke="none"/>',
  "exclamation-triangle": '<path d="M12 3.5l9 16H3z"/><line x1="12" y1="9.5" x2="12" y2="14"/><circle cx="12" cy="16.8" r="0.7" fill="currentColor" stroke="none"/>',
  exclamation: '<line x1="12" y1="6" x2="12" y2="13"/><circle cx="12" cy="16.8" r="0.8" fill="currentColor" stroke="none"/>',
  "list-check": '<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/><polyline points="3 6 4.2 7.2 6 5"/><polyline points="3 12 4.2 13.2 6 11"/><polyline points="3 18 4.2 19.2 6 17"/>',
  archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><line x1="10" y1="12" x2="14" y2="12"/>',
  inbox: '<path d="M3 13l3-8h12l3 8v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/><path d="M3 13h5l1.5 2.5h5L16 13h5"/>',
  clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><rect x="9" y="2.5" width="6" height="3.5" rx="1"/>',
  "box-arrow-up-right": '<path d="M14 4h6v6"/><line x1="20" y1="4" x2="11" y2="13"/><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6"/>',
  magic: '<line x1="5" y1="19" x2="15" y2="9"/><path d="M17 3l.7 2.3L20 6l-2.3.7L17 9l-.7-2.3L14 6z" fill="currentColor" stroke="none"/><circle cx="6.5" cy="7" r="0.7" fill="currentColor" stroke="none"/><circle cx="20" cy="14" r="0.7" fill="currentColor" stroke="none"/>',
  hash: '<line x1="9" y1="4" x2="7" y2="20"/><line x1="17" y1="4" x2="15" y2="20"/><line x1="4" y1="9" x2="20" y2="9"/><line x1="4" y1="15" x2="20" y2="15"/>',
  command: '<path d="M9 6.5A2.5 2.5 0 1 0 6.5 9H9V6.5zM15 6.5A2.5 2.5 0 1 1 17.5 9H15V6.5zM9 17.5A2.5 2.5 0 1 1 6.5 15H9v2.5zM15 17.5a2.5 2.5 0 1 0 2.5-2.5H15v2.5zM9 9h6v6H9z"/>',
};

/* ---- JSON renderer (colourised, monospace) ---- */
function jsonHtml(data) {
  const json = JSON.stringify(data, null, 2);
  if (json == null) return "";
  return json
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:')
    .replace(/: ?"([^"]*)"/g, ': <span class="v">"$1"</span>')
    .replace(/: ?(true|false|null|-?\d+\.?\d*)/g, ': <span class="v">$1</span>');
}
function JsonBlock({ data, className = "" }) {
  return <div className={`codeblock ${className}`} dangerouslySetInnerHTML={{ __html: jsonHtml(data) }} />;
}

/* ---- expand-the-window-safe export ---- */
Object.assign(window, {
  STATUS_META, statusMeta, RESOLVED, INFLIGHT, TERMINAL_FAIL, bucketOf, isSchemaV2,
  severityTone, shortId, fmtTime, fmtClock, fmtPct,
  Led, Pill, Tag, Btn, StatusPill, SevPill, Icon, jsonHtml, JsonBlock,
});
