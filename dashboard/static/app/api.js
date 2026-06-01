/* ============================================================
   AADS Gate Console — live API adapter (window.AADS_API)

   Bridges the real Flask backend to the console's report shape.
   - GET  /api/diagnosis?hours=N        -> reports[]
   - GET  /api/agents                   -> agents[]
   - GET  /api/stats?hours=N            -> instrument-rail stats
   - GET  /api/plans/<id>/execution     -> execution trace
   - GET  /api/plans/<id>/approval      -> latest approval (for countdown)
   - POST /api/auth/check               -> admin-key validation
   - POST /api/plans/<id>/approve|reject|execute  (gate actions)
   ============================================================ */
(function () {
  const IDEM_TTL_MIN = 30; // approval + idempotency TTL (matches backend)

  /* ---------- low-level fetch ---------- */
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  }
  async function postJSON(url, { key, idem, body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (key) headers["X-Admin-API-Key"] = key;
    if (idem) headers["Idempotency-Key"] = idem;
    const r = await fetch(url, { method: "POST", headers, body: JSON.stringify(body || {}) });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    return { ok: r.ok, status: r.status, data };
  }
  function mintIdem() {
    const a = crypto.getRandomValues(new Uint8Array(8));
    return Array.from(a).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  /* ---------- report normalization ----------
     The detail pane reads plan.requires_approval, plan.auto_execute and a
     human string final_verification.expect. The backend keeps those under
     environment_policy / final_verification.expected, so lift + derive here. */
  function expectString(fv) {
    if (!fv) return "—";
    const e = fv.expected || {};
    if (e.http_status != null) return `http ${e.http_status}`;
    if ("active" in e) return e.active ? "service active" : "inactive";
    const keys = Object.keys(e);
    return keys.length ? keys.map((k) => `${k}: ${e[k]}`).join(", ") : (fv.command_id || "—");
  }
  function mapReport(d) {
    const plan = d.action_plan || {};
    const pol = plan.environment_policy || {};
    const fv = plan.final_verification
      ? { ...plan.final_verification, expect: expectString(plan.final_verification) }
      : {};

    // A schema != 2.0 plan has no executable steps regardless of plan_status.
    // Normalise to needs_triage so the queue bucket and status badge are consistent:
    //   - "approved"/"pending_approval" on a schema 1.0 item is misleading noise.
    //   - "rejected" is already a terminal state and should stay as-is.
    const isNonExecutable = !plan.schema_version || plan.schema_version !== "2.0";
    const terminalOrRejected = ["rejected", "blocked", "execution_failed",
      "execution_failed_unknown_state", "final_verified", "kb_imported",
      "kb_skipped", "kb_import_failed"].includes(d.plan_status);
    const effectiveStatus = (isNonExecutable && !terminalOrRejected)
      ? "needs_triage"
      : d.plan_status;

    return {
      ...d,
      plan_status: effectiveStatus,
      action_plan: {
        ...plan,
        requires_approval: pol.requires_approval ?? plan.requires_approval,
        auto_execute: pol.auto_execute_allowed === true,
        final_verification: fv,
      },
    };
  }

  /* ---------- execution -> trace rows ---------- */
  const STEP_STATE = {
    step_verified: "ok",
    step_running: "run",
    step_failed_aborted: "fail",
    step_failed_blocked: "fail",
    step_failed_retried: "run",
  };
  function relTime(startISO, endISO) {
    if (!startISO || !endISO) return undefined;
    const ms = new Date(endISO).getTime() - new Date(startISO).getTime();
    if (!Number.isFinite(ms) || ms < 0) return undefined;
    return `+${(ms / 1000).toFixed(1)}s`;
  }
  function latestExecution(execs) {
    if (!execs || !execs.length) return null;
    // /execution returns newest-first already, but be defensive.
    return execs.slice().sort((a, b) =>
      new Date(b.requested_at) - new Date(a.requested_at))[0];
  }
  function traceFromExecution(exec, plan) {
    if (!exec) return null;
    const result = exec.result || {};
    const byStep = {};
    (exec.steps || []).forEach((s) => { byStep[s.step_id] = s; });
    const rows = [];

    // S — pre-execution snapshot (step_id 0)
    const snap = byStep[0];
    if (snap || result.pre_execution_snapshot) {
      rows.push({
        tn: "S",
        command_id: (plan.pre_execution_snapshot || {}).command_id || (snap && snap.command_id) || "ensure_known_good_snapshot",
        state: STEP_STATE[snap && snap.status] || (snap ? "run" : "wait"),
        at: relTime(exec.started_at, snap && snap.finished_at),
        note: snap && snap.status === "step_verified" ? "snapshot captured" : undefined,
      });
    }

    // 1..N — ordered plan steps
    (plan.steps || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0)).forEach((ps) => {
      const s = byStep[ps.step_id];
      const verif = (s && s.result && s.result.verification) || {};
      rows.push({
        tn: String(ps.order || ps.step_id),
        command_id: ps.command_id,
        state: s ? (STEP_STATE[s.status] || "wait") : "wait",
        at: relTime(exec.started_at, s && s.finished_at),
        expected: (ps.verification && ps.verification.expected) || verif.expected || null,
        observed: verif.observed || null,
      });
    });

    // R — rollback (snapshot restore) if the run rolled back
    if (result.rollback) {
      const done = result.rollback.status === "rollback_completed";
      rows.push({
        tn: "R",
        command_id: "nginx.restore_known_good_config",
        state: "roll",
        note: done ? "snapshot restored — no partial state left on target" : "rollback attempted",
      });
    }

    // F — final verification
    const fv = result.final_verification;
    if (fv) {
      const ok = fv.status === "success" || (fv.observed && !(fv.mismatches || []).length);
      let state = "wait";
      if (ok) state = "ok";
      else if (exec.status === "final_verifying") state = "run";
      else if (exec.status === "execution_failed") state = "fail";
      rows.push({
        tn: "F",
        command_id: (plan.final_verification || {}).command_id || "http_check",
        state,
        at: relTime(exec.started_at, exec.finished_at),
        note: ok ? "final verification passed" : undefined,
        expected: fv.expected || null,
        observed: fv.observed || null,
      });
    }
    return rows;
  }

  function runProgress(exec, plan) {
    const total = (plan.steps || []).length || 1;
    const verified = (exec.steps || []).filter(
      (s) => s.step_id > 0 && s.status === "step_verified").length;
    const running = (exec.steps || []).find((s) => s.step_id > 0 && s.status === "step_running");
    const stepIndex = running ? Math.max(0, running.step_id - 1) : Math.min(total - 1, verified);
    return { stepIndex, total };
  }

  const FRIENDLY_REASON = {
    agent_unreachable_or_unknown: "On-Device Agent unreachable (connect timeout)",
    node_locked: "target node is locked by another execution",
    unknown_node: "target node not registered",
  };
  function retryFromExecution(exec) {
    const result = exec.result || {};
    const reason = result.reason || "transient failure";
    return {
      reason: FRIENDLY_REASON[reason] || reason,
      code: 409, // re-submitting within the idempotency window returns 409
      attempt: (exec.retry_count || 0) + 1,
      retry_eligible_at: exec.finished_at
        ? new Date(new Date(exec.finished_at).getTime() + IDEM_TTL_MIN * 60000).toISOString()
        : null,
    };
  }
  function auditFromExecution(exec, approval) {
    const result = exec.result || {};
    const kb = result.knowledge_base || {};
    const kbText = kb.status === "kb_skipped" ? "skipped (KB disabled)"
      : kb.status === "kb_imported" ? `imported · ${kb.case_id || "case"}`
      : kb.status === "kb_import_failed" ? `import FAILED — ${kb.error || "KB error"} (repair preserved)`
      : kb.status || "—";
    return {
      approved_by: approval ? approval.approved_by : "—",
      approved_at: approval ? approval.approved_at : null,
      executed: exec.requested_at,
      execution_id: exec.execution_id,
      idem: exec.idempotency_key,
      rollbacks: result.rollback ? 1 : 0,
      kb: kbText,
      verified_at: exec.finished_at,
    };
  }

  /* ---------- public surface ---------- */
  const RESOLVED_SET = new Set(["final_verified", "kb_imported", "kb_skipped", "kb_import_failed"]);
  const INFLIGHT_SET = new Set(["queued", "executing", "final_verifying"]);
  const FAIL_SET = new Set(["execution_failed", "blocked", "execution_failed_unknown_state"]);

  async function loadAll(hours) {
    const [diag, agents, stats] = await Promise.all([
      getJSON(`/api/diagnosis?hours=${hours}`).catch(() => []),
      getJSON(`/api/agents`).catch(() => []),
      getJSON(`/api/stats?hours=${hours}`).catch(() => null),
    ]);
    return { reports: (diag || []).map(mapReport), agents: agents || [], stats: normalizeStats(stats) };
  }
  function normalizeStats(s) {
    if (!s) return { anomalies: { total: 0, by_container: [] }, diagnoses: { total: 0, by_severity: [] } };
    return {
      anomalies: { total: s.anomalies?.total || 0, by_container: s.anomalies?.by_container || [] },
      diagnoses: { total: s.diagnoses?.total || 0, by_severity: s.diagnoses?.by_severity || [] },
    };
  }

  // Build the decoration the console needs for one report (trace/approval/retry/audit).
  async function enrichReport(report) {
    const id = report.diagnosis_id;
    const plan = report.action_plan || {};
    const status = report.plan_status;
    const out = {};

    // approval — needed for the 30-min countdown (approved items) AND to know
    // whether a failed_retryable plan still has valid approval for retry.
    if (status === "approved" || status === "failed_retryable") {
      try {
        const ap = await getJSON(`/api/plans/${id}/approval`);
        if (ap && ap.decision === "approved" && ap.approved_until) {
          out.approval = {
            approved_by: ap.actor || "admin",
            approved_at: ap.created_at || null,
            approved_until: ap.approved_until,
          };
        }
      } catch (e) {}
    }

    // execution-derived decorations
    const needsExec = INFLIGHT_SET.has(status) || FAIL_SET.has(status)
      || status === "failed_retryable" || RESOLVED_SET.has(status);
    if (needsExec || plan.schema_version === "2.0") {
      try {
        const execs = await getJSON(`/api/plans/${id}/execution`);
        const exec = latestExecution(execs);
        if (exec) {
          out.trace = {
            rows: traceFromExecution(exec, plan),
            execution_id: exec.execution_id,
            idem: exec.idempotency_key,
          };
          if (INFLIGHT_SET.has(status)) out._run = runProgress(exec, plan);
          if (status === "failed_retryable") out.retry = retryFromExecution(exec);
          if (FAIL_SET.has(status)) out.rolled_back = !!(exec.result && exec.result.rollback);
          if (RESOLVED_SET.has(status)) out.audit = auditFromExecution(exec, out.approval);
        }
      } catch (e) {}
    }
    return out;
  }

  /* ---------- gate actions ---------- */
  const authCheck = (key) => postJSON(`/api/auth/check`, { key });
  const approve = (id, key, reason) => postJSON(`/api/plans/${id}/approve`, { key, body: { reason } });
  const reject = (id, key, reason) => postJSON(`/api/plans/${id}/reject`, { key, body: { reason } });
  const execute = (id, key) => postJSON(`/api/plans/${id}/execute`, { key, idem: mintIdem() });

  window.AADS_API = {
    IDEM_TTL_MIN, loadAll, enrichReport, mapReport,
    authCheck, approve, reject, execute, mintIdem,
    RESOLVED_SET, INFLIGHT_SET, FAIL_SET,
  };
})();
