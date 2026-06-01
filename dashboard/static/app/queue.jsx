/* ============================================================
   AADS Gate Console — left queue pane (Direction B list)
   Reads decorated reports: report._status (effective), report._run
   ============================================================ */
const { useMemo } = React;

const TABS = [
  { id: "queue", label: "Queue", icon: "list-check" },
  { id: "attention", label: "Attention", icon: "exclamation-diamond" },
  { id: "history", label: "History", icon: "archive" },
];

const QUEUE_BUCKETS = [
  { id: "awaiting", label: "Awaiting approval", tone: "amber" },
  { id: "approved", label: "Approved · ready", tone: "blue" },
  { id: "inflight", label: "In-flight", tone: "blue" },
];
const ATTN_BUCKETS = [
  { id: "attention", label: "Needs attention", tone: "red" },
];

function rowContext(report, now) {
  const st = report._status;
  if (st === "approved" && report.approval) {
    const ms = new Date(report.approval.approved_until).getTime() - now;
    return { tone: ms < 5 * 60000 ? "amber" : "blue", text: `expires ${fmtClock(ms)}` };
  }
  if (st === "failed_retryable" && report.retry) {
    const ms = new Date(report.retry.retry_eligible_at).getTime() - now;
    return ms > 0
      ? { tone: "amber", text: `retry in ${fmtClock(ms)}` }
      : { tone: "green", text: "retry ready" };
  }
  if (INFLIGHT.has(st) && report._run) {
    return { tone: "blue", text: `step ${report._run.stepIndex + 1}/${report._run.total}` };
  }
  if (st === "execution_failed" || st === "blocked") {
    return report.rolled_back ? { tone: "green", text: "rolled back" } : { tone: "red", text: "failed" };
  }
  if (st === "kb_import_failed") return { tone: "amber", text: "KB warn" };
  if (st === "needs_triage") return { tone: "grey", text: "schema 1.0" };
  return null;
}

function QueueRow({ report, selected, onSelect, now }) {
  const m = statusMeta(report._status);
  const root = report.root_cause || {};
  const policy = (report.action_plan || {}).environment_policy || {};
  const ctx = rowContext(report, now);
  return (
    <div className={`qrow ${m.tone} ${selected ? "sel" : ""}`} onClick={() => onSelect(report.diagnosis_id)}>
      <div className="edge" />
      <div className="qb">
        <div className="qtitle">{report.summary}</div>
        <div className="qmeta">
          <SevPill severity={report.severity} />
          <StatusPill status={report._status} />
          {ctx && <Pill tone={ctx.tone} mono led={ctx.tone === "blue" || ctx.tone === "amber"} pulse={INFLIGHT.has(report._status)}>{ctx.text}</Pill>}
        </div>
        <div className="qfoot">
          <span>{root.affected_service || "service"}</span>
          <span className="dot" />
          <span>{shortId(report.action_plan?.target_node_id || root.target_node_id || "n/a")}</span>
          <span className="dot" />
          <span>{policy.environment || "—"}</span>
        </div>
      </div>
    </div>
  );
}

function Bucket({ def, items, selectedId, onSelect, now }) {
  if (!items.length) return null;
  return (
    <React.Fragment>
      <div className="bucket">
        <Led tone={def.tone} />
        <span>{def.label}</span>
        <span className="ct">{items.length}</span>
        <span className="ln" />
      </div>
      {items.map((r) => (
        <QueueRow key={r.diagnosis_id} report={r} selected={r.diagnosis_id === selectedId} onSelect={onSelect} now={now} />
      ))}
    </React.Fragment>
  );
}

function QueuePane({ reports, tab, setTab, selectedId, onSelect, counts, now }) {
  const inTab = useMemo(() => {
    if (tab === "history") return reports.filter((r) => bucketFromStatus(r._status) === "history");
    if (tab === "attention") return reports.filter((r) => bucketFromStatus(r._status) === "attention");
    return reports.filter((r) => ["awaiting", "approved", "inflight"].includes(bucketFromStatus(r._status)));
  }, [reports, tab]);

  const buckets = tab === "attention" ? ATTN_BUCKETS : QUEUE_BUCKETS;

  return (
    <div className="pane list">
      <div className="list-head">
        <div className="seg">
          {TABS.map((t) => (
            <button key={t.id} className={`${tab === t.id ? "on" : ""} ${t.id === "attention" && counts.attention ? "alert" : ""}`} onClick={() => setTab(t.id)}>
              <Icon name={t.icon} />{t.label}<span className="c">{counts[t.id]}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="qlist">
        {tab === "history" ? (
          <HistoryList items={inTab} selectedId={selectedId} onSelect={onSelect} now={now} />
        ) : (
          buckets.map((b) => (
            <Bucket
              key={b.id}
              def={b}
              items={inTab.filter((r) => bucketFromStatus(r._status) === b.id)}
              selectedId={selectedId}
              onSelect={onSelect}
              now={now}
            />
          ))
        )}
        {inTab.length === 0 && (
          <div className="empty-detail" style={{ height: "auto", padding: "40px 16px" }}>
            <div>
              <Icon name="inbox" className="" />
              <div style={{ marginTop: 8 }}>Nothing in this view.</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function HistoryList({ items, selectedId, onSelect, now }) {
  if (!items.length) return null;
  return (
    <React.Fragment>
      <div className="bucket"><Led tone="green" /><span>Resolved repairs</span><span className="ct">{items.length}</span><span className="ln" /></div>
      {items.map((r) => (
        <QueueRow key={r.diagnosis_id} report={r} selected={r.diagnosis_id === selectedId} onSelect={onSelect} now={now} />
      ))}
    </React.Fragment>
  );
}

function bucketFromStatus(status) { return statusMeta(status).bucket; }

Object.assign(window, { QueuePane, TABS });
