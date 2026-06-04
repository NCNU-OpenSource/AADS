/* ============================================================
   AADS Gate Console — shell chrome (topbar, rail, drawers, toasts)
   Presentational components driven by props from App.
   ============================================================ */

function Topbar({ locked, fp, search, setSearch, hours, setHours, theme, onToggleTheme, onOpenAuth, onOpenFleet, onOpenTweaks, onRefresh, refreshing }) {
  return (
    <div className="topbar">
      <div className="brand">
        <span className="brand-mark"><Icon name="command" /></span>
        <div>
          <h1>AADS Gate Console</h1>
          <div className="sub">Plan · Gate · Execution Trace</div>
        </div>
      </div>
      <div className="grow" />
      <div className="topbar-tools">
        <button className={`keychip ${locked ? "locked" : "unlocked"}`} onClick={onOpenAuth} title="Admin API key">
          <Led tone={locked ? "amber" : "green"} glow />
          <Icon name={locked ? "lock-fill" : "unlock-fill"} />
          {locked ? "locked" : `unlocked · ${fp}`}
        </button>
        <div className="field search">
          <span className="ic"><Icon name="search" /></span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="service, status, command, target" />
          {search && <button className="iconbtn" style={{ width: 22, height: 22, border: 0, background: "transparent" }} onClick={() => setSearch("")}><Icon name="x-lg" /></button>}
        </div>
        <div className="field">
          <Icon name="clock-history" className="ic" />
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={1}>1h</option>
            <option value={6}>6h</option>
            <option value={24}>24h</option>
            <option value={168}>7d</option>
          </select>
        </div>
        <button className={`iconbtn ${refreshing ? "on" : ""}`} onClick={onRefresh} title="Refresh">
          <Icon name="arrow-clockwise" className={refreshing ? "spin" : ""} />
        </button>
        <button className="iconbtn" onClick={onOpenFleet} title="Fleet & signals"><Icon name="hdd-network" /></button>
        <button className="iconbtn" onClick={onToggleTheme} title="Toggle theme"><Icon name={theme === "dark" ? "sun" : "moon-stars"} /></button>
      </div>
    </div>
  );
}

function Gauge({ label, value, note, ok }) {
  return (
    <div className="gauge">
      <div className="gl">{label}</div>
      <div className={`gv tnum ${ok ? "ok" : ""}`}>{value}</div>
      <div className="gn">{note}</div>
    </div>
  );
}
function PStage({ idx, label, n, unit, live }) {
  return (
    <div className={`pstage ${live ? "live" : ""}`}>
      <div className="pt"><span>{idx} {label}</span></div>
      <div className="pn tnum">{n} <span className="u">{unit}</span></div>
    </div>
  );
}
function InstrumentRail({ stats, counts }) {
  return (
    <div className="rail">
      <div className="gauges">
        <Gauge label="Signals" value={stats.anomalies.total} note="canonical anomalies" />
        <Gauge label="FixingPlans" value={stats.diagnoses.total} note="schema-v2 capable" />
        <Gauge label="High priority" value={counts.highPriority} note="critical + high" />
        <Gauge label="Runtime" value="OK" note={`${counts.agents} registered targets`} ok />
      </div>
      <div className="pipe">
        <PStage idx="01" label="Detect" n={stats.anomalies.total} unit="anomalies" />
        <PStage idx="02" label="RCA" n={stats.diagnoses.total} unit="plans" />
        <PStage idx="03" label="Gate" n={counts.gateWaiting} unit="waiting" />
        <PStage idx="04" label="Execute" n={counts.inflight} unit="in-flight" live={counts.inflight > 0} />
        <PStage idx="05" label="KB" n={counts.history} unit="resolved" />
      </div>
    </div>
  );
}

/* ---- Fleet & signals slide-over ---- */
function Bars({ data, accessorLabel, accessorVal, tone }) {
  const max = Math.max(...data.map((d) => Number(d.count) || 0), 1);
  return (
    <div className="col" style={{ gap: 9 }}>
      {data.map((d, i) => {
        const pct = Math.max(5, Math.round((Number(d.count) || 0) / max * 100));
        return (
          <div key={i} className="agentcard" style={{ padding: 10 }}>
            <div className="between">
              <span className="strong sm">{accessorLabel(d)}</span>
              <Pill tone={tone ? tone(d) : "grey"} mono>{accessorVal(d)}</Pill>
            </div>
            <div className="track" style={{ marginTop: 8 }}><div className="fill" style={{ width: pct + "%", background: "var(--accent)" }} /></div>
          </div>
        );
      })}
    </div>
  );
}
function FleetDrawer({ open, onClose, agents, stats }) {
  if (!open) return null;
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" style={{ width: "min(520px,100%)", maxHeight: "86vh", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
        <div className="mhead">
          <Icon name="hdd-network" /><span className="strong">Fleet &amp; signals</span>
          <div className="grow" />
          <button className="iconbtn" onClick={onClose}><Icon name="x-lg" /></button>
        </div>
        <div className="mbody">
          <div>
            <div className="sec-label">On-device agents · {agents.length}<span className="ln" /></div>
            {agents.map((a) => {
              // V2: runner_capabilities replaces the legacy catalog supported_commands.
              const caps = a.runner_capabilities || {};
              const modes = caps.modes || [];
              return (
                <div className="agentcard" key={a.node_id}>
                  <div className="between">
                    <span className="mono strong xs nowrap" style={{ maxWidth: 220 }}>{a.node_id}</span>
                    <Pill tone={a.environment === "test" ? "green" : "amber"} mono led>{a.environment}</Pill>
                  </div>
                  <div className="row tight wrap" style={{ marginTop: 8 }}>
                    <Tag k="agent">{a.agent_version}</Tag>
                    <Tag k="runner">{caps.schema_version || "runner.v1"}</Tag>
                    {caps.supports_as_root && <Tag>root</Tag>}
                    <Tag k="seen">{fmtTime(a.last_seen)}</Tag>
                  </div>
                  <div className="xs muted mono" style={{ marginTop: 7 }}>{a.base_url}</div>
                  <div className="cmd-cloud">
                    {modes.map((m) => <span className="tag" key={m}><span className="k">◇</span>{m}</span>)}
                    <span className="tag"><span className="k">hook</span>{caps.hook_default || "allow_audit"}</span>
                  </div>
                </div>
              );
            })}
          </div>
          <div>
            <div className="sec-label">Signal breakdown · where the window is noisy<span className="ln" /></div>
            <Bars data={stats.anomalies.by_container} accessorLabel={(d) => d.container} accessorVal={(d) => `${d.count} · ${Number(d.avg_score).toFixed(2)}`} tone={() => "amber"} />
          </div>
          <div>
            <div className="sec-label">Severity distribution<span className="ln" /></div>
            <Bars data={stats.diagnoses.by_severity} accessorLabel={(d) => d.severity} accessorVal={(d) => d.count} tone={(d) => severityTone(d.severity)} />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---- Auth modal (fix #5) ---- */
function AuthModal({ open, onClose, value, setValue, onTest, onUnlock, onLock, locked, fp, diag, show, setShow, onFillDemo }) {
  if (!open) return null;
  return (
    <div className="modal-scrim" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="mhead">
          <Led tone={locked ? "amber" : "green"} glow />
          <span className="strong">{locked ? "Unlock to act" : "Admin key active"}</span>
          <div className="grow" />
          <button className="iconbtn" onClick={onClose}><Icon name="x-lg" /></button>
        </div>
        <div className="mbody">
          <div className="keyfield">
            <Icon name="key" className="muted" />
            <input type={show ? "text" : "password"} value={value} onChange={(e) => setValue(e.target.value)} placeholder="paste AADS_ADMIN_API_KEY=…" autoComplete="off" />
            <button className="iconbtn" onClick={() => setShow(!show)} title={show ? "Hide" : "Show"}><Icon name={show ? "eye-slash" : "eye"} /></button>
          </div>
          <div className="diag">
            <Pill tone={diag.valid ? "green" : "grey"} mono led={diag.valid}>{diag.valid ? "valid" : "unverified"}</Pill>
            <span>key length {diag.len}</span>
            {diag.fp && <React.Fragment><span>·</span><span>fp {diag.fp}</span></React.Fragment>}
          </div>
          <div className="xs muted" style={{ lineHeight: 1.5 }}>
            <span className="fixflag" style={{ marginRight: 6 }}>FIX 5</span>
            Diagnostics describe <em>your</em> key only — its length and fingerprint. The expected key is never echoed, and its length is never revealed on failure.
          </div>
          {diag.error && (
            <div className="callout red"><Icon name="x-circle" /><div><strong>401 — key rejected.</strong> {diag.error} No hint about the expected key is shown.</div></div>
          )}
          <div className="row tight wrap" style={{ justifyContent: "space-between" }}>
            <button className="btn ghost sm" onClick={onFillDemo}><Icon name="magic" /> Fill demo key</button>
            <div className="row tight">
              {!locked && <Btn variant="danger" size="sm" icon="lock" onClick={onLock}>Lock</Btn>}
              <Btn size="sm" icon="shield-check" onClick={onTest}>Test key</Btn>
              <Btn variant="accent" size="sm" icon="unlock" onClick={onUnlock}>Unlock</Btn>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---- toasts ---- */
function Toasts({ toasts }) {
  return (
    <div className="toast-wrap">
      {toasts.map((t) => (
        <div className={`toast ${t.tone}`} key={t.id}>
          <span className="tled" />
          <div><div className="tt">{t.title}</div>{t.body && <div className="tb">{t.body}</div>}</div>
        </div>
      ))}
    </div>
  );
}

/* ---- mobile tab bar ---- */
function MobileTabbar({ tab, setTab, onOpenFleet, counts }) {
  const items = [
    { id: "queue", label: "Queue", icon: "list-check" },
    { id: "attention", label: "Attention", icon: "exclamation-diamond" },
    { id: "history", label: "History", icon: "archive" },
  ];
  return (
    <div className="mobile-tabbar">
      {items.map((it) => (
        <button key={it.id} className={tab === it.id ? "on" : ""} onClick={() => setTab(it.id)}>
          <Icon name={it.icon} className="ic" />{it.label}
        </button>
      ))}
      <button onClick={onOpenFleet}><Icon name="hdd-network" className="ic" />Fleet</button>
    </div>
  );
}

Object.assign(window, { Topbar, InstrumentRail, FleetDrawer, AuthModal, Toasts, MobileTabbar });
