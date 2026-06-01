/* ============================================================
   AADS Gate Console — App orchestrator (LIVE-WIRED)

   Talks to the real Flask backend via window.AADS_API:
   loads /api/diagnosis + /api/agents + /api/stats, enriches the
   selected/in-flight items with /api/plans/<id>/execution, runs real
   approve/reject/execute, and polls execution traces to terminal state.
   ============================================================ */
const { useState, useEffect, useRef, useMemo, useCallback } = React;
const API = window.AADS_API;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  theme: "dark",
  accent: "#1ec8ad",
  density: "regular",
}/*EDITMODE-END*/;

const ACCENTS = {
  "#1ec8ad": "Signal teal",
  "#6c8cff": "Console indigo",
  "#c9cfd8": "Graphite",
  "#e0865a": "Amber clay",
};
const POLL_MS = 2500;       // execution-trace poll cadence while in-flight
const BG_REFRESH_MS = 15000; // background list refresh

function loadTweaks() {
  try { return { ...TWEAK_DEFAULTS, ...(JSON.parse(localStorage.getItem("aadsTweaks") || "{}")) }; }
  catch (e) { return TWEAK_DEFAULTS; }
}

/* ---- helpers ---- */
function hexToRgba(hex, a) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}
function luminance(hex) {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}
function normalizeKey(value) {
  let key = (value || "").trim();
  const m = key.match(/AADS_ADMIN_API_KEY\s*=\s*([^\s#]+)/);
  if (m) key = m[1];
  if (key.startsWith("export ")) key = key.slice(7).trim();
  if (key.startsWith("AADS_ADMIN_API_KEY=")) key = key.slice(19).trim();
  return key.replace(/^['"]|['"]$/g, "");
}

let toastSeq = 0;

function App() {
  const [t, setTweak] = useTweaks(loadTweaks());

  // ---- apply theme / density / accent to <html> ----
  useEffect(() => {
    const el = document.documentElement;
    el.setAttribute("data-theme", t.theme);
    el.setAttribute("data-density", t.density);
    const a = t.accent || "#1ec8ad";
    el.style.setProperty("--accent", a);
    el.style.setProperty("--accent-soft", hexToRgba(a, 0.14));
    el.style.setProperty("--accent-line", hexToRgba(a, 0.42));
    el.style.setProperty("--accent-ink", luminance(a) > 0.6 ? "#07120f" : "#04130f");
    try { localStorage.setItem("aadsTweaks", JSON.stringify(t)); } catch (e) {}
  }, [t.theme, t.density, t.accent]);

  // ---- auth ----
  const [locked, setLocked] = useState(() => localStorage.getItem("aadsLocked") !== "false");
  const [adminKey, setAdminKey] = useState(() => localStorage.getItem("aadsAdminKey") || "");
  const [authOpen, setAuthOpen] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [authError, setAuthError] = useState("");
  const [authState, setAuthState] = useState({ valid: false, fp: "" });
  useEffect(() => { localStorage.setItem("aadsAdminKey", adminKey); }, [adminKey]);
  useEffect(() => { localStorage.setItem("aadsLocked", locked ? "true" : "false"); }, [locked]);

  const diag = { len: normalizeKey(adminKey).length, fp: authState.fp, valid: authState.valid, error: authError };
  const fp = authState.fp || "";

  // ---- ui state ----
  const [tab, setTab] = useState("queue");
  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState("");
  const [hours, setHours] = useState(168); // 7d default — History is always returned regardless
  const [reason, setReason] = useState("");
  const [fleetOpen, setFleetOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [toasts, setToasts] = useState([]);

  // ---- live data ----
  const [reports, setReports] = useState([]);
  const [agents, setAgents] = useState([]);
  const [stats, setStats] = useState({ anomalies: { total: 0, by_container: [] }, diagnoses: { total: 0, by_severity: [] } });
  const [enrich, setEnrich] = useState({});       // id -> {trace, approval, retry, audit, rolled_back, _run}
  const [optimistic, setOptimistic] = useState({}); // id -> {_status, approval} (cleared once server agrees)
  const [loadError, setLoadError] = useState("");

  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(i); }, []);
  useEffect(() => { setReason(""); }, [selectedId]);
  useEffect(() => { document.body.classList.toggle("has-selection", !!selectedId); }, [selectedId]);

  const toast = useCallback((tone, title, body) => {
    const id = ++toastSeq;
    setToasts((p) => [...p, { id, tone, title, body }]);
    setTimeout(() => setToasts((p) => p.filter((x) => x.id !== id)), 5200);
  }, []);

  /* ============================================================
     DATA LOADING
     ============================================================ */
  const reloadList = useCallback(async (opts = {}) => {
    try {
      const { reports: rs, agents: ag, stats: st } = await API.loadAll(hours);
      setReports(rs);
      setAgents(ag);
      setStats(st);
      setLoadError("");
      // drop optimistic overrides the server has caught up with
      setOptimistic((p) => {
        const next = {};
        for (const r of rs) {
          const o = p[r.diagnosis_id];
          if (o && o._status && o._status !== r.plan_status) next[r.diagnosis_id] = o;
        }
        return next;
      });
      // keep a valid selection
      setSelectedId((cur) => (cur && rs.some((r) => r.diagnosis_id === cur)) ? cur : (rs[0]?.diagnosis_id ?? null));
    } catch (e) {
      if (!opts.quiet) setLoadError(String(e.message || e));
    }
  }, [hours]);

  const enrichOne = useCallback(async (report) => {
    if (!report) return;
    try {
      const data = await API.enrichReport(report);
      setEnrich((p) => ({ ...p, [report.diagnosis_id]: data }));
    } catch (e) {}
  }, []);

  // initial + on window change
  useEffect(() => { reloadList(); }, [reloadList]);

  // background refresh
  useEffect(() => {
    const i = setInterval(() => reloadList({ quiet: true }), BG_REFRESH_MS);
    return () => clearInterval(i);
  }, [reloadList]);

  /* ============================================================
     DECORATION  (server status + optimistic + enrichment)
     ============================================================ */
  const decorated = useMemo(() => reports.map((r) => {
    const o = optimistic[r.diagnosis_id] || {};
    const e = enrich[r.diagnosis_id] || {};
    const _status = o._status || r.plan_status;
    return {
      ...r,
      _status,
      approval: o.approval || e.approval || null,
      retry: e.retry || null,
      audit: e.audit || null,
      rolled_back: e.rolled_back || false,
      trace: e.trace || null,
      _run: e._run || null,
    };
  }), [reports, optimistic, enrich]);

  // enrich the selected item whenever it changes identity
  // Only show a detail pane if the selected item actually belongs to the active tab.
  // This prevents stale detail content when the tab is empty, and auto-clears
  // when an item transitions between buckets (e.g. Queue → History after execution).
  const TAB_BUCKETS = {
    queue:     ["awaiting", "approved", "inflight"],
    attention: ["attention"],
    history:   ["history"],
  };
  const selected = useMemo(() => {
    const report = decorated.find((r) => r.diagnosis_id === selectedId);
    if (!report) return null;
    const bucket = statusMeta(report._status).bucket;
    if (!(TAB_BUCKETS[tab] || []).includes(bucket)) return null;
    return report;
  }, [decorated, selectedId, tab]);
  useEffect(() => {
    const r = reports.find((x) => x.diagnosis_id === selectedId);
    if (r) enrichOne(r);
  }, [selectedId, reports, enrichOne]);

  // poll execution while anything is in-flight (and keep the selected trace fresh)
  const anyInflight = decorated.some((r) => INFLIGHT.has(r._status));
  useEffect(() => {
    if (!anyInflight) return;
    const i = setInterval(() => {
      reloadList({ quiet: true });
      const sel = reports.find((x) => x.diagnosis_id === selectedId);
      if (sel && INFLIGHT.has((optimistic[selectedId]?._status) || sel.plan_status)) enrichOne(sel);
      // also refresh any in-flight item's trace so counts stay live
      reports.forEach((r) => { if (INFLIGHT.has(r.plan_status)) enrichOne(r); });
    }, POLL_MS);
    return () => clearInterval(i);
  }, [anyInflight, reports, selectedId, optimistic, reloadList, enrichOne]);

  /* ---- search filter ---- */
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return decorated;
    return decorated.filter((r) => {
      const plan = r.action_plan || {};
      const root = r.root_cause || {};
      const idx = [
        r.diagnosis_id, r.summary, r.severity, r._status, plan.schema_version, plan.target_node_id, plan.goal, plan.risk_level,
        (plan.environment_policy || {}).environment, root.affected_service,
        ...(root.recommended_capabilities || []),
        ...((plan.steps || []).flatMap((s) => [s.command_id, s.expected_outcome, s.on_failure, s.verification?.command_id])),
      ].filter(Boolean).join(" ").toLowerCase();
      return idx.includes(q);
    });
  }, [decorated, search]);

  /* ---- counts ---- */
  const counts = useMemo(() => {
    const bk = (r) => statusMeta(r._status).bucket;
    const queue = filtered.filter((r) => ["awaiting", "approved", "inflight"].includes(bk(r)));
    return {
      queue: queue.length,
      attention: filtered.filter((r) => bk(r) === "attention").length,
      history: filtered.filter((r) => bk(r) === "history").length,
      gateWaiting: decorated.filter((r) => ["awaiting", "approved"].includes(bk(r))).length,
      inflight: decorated.filter((r) => bk(r) === "inflight").length,
      highPriority: reports.filter((r) => ["critical", "high"].includes(r.severity)).length,
      agents: agents.length,
    };
  }, [filtered, decorated, reports, agents]);

  /* ---- approval-expiry watchdog (matches backend: execute 403s after 30 min) ---- */
  useEffect(() => {
    decorated.forEach((r) => {
      if (r._status === "approved" && r.approval && new Date(r.approval.approved_until).getTime() <= now) {
        setOptimistic((p) => ({ ...p, [r.diagnosis_id]: { _status: "pending_approval" } }));
        toast("info", "Approval expired", `${shortId(r.diagnosis_id)} returned to pending_approval`);
      }
    });
  }, [now]); // eslint-disable-line

  /* ============================================================
     GATE HANDLERS (real backend)
     ============================================================ */
  const guard = () => {
    if (locked) { toast("danger", "Locked", "Unlock the admin key to gate this plan."); setAuthOpen(true); return false; }
    return true;
  };
  const key = () => normalizeKey(adminKey);

  const onApprove = async (report) => {
    if (!guard()) return;
    const res = await API.approve(report.diagnosis_id, key(), reason);
    if (res.status === 401) { toast("danger", "Unauthorized", "Admin key rejected."); setLocked(true); setAuthOpen(true); return; }
    if (!res.ok) { toast("danger", "Approve failed", (res.data && res.data.error) || `HTTP ${res.status}`); return; }
    const until = res.data.approved_until;
    setOptimistic((p) => ({ ...p, [report.diagnosis_id]: { _status: "approved", approval: { approved_by: `fp ${fp || "admin"}`, approved_at: new Date().toISOString(), approved_until: until } } }));
    toast("success", "Plan approved", `Window open ${API.IDEM_TTL_MIN}:00 · Execute unlocked${reason ? ` · "${reason}"` : ""}`);
    reloadList({ quiet: true });
  };

  const onReject = async (report) => {
    if (!guard()) return;
    const res = await API.reject(report.diagnosis_id, key(), reason);
    if (res.status === 401) { toast("danger", "Unauthorized", "Admin key rejected."); setLocked(true); setAuthOpen(true); return; }
    if (!res.ok) { toast("danger", "Reject failed", (res.data && res.data.error) || `HTTP ${res.status}`); return; }
    setOptimistic((p) => ({ ...p, [report.diagnosis_id]: { _status: "rejected" } }));
    toast("info", "Plan rejected", `${shortId(report.diagnosis_id)} closed`);
    reloadList({ quiet: true });
  };

  const submitExecute = async (report, retrying) => {
    if (!guard()) return;
    const res = await API.execute(report.diagnosis_id, key());
    if (res.status === 401) { toast("danger", "Unauthorized", "Admin key rejected."); setLocked(true); setAuthOpen(true); return; }
    if (res.status === 202 || res.status === 200) {
      setOptimistic((p) => ({ ...p, [report.diagnosis_id]: { _status: "executing" } }));
      toast(retrying ? "info" : "success", retrying ? "Retry submitted" : "Execution queued",
        `${res.data?.execution_id || "exec"} · ${res.status === 202 ? "202 queued" : "200 (idempotent replay)"}`);
      reloadList({ quiet: true });
      setTimeout(() => enrichOne(reports.find((x) => x.diagnosis_id === report.diagnosis_id)), 800);
      return;
    }
    // documented failure matrix → explained, never a silent error
    const msg = (res.data && res.data.error) || `HTTP ${res.status}`;
    if (res.status === 403) toast("danger", "Approval required", "Plan is not approved, or the 30-min window expired.");
    else if (res.status === 409) toast("danger", "Idempotency conflict (409)", "An execution key for this plan is still inside its TTL window.");
    else if (res.status === 400) toast("danger", "Rejected (400)", msg.includes("schema") ? "Knowledge Agent only executes FixingPlan schema 2.0." : msg);
    else toast("danger", "Execute failed", msg);
  };
  const onExecute = (report) => submitExecute(report, false);
  const onRetry = (report) => submitExecute(report, true);

  // Re-approve an expired approval, then immediately retry execution.
  // Called when a failed_retryable plan's original 30-min approval window has elapsed.
  const onReapproveAndRetry = async (report) => {
    if (!guard()) return;
    // Step 1: open a fresh 30-min approval window
    const apRes = await API.approve(report.diagnosis_id, key(), reason);
    if (apRes.status === 401) { toast("danger", "Unauthorized", "Admin key rejected."); setLocked(true); setAuthOpen(true); return; }
    if (!apRes.ok) { toast("danger", "Re-approve failed", (apRes.data && apRes.data.error) || `HTTP ${apRes.status}`); return; }
    // Step 2: optimistically show approval window, then immediately submit retry.
    // If execute fails the approval row is still valid — the UI will show "Retry now"
    // on the next poll so the operator can retry without re-approving again.
    const until = apRes.data.approved_until;
    setOptimistic((p) => ({ ...p, [report.diagnosis_id]: {
      _status: "approved",
      approval: { approved_by: `fp ${fp || "admin"}`, approved_at: new Date().toISOString(), approved_until: until },
    }}));
    toast("info", "Re-approved", `Fresh 30-min window open · submitting retry…`);
    await submitExecute(report, true);
  };

  const onRediagnose = (report) => {
    if (!guard()) return;
    toast("info", "Re-diagnosis", "Trigger a fresh anomaly/RCA cycle from Layer 1–2; a new FixingPlan will appear in the queue.");
  };

  /* ---- auth actions (real /api/auth/check) ---- */
  const runAuthCheck = async () => {
    const res = await API.authCheck(key());
    if (res.ok && res.data && res.data.status === "ok") {
      setAuthState({ valid: true, fp: res.data.key?.fingerprint || "" });
      setAuthError("");
      return true;
    }
    setAuthState({ valid: false, fp: "" });
    setAuthError(`received length ${key().length}.`);
    return false;
  };
  const onTestKey = async () => {
    const ok = await runAuthCheck();
    if (ok) toast("success", "Admin key accepted", "Console can gate plans.");
    else toast("danger", "Admin key rejected", "Check the value or your access. No hint about the expected key is shown.");
  };
  const onUnlock = async () => {
    const ok = await runAuthCheck();
    if (ok) { setLocked(false); setAuthOpen(false); toast("success", "Console unlocked", "Gate actions enabled."); }
    else toast("danger", "Admin key rejected", "No hint about the expected key is shown.");
  };
  const onLock = () => { setLocked(true); setAuthState({ valid: false, fp: "" }); setAuthOpen(false); toast("info", "Console locked", "Read-only until unlocked"); };
  const onFillDemo = () => { toast("info", "Paste your admin key", "Accepts a raw key or AADS_ADMIN_API_KEY=… / export …"); };

  const onRefresh = () => { setRefreshing(true); reloadList().finally(() => setTimeout(() => setRefreshing(false), 500)); toast("info", "Refreshed", `Re-fetched ${hours}h window`); };
  const onSelect = (id) => setSelectedId(id);
  const onBack = () => { setSelectedId(null); document.body.classList.remove("has-selection"); };

  const handlers = { onApprove, onReject, onExecute, onRetry, onReapproveAndRetry, onRediagnose };

  return (
    <div className="console">
      <Topbar
        locked={locked} fp={fp} search={search} setSearch={setSearch} hours={hours} setHours={setHours}
        theme={t.theme} onToggleTheme={() => setTweak("theme", t.theme === "dark" ? "light" : "dark")}
        onOpenAuth={() => setAuthOpen(true)} onOpenFleet={() => setFleetOpen(true)}
        onRefresh={onRefresh} refreshing={refreshing}
      />
      <InstrumentRail stats={stats} counts={counts} />
      <div className="workspace">
        <QueuePane key={`${t.theme}-${t.accent}`} reports={filtered} tab={tab} setTab={setTab} selectedId={selectedId} onSelect={onSelect} counts={counts} now={now} />
        <DetailPane report={selected} locked={locked} agents={agents} reason={reason} setReason={setReason} onBack={onBack} handlers={handlers} now={now} />
      </div>

      <MobileTabbar tab={tab} setTab={(x) => { setTab(x); onBack(); }} onOpenFleet={() => setFleetOpen(true)} counts={counts} />
      <FleetDrawer open={fleetOpen} onClose={() => setFleetOpen(false)} agents={agents} stats={stats} />
      <AuthModal
        open={authOpen} onClose={() => setAuthOpen(false)} value={adminKey} setValue={(v) => { setAdminKey(v); setAuthError(""); setAuthState({ valid: false, fp: "" }); }}
        onTest={onTestKey} onUnlock={onUnlock} onLock={onLock} locked={locked} fp={fp} diag={diag}
        show={showKey} setShow={setShowKey} onFillDemo={onFillDemo}
      />
      <Toasts toasts={toasts} />

      {loadError && (
        <div className="toast-wrap"><div className="toast danger"><span className="tled" /><div><div className="tt">Backend unreachable</div><div className="tb">{loadError}</div></div></div></div>
      )}

      <TweaksPanel>
        <TweakSection label="Console" />
        <TweakRadio label="Theme" value={t.theme} options={["dark", "light"]} onChange={(v) => setTweak("theme", v)} />
        <TweakRadio label="Density" value={t.density} options={["compact", "regular", "comfy"]} onChange={(v) => setTweak("density", v)} />
        <TweakColor label="Accent" value={t.accent} options={Object.keys(ACCENTS)} onChange={(v) => setTweak("accent", v)} />
        <TweakSection label="Data" />
        <TweakButton label="Reload now" onClick={() => { reloadList(); toast("info", "Reloaded", "Re-fetched from backend"); }}>Reload</TweakButton>
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
