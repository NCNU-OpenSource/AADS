/* ============================================================
   AADS Gate Console — right detail pane
   Renders gate stepper, plan audit, live trace, history & attention.
   Reads decorated report: _status (effective), _liveTrace, _run
   ============================================================ */

function effectiveTrace(report) {
  return report._liveTrace || report.trace && report.trace.rows || null;
}

/* ---- stepper ---- */
function stepperModel(status) {
  // returns [{state}] for Approve, Execute, Verify
  const m = { approve: "idle", execute: "idle", verify: "idle", execTone: "blue" };
  if (status === "pending_approval") {m.approve = "active-amber";} else
  if (status === "approved") {m.approve = "done";m.execute = "active-blue";} else
  if (status === "queued" || status === "executing") {m.approve = "done";m.execute = "active-blue";} else
  if (status === "final_verifying") {m.approve = "done";m.execute = "done";m.verify = "active-blue";} else
  if (RESOLVED.has(status)) {m.approve = "done";m.execute = "done";m.verify = "done";} else
  if (status === "failed_retryable") {m.approve = "done";m.execute = "active-amber";} else
  if (status === "paused_for_review") {m.approve = "done";m.execute = "active-amber";} else
  if (status === "execution_failed" || status === "blocked") {m.approve = "done";m.execute = "fail";}
  return m;
}
function Step({ n, label, sub, state }) {
  let cls = "step";
  let inner = n;
  if (state === "done") {cls += " done";inner = <Icon name="check-lg" />;} else
  if (state === "active-amber") cls += " amber active";else
  if (state === "active-blue") cls += " blue active";else
  if (state === "fail") {cls += " active";}
  return (
    <div className={cls} style={state === "fail" ? { borderColor: "var(--red-line)", background: "var(--red-bg)" } : undefined}>
      <span className="sn" style={state === "fail" ? { background: "var(--red)" } : undefined}>{state === "fail" ? <Icon name="x-lg" /> : inner}</span>
      <div><div className="sl">{label}</div><div className="ss">{sub}</div></div>
    </div>);

}
function GateStepper({ status }) {
  const m = stepperModel(status);
  return (
    <div className="stepper">
      <Step n="1" label="Approve" sub="manual gate" state={m.approve} />
      <Step n="2" label="Execute" sub="controller-driven" state={m.execute} />
      <Step n="3" label="Verify" sub="final probe" state={m.verify} />
    </div>);

}

/* ---- plan audit (gate contract + sequence) ---- */
/* V2: runner-based labels (replaces legacy command_id). */
function runnerLabel(runner) {
  if (!runner || !runner.argv || !runner.argv.length) return "n/a";
  const base = runner.argv[0].split("/").pop();
  return runner.argv.length > 1 ? `${base} ${runner.argv.slice(1).join(" ")}` : base;
}
function stepLabel(s) {
  const ctx = s.context || {};
  if (ctx.service && ctx.operation) return `${ctx.service}.${ctx.operation}`;
  return runnerLabel(s.runner);
}
function expectSummary(v) {
  const e = (v && v.expected) || {};
  const keys = Object.keys(e);
  return keys.length ? keys.map((k) => `${k}=${e[k]}`).join(", ") : "—";
}

function PlanBody({ plan }) {
  const snap = plan.pre_execution_snapshot || {};
  const finalV = plan.final_verification || {};
  const steps = (plan.steps || []).slice().sort((a, b) => (a.order || 0) - (b.order || 0));
  return (
    <div className="plan-grid">
      <div>
        <div className="sec-label">Gate contract<span className="ln" /></div>
        <div className="contract">
          <div className="cstep">
            <span className="ix snap">S</span>
            <div>
              <code>{snap.enabled !== false ? runnerLabel(snap.runner) : "snapshot disabled"}</code>
              <div className="cmeta">scope {snap.scope || "n/a"} · on_fail: {snap.on_failure || "block"}</div>
            </div>
          </div>
          <div className="cstep">
            <span className="ix final">F</span>
            <div>
              <code>{runnerLabel(finalV.runner)}</code>
              <div className="cmeta">final probe · expect {expectSummary(finalV)}</div>
            </div>
          </div>
        </div>
      </div>
      <div data-comment-anchor="3e6d341cce-div-75-7">
        <div className="sec-label">FixingPlan sequence · {steps.length} step{steps.length === 1 ? "" : "s"}<span className="ln" /></div>
        {plan.goal && <p className="objective">{plan.goal}</p>}
        <div className="seq">
          {steps.map((s) =>
          <div className="srow" key={s.order}>
              <span className="ix">{s.order}</span>
              <div>
                <div className="stitle">
                  <code>{stepLabel(s)}</code>
                  {s.runner?.as_root && <Pill tone="amber" mono>root</Pill>}
                  <Pill tone={s.runner?.side_effect === "mutate" ? "violet" : "grey"} mono>{s.runner?.side_effect || "read"}</Pill>
                  <Pill tone={s.on_failure === "rollback" ? "violet" : "grey"} mono>on_fail {s.on_failure}</Pill>
                  <Pill plain mono>verify {runnerLabel(s.verification?.runner)}</Pill>
                </div>
                <div className="sdetail">{s.expected_outcome}</div>
                <JsonBlock data={s.verification?.expected || {}} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>);

}

/* ---- approve checklist ---- */
function ApproveChecklist({ report, plan, agent }) {
  const snap = plan.pre_execution_snapshot || {};
  const finalV = plan.final_verification || {};
  const hasRollback = (plan.steps || []).some((s) => s.on_failure === "rollback");
  const items = [
  { label: "target", ok: !!agent, detail: agent ? `${shortId(agent.node_id)} · agent ${agent.agent_version} · seen ${fmtTime(agent.last_seen)}` : "target not registered" },
  { label: "snapshot", ok: snap.enabled !== false, detail: `${snap.enabled !== false ? runnerLabel(snap.runner) : "none"} · on_fail ${snap.on_failure || "block"}` },
  { label: "rollback", ok: hasRollback, detail: hasRollback ? "at least one step reverts on failure" : "no rollback contract" },
  { label: "verify", ok: !!(finalV.runner && finalV.runner.argv), detail: `final ${runnerLabel(finalV.runner)} → ${expectSummary(finalV)}` }];

  return (
    <div className="checklist">
      {items.map((it) =>
      <div className="cl-item" key={it.label}>
          <span className="ck" style={it.ok ? undefined : { background: "var(--amber-bg)", color: "var(--amber)", borderColor: "var(--amber-line)" }}>
            <Icon name={it.ok ? "check-lg" : "exclamation"} />
          </span>
          <span className="strong" style={{ width: 66 }}>{it.label}</span>
          <span className="mono grow nowrap">{it.detail}</span>
        </div>
      )}
    </div>);

}

/* ---- trace ---- */
function ObservedView({ row }) {
  if (!row.expected) return null;
  const running = row.state === "run";
  const matched = row.state === "ok" && row.observed;
  return (
    <div className="expvar">
      <div className="ev">
        <div className="evl">expected</div>
        <JsonBlock data={row.expected} />
      </div>
      <div className={`ev ${matched ? "match" : ""}`}>
        <div className="evl">{matched ? <React.Fragment><Icon name="check-lg" /> observed</React.Fragment> : "observed"}</div>
        {running && !row.observed ?
        <div className="probing"><span className="cursor-blink">probing</span></div> :
        row.observed ? <JsonBlock data={row.observed} /> : <div className="muted xs mono" style={{ marginTop: 6 }}>—</div>}
      </div>
    </div>);

}
function TraceRow({ row }) {
  const cls = `trow ${row.state}`;
  const badge = {
    ok: <Pill tone="green" className="xs">✓ verified</Pill>,
    run: <Pill tone="blue" led pulse className="xs">running</Pill>,
    fail: <Pill tone="red" className="xs">failed</Pill>,
    roll: <Pill tone="violet" className="xs">rolled back</Pill>,
    wait: <Pill plain className="xs muted">queued</Pill>
  }[row.state];
  return (
    <div className={cls}>
      <span className="tn">{row.tn}</span>
      <div>
        <div className="row tight wrap">
          <code>{row.command_id}</code>
          {badge}
        </div>
        {row.note && <div className="sdetail" style={{ marginTop: 6 }}>{row.note}</div>}
        <ObservedView row={row} />
      </div>
      <span className="tt">{row.at || "—"}</span>
    </div>);

}
function TraceCard({ report, run }) {
  const rows = effectiveTrace(report);
  if (!rows) return null;
  const meta = run || report.trace || {};
  const st = report._status;
  return (
    <div className="card">
      <div className="dhead" style={{ borderBottom: "1px solid var(--line)" }}>
        <div className="row tight">
          <Icon name="terminal" /><span className="strong">Execution trace</span>
        </div>
        <div className="row tight wrap" style={{ justifyContent: "flex-end" }}>
          <StatusPill status={st} />
          {meta.execution_id && <Tag k="exec">{shortId(meta.execution_id)}</Tag>}
          {meta.idem && <Tag k="idem">{meta.idem}</Tag>}
        </div>
      </div>
      <div className="card-pad">
        <div className="trace">
          {rows.map((r, i) => <TraceRow key={i} row={r} />)}
        </div>
      </div>
    </div>);

}

/* ---- audit ---- */
function AuditBlock({ audit }) {
  if (!audit) return null;
  return (
    <div className="audit">
      <div className="sec-label">Audit trail<span className="ln" /></div>
      <div className="ajson">
        <span className="lbl">approved </span>{audit.approved_by} @ {fmtTime(audit.approved_at)}<br />
        <span className="lbl">executed </span>{shortId(audit.execution_id)} · idem {audit.idem}<br />
        <span className="lbl">rollbacks </span>{audit.rollbacks} · <span className="lbl">verified </span>{fmtTime(audit.verified_at)}<br />
        <span className="lbl">kb </span>{audit.kb}
      </div>
      <div className="row tight" style={{ marginTop: 11 }}>
        <Btn size="sm" icon="clipboard">Copy audit JSON</Btn>
        <Btn size="sm" icon="box-arrow-up-right">Open run</Btn>
      </div>
    </div>);

}

/* ============================================================
   The gate / action zone — varies by status
   ============================================================ */
function GateZone({ report, locked, reason, setReason, onApprove, onReject, onExecute, onRetry, onReapproveAndRetry, onResume, onAbort, onRediagnose, now }) {
  const st = report._status;
  const plan = report.action_plan || {};
  const auto = (plan.environment_policy || {}).auto_execute_allowed === true;

  // PAUSED FOR REVIEW → drift escalation: human decides (ADR-006)
  if (st === "paused_for_review") {
    const esc = report.escalation || {};
    const ap = report.approval || {};
    const approvalValid = ap.approved_until && new Date(ap.approved_until).getTime() > now;
    const DRIFT_LABEL = {
      approved_plan_hash_mismatch: "plan content changed after approval",
      policy_violation: "On-Device PolicyCard denied a command of this approved plan",
      verification_failed: "a step's verification probe did not match the expected state",
      step_retries_exhausted: "a mutating step kept failing after all retries",
      final_verification_failed: "the final verification probe failed after all steps ran",
      unrecoverable_step_state: "a step was left in a state the executor cannot safely recover",
    };
    return (
      <div className="card">
        <div className="card-pad">
          <div className="callout amber">
            <Icon name="exclamation-diamond" />
            <div>
              <strong>Execution paused — drift detected.</strong> {DRIFT_LABEL[esc.drift_type] || "the execution diverged from the approved plan"}.
              <div className="xs muted mono" style={{ marginTop: 7 }}>
                drift_type: {esc.drift_type || "—"} · escalation {shortId(esc.escalation_id || "—")} · {fmtTime(esc.created_at)}
              </div>
            </div>
          </div>
          {esc.details && <JsonBlock data={esc.details} className="xs" />}
        </div>
        <div className="gatebar">
          <div className="gate-msg">
            <strong>{approvalValid ? "Resume re-runs from the failed step" : "Re-approval required before resume"}</strong>
            <span>Resume needs a valid approval window; Abort closes this execution; Re-diagnose starts fresh RCA.</span>
          </div>
          <div className="gate-actions">
            <Btn variant="danger" icon="x-circle" disabled={locked} onClick={() => onAbort(report)}>Abort</Btn>
            <Btn icon="diagram-3" disabled={locked} onClick={() => onRediagnose(report)}>Re-diagnose</Btn>
            <Btn variant="approve" icon="check2-circle" disabled={locked} onClick={() => onResume(report)}>
              {approvalValid ? "Resume" : "Re-approve & Resume"}
            </Btn>
          </div>
        </div>
      </div>);

  }

  // AWAITING → approve
  if (st === "pending_approval") {
    return (
      <div className="card">
        <div className="card-pad">
          <div className="sec-label">Gate · manual approval required<span className="ln" /></div>
          <textarea className="reason-input" placeholder="Reason (optional) — e.g. confirmed lab stop condition" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <div className="gatebar">
          <div className="gate-msg">
            <strong>{locked ? "Locked — unlock the admin key to act" : "Approval grants a 30-minute execution window"}</strong>
            <span>POST /approve → returns approved_until (+30 min). Execute unlocks next.</span>
          </div>
          <div className="gate-actions">
            <Btn variant="danger" icon="x-circle" disabled={locked} onClick={() => onReject(report)}>Reject</Btn>
            <Btn variant="approve" icon="check2-circle" disabled={locked} onClick={() => onApprove(report)}>Approve plan</Btn>
          </div>
        </div>
      </div>);

  }

  // APPROVED → execute, with countdown
  if (st === "approved") {
    const ap = report.approval || {};
    const total = 30 * 60000;
    const remaining = Math.max(0, new Date(ap.approved_until).getTime() - now);
    const pct = Math.max(0, Math.min(100, remaining / total * 100));
    const warn = remaining < 5 * 60000;
    return (
      <div className="card">
        <div className="countbar">
          <div className="count-top">
            <span className="row tight"><Icon name="stopwatch" /><span className="sm strong">Approval window</span></span>
            <span className="count-time" style={{ color: warn ? "var(--amber)" : "var(--blue)" }}>{fmtClock(remaining)}</span>
          </div>
          <div className="track"><div className={`fill ${warn ? "warn" : ""}`} style={{ width: pct + "%" }} /></div>
          <div className="xs muted mono" style={{ marginTop: 8 }}>
            Approved by {ap.approved_by} @ {fmtTime(ap.approved_at)}. On expiry the item returns to pending_approval — no silent failures.
          </div>
        </div>
        <div className="card-pad" style={{ borderTop: "1px solid var(--line)" }}>
          <div className="row tight" style={{ marginBottom: 9, justifyContent: "space-between" }}>
            <span className="xs mono muted"><Icon name="shield-lock" /> Idempotency-Key auto-generated — one safe submit</span>
            <Pill tone="blue" mono>{auto ? "auto-run policy" : "manual"}</Pill>
          </div>
          <Btn variant="exec" className="block lg" icon="play-fill" disabled={locked} onClick={() => onExecute(report)}>Execute FixingPlan</Btn>
          <div className="xs muted mono" style={{ marginTop: 9 }}>POST /execute → 202 queued. Button disables on submit; the trace streams below.</div>
        </div>
        <div className="gatebar">
          <div className="gate-msg"><span>Changed your mind? You can still reject before executing.</span></div>
          <Btn variant="danger" size="sm" icon="x-circle" disabled={locked} onClick={() => onReject(report)}>Reject</Btn>
        </div>
      </div>);

  }

  // IN-FLIGHT → no actions
  if (INFLIGHT.has(st)) {
    return (
      <div className="card"><div className="card-pad">
        <div className="callout"><Icon name="hourglass-split" /><div><strong>Executing — controller-driven.</strong> No operator action while the agent runs the sequence. The live trace streams below; verification settles the terminal state.</div></div>
      </div></div>);

  }

  // FAILED RETRYABLE → retry (with re-approval gate if the original approval expired)
  if (st === "failed_retryable") {
    const rt = report.retry || {};
    const remaining = Math.max(0, new Date(rt.retry_eligible_at).getTime() - now);
    const ready = remaining <= 0;
    // Check whether the original approval is still valid (30-min window).
    // If expired, the operator must re-approve before the executor will accept the request.
    const ap = report.approval || {};
    const approvalValid = ap.approved_until && new Date(ap.approved_until).getTime() > now;
    const needsReapproval = ready && !approvalValid;

    return (
      <div className="card">
        <div className="card-pad">
          <div className="callout amber">
            <div>
              <strong>Why this failed:</strong> last attempt returned <code>{rt.code}</code> — {rt.reason}. The executor does not auto-retry.
              <div className="xs muted mono" style={{ marginTop: 7 }}>cause: {rt.reason} · attempt {rt.attempt}</div>
            </div>
          </div>
          {needsReapproval && (
            <div className="callout" style={{ marginTop: 10, background: "var(--panel-2)", border: "1px solid var(--line)" }}>
              <Icon name="clock" />
              <div className="xs muted">
                <strong>Original approval expired.</strong> The 30-minute window from the first approval has elapsed. You must re-approve before retrying — the executor will reject the request otherwise.
              </div>
            </div>
          )}
        </div>
        <div className="gatebar">
          {needsReapproval ? (
            <React.Fragment>
              <div className="gate-msg">
                <strong>Re-approve required before retry</strong>
                <span>Approval opens a fresh 30-min window, then the retry is submitted automatically.</span>
              </div>
              <div className="gate-actions">
                <Btn variant="approve" icon="check2-circle" disabled={locked} onClick={() => onReapproveAndRetry(report)}>
                  Re-approve &amp; Retry
                </Btn>
              </div>
            </React.Fragment>
          ) : (
            <React.Fragment>
              <div className="gate-msg">
                <strong>{ready ? "Retry window open" : `Retry unlocks in ${fmtClock(remaining)}`}</strong>
                <span>A fresh idempotency-key is minted on retry.</span>
              </div>
              <div className="gate-actions">
                <Btn variant={ready ? "amber" : ""} icon="arrow-repeat" disabled={locked || !ready} onClick={() => onRetry(report)}>
                  {ready ? "Retry now" : `Retry in ${fmtClock(remaining)}`}
                </Btn>
              </div>
            </React.Fragment>
          )}
        </div>
      </div>);
  }

  // TERMINAL FAILURE → re-diagnose
  if (st === "execution_failed" || st === "blocked") {
    return (
      <div className="card">
        <div className="card-pad">
          <div className="callout red">
            <span className="fixflag">FIX 3</span>
            <div>
              <strong>Terminal failure — out of the active queue.</strong> {report.rolled_back ? "A step failed and the snapshot was restored automatically; no partial state was left on the target." : "Execution is blocked by schema, catalog, approval, or environment policy."} Safe next step: re-run RCA, then a fresh plan.
            </div>
          </div>
        </div>
        <div className="gatebar">
          <div className="gate-msg"><strong>This FixingPlan will not be replayed from the Gate.</strong><span>Create a new diagnosis to run another repair.</span></div>
          <div className="gate-actions">
            <Btn variant="danger" icon="diagram-3" disabled={locked} onClick={() => onRediagnose(report)}>Re-diagnose</Btn>
          </div>
        </div>
      </div>);

  }

  // RESOLVED → audit only (rendered as audit on the trace card area)
  if (RESOLVED.has(st)) {
    return (
      <div className="card">
        <div className="card-pad" style={{ paddingBottom: 4 }}>
          <div className="callout grey">
            <Icon name={st === "kb_import_failed" ? "exclamation-triangle" : "check-circle"} />
            <div>{terminalMessage(st)}</div>
          </div>
        </div>
        <AuditBlock audit={report.audit} />
      </div>);

  }
  return null;
}

function terminalMessage(status) {
  switch (status) {
    case "kb_skipped":return <span><strong>Resolved — repair passed final verification.</strong> KB import was skipped because the knowledge base is disabled. <strong>kb_skipped is success</strong>, not a failure.</span>;
    case "kb_imported":return <span><strong>Resolved — verified and imported into the knowledge base.</strong> Future diagnoses can reuse this case.</span>;
    case "kb_import_failed":return <span><strong>Resolved — repair verified and preserved.</strong> The KB write failed (endpoint 503); execution stays locked so the repair result is not replayed. The repair itself is a success.</span>;
    default:return <span><strong>Resolved — final verification passed.</strong> Kept in Repair History for trace review.</span>;
  }
}

/* ============================================================
   DetailPane
   ============================================================ */
function DetailPane({ report, locked, agents, reason, setReason, onBack, handlers, now }) {
  if (!report) {
    return (
      <div className="pane detail">
        <div className="empty-detail">
          <div>
            <div className="big">Select a remediation item</div>
            <div className="muted sm">The queue stays pinned on the left — read a plan without losing your place in triage.</div>
          </div>
        </div>
      </div>);

  }

  const plan = report.action_plan || {};
  const root = report.root_cause || {};
  const policy = plan.environment_policy || {};
  const schema = plan.schema_version || report.schema_version || "legacy";
  const v2 = isSchemaV2(report);
  const target = plan.target_node_id || root.target_node_id;
  const agent = (agents || []).find((a) => a.node_id === target);
  const auto = policy.auto_execute_allowed === true;
  const run = report._run || report.trace;

  return (
    <div className="pane detail">
      <div className="detail-wrap">
        {/* header */}
        <div className="card">
          <div className="dhead">
            <div className="grow">
              <button className="btn ghost sm back-btn" style={{ marginBottom: 10 }} onClick={onBack}><Icon name="chevron-left" /> Queue</button>
              <h2>{report.summary}</h2>
              <div className="idline">
                <span><Icon name="hash" /> {report.diagnosis_id}</span>
                <span className="dot">·</span>
                <span>target {shortId(target || "n/a")}</span>
                <span className="dot">·</span>
                <span><Icon name="clock" /> {fmtTime(report.timestamp)}</span>
              </div>
            </div>
            <div className="dhead-right">
              <SevPill severity={report.severity} />
              <StatusPill status={report._status} lg />
            </div>
          </div>
          <div className="metarow">
            <Pill plain mono>schema {schema}</Pill>
            <Tag k="env">{policy.environment || "—"}</Tag>
            <Tag k="risk">{plan.risk_level || "n/a"}</Tag>
            {v2 && <Tag k="approval">{plan.requires_approval ? "required" : "not required"}</Tag>}
            {v2 && <Pill tone={auto ? "green" : "amber"} mono>{auto ? "lab auto-run" : "manual gate"}</Pill>}
            {agent && <Pill tone={agent.environment === "test" ? "green" : "amber"} mono led>agent {agent.agent_version}</Pill>}
          </div>
        </div>

        {/* NON-2.0 → needs triage (fix #2) */}
        {!v2 ?
        <React.Fragment>
            <div className="card" style={{ marginTop: 16 }}><div className="card-pad">
              <div className="callout grey">
                <span className="fixflag">FIX 2</span>
                <div>
                  <strong>Analysis-failed — no executable FixingPlan.</strong> The System Agent produced no node_agent steps, so this terminated at schema {schema}. The Knowledge Agent only executes FixingPlan v2 — <strong>Approve & Execute are intentionally hidden</strong>. This is a read-only triage item.
                </div>
              </div>
            </div></div>
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-pad">
                <div className="sec-label">Root cause report · confidence {fmtPct(root.confidence)}<span className="ln" /></div>
                <p className="objective" style={{ margin: 0 }}>{root.description}</p>
                <div className="row tight wrap" style={{ marginTop: 12 }}>
                  {root.affected_service && <Tag k="service">{root.affected_service}</Tag>}
                  <Btn size="sm" icon="diagram-3">Open RCA (read-only)</Btn>
                </div>
              </div>
            </div>
          </React.Fragment> :

        <React.Fragment>
            {/* stepper — full width */}
            <div style={{ marginTop: 16 }}><GateStepper status={report._status} /></div>

            {/* two-column body */}
            <div className="detail-body">
              {/* LEFT: plan audit + checklist + RCA */}
              <div className="detail-col-plan">
                <div className="card">
                  <PlanBody plan={plan} />
                  <details className="rca">
                    <summary><Icon name="chevron-right" className="chev" /> Root cause report · confidence {fmtPct(root.confidence)}</summary>
                    <p>{root.description}</p>
                    <div className="conf">
                      {root.affected_service && <Tag k="service">{root.affected_service}</Tag>}
                      {(root.recommended_capabilities || []).map((c) => <Tag key={c}>{c}</Tag>)}
                    </div>
                  </details>
                </div>

                {report._status === "pending_approval" &&
                <div className="card"><div className="card-pad">
                    <div className="sec-label">Before you approve, confirm<span className="ln" /></div>
                    <ApproveChecklist report={report} plan={plan} agent={agent} />
                  </div></div>
                }
              </div>

              {/* RIGHT: gate zone + trace (sticky) */}
              <div className="detail-col-gate">
                <GateZone report={report} locked={locked} reason={reason} setReason={setReason} now={now} {...handlers} />
                <TraceCard report={report} run={report._liveTrace ? report._run : report.trace} />
              </div>
            </div>
          </React.Fragment>
        }
      </div>
    </div>);

}

Object.assign(window, { DetailPane });