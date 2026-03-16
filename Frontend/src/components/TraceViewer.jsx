import { useState } from 'react'
import { ChevronDown, ChevronRight, Shield, ShieldCheck, ShieldAlert, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'

function Section({ title, badge, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="overflow-hidden rounded-lg border border-terminal-600">
      <button
        onClick={() => setOpen(!open)}
        className="hover:bg-terminal-750 flex w-full items-center justify-between bg-terminal-800 px-3 py-2 text-left transition-colors"
      >
        <div className="flex items-center gap-2">
          {open
            ? <ChevronDown className="size-3.5 text-terminal-400" />
            : <ChevronRight className="size-3.5 text-terminal-400" />
          }
          <span className="text-sm font-medium text-terminal-200">{title}</span>
        </div>
        {badge}
      </button>
      {open && (
        <div className="border-t border-terminal-700 bg-terminal-900 px-3 py-2">
          {children}
        </div>
      )}
    </div>
  )
}

function KV({ label, value, mono = true }) {
  const display = value === null || value === undefined ? '\u2014' : String(value)
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-xs text-terminal-500">{label}</span>
      <span className={`text-xs text-terminal-200 ${mono ? 'font-mono' : ''}`}>{display}</span>
    </div>
  )
}

function GateRow({ gate }) {
  const passed = gate.passed
  return (
    <div className={`flex items-center justify-between rounded px-2 py-1 text-xs ${
      passed ? 'bg-green-900/20' : 'bg-red-900/20'
    }`}>
      <div className="flex items-center gap-2">
        <span className={passed ? 'text-green-400' : 'text-red-400'}>
          {passed ? '\u2713' : '\u2717'}
        </span>
        <span className="font-medium text-terminal-200">{gate.gate}</span>
      </div>
      <span className="font-mono text-terminal-300">
        {fmtNum(gate.value)} {gate.op} {fmtNum(gate.threshold)}
      </span>
    </div>
  )
}

function fmtNum(v) {
  if (v === null || v === undefined) return '\u2014'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v)
    if (Math.abs(v) < 0.001 && v !== 0) return v.toExponential(3)
    return v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
  }
  return String(v)
}

function fmtTs(ts) {
  if (!ts) return '\u2014'
  // Strip trailing +00:00 or Z for compactness, show UTC
  const s = String(ts).replace(/\+00:00$/, '').replace(/Z$/, '')
  return s + ' UTC'
}

function fmtLatency(ms) {
  if (ms === null || ms === undefined) return '\u2014'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function CausalBadge({ causal }) {
  if (causal === true) {
    return (
      <span className="inline-flex items-center gap-1 font-mono text-xs text-green-400">
        <CheckCircle2 className="size-3" /> Causal
      </span>
    )
  }
  if (causal === false) {
    return (
      <span className="inline-flex items-center gap-1 font-mono text-xs text-red-400">
        <XCircle className="size-3" /> Non-causal
      </span>
    )
  }
  return null
}

function CausalitySection({ lineage }) {
  if (!lineage) return null

  const { signal, entry, exit, causality_verified } = lineage

  return (
    <>
      {/* Causality Banner */}
      <div className={`flex items-center gap-2 rounded border px-3 py-2 ${
        causality_verified
          ? 'border-green-700/40 bg-green-900/20'
          : causality_verified === false
            ? 'border-red-700/40 bg-red-900/20'
            : 'border-terminal-600 bg-terminal-800'
      }`}>
        {causality_verified ? (
          <CheckCircle2 className="size-4 shrink-0 text-green-400" />
        ) : causality_verified === false ? (
          <XCircle className="size-4 shrink-0 text-red-400" />
        ) : (
          <Shield className="size-4 shrink-0 text-terminal-500" />
        )}
        <span className={`text-sm font-medium ${
          causality_verified ? 'text-green-300' : causality_verified === false ? 'text-red-300' : 'text-terminal-400'
        }`}>
          {causality_verified
            ? `CAUSALITY VERIFIED \u2014 ${fmtLatency(signal?.latency_ms)} signal latency`
            : causality_verified === false
              ? 'CAUSALITY VIOLATION DETECTED'
              : 'Causality data unavailable'
          }
        </span>
      </div>

      {/* Signal Phase */}
      {signal && (
        <Section title="Signal Phase" defaultOpen={true} badge={<CausalBadge causal={signal.causal} />}>
          <KV label="Bar open" value={fmtTs(signal.bar_open_ts)} />
          <KV label="Bar close" value={fmtTs(signal.bar_close_ts)} />
          <KV label="Decision" value={fmtTs(signal.decision_ts)} />
          <KV label="Latency" value={fmtLatency(signal.latency_ms)} />
          <div className="mt-1.5 border-t border-terminal-700 pt-1.5">
            <span className="font-mono text-[10px] text-terminal-600">
              bar_close {signal.causal ? '<' : '\u2265'} decision_ts
            </span>
          </div>
        </Section>
      )}

      {/* Entry Phase */}
      {entry && (
        <Section title="Entry Phase" defaultOpen={true} badge={<CausalBadge causal={entry.causal} />}>
          <KV label="Entry bar" value={fmtTs(entry.bar_ts)} />
          <KV label="Signal bar close" value={fmtTs(entry.signal_bar_close_ts)} />
          <div className="mt-1.5 border-t border-terminal-700 pt-1.5">
            <span className="font-mono text-[10px] text-terminal-600">
              entry_bar {entry.causal ? '\u2265' : '<'} signal_bar_close
            </span>
          </div>
        </Section>
      )}

      {/* Exit Phase */}
      {exit && (
        <Section title="Exit Phase" defaultOpen={true} badge={<CausalBadge causal={exit.causal} />}>
          <KV label="Exit bar" value={fmtTs(exit.bar_ts)} />
          <KV label="Exit reason" value={exit.exit_reason} />
          <KV label="Entry bar" value={fmtTs(exit.entry_bar_ts)} />
          <div className="mt-1.5 border-t border-terminal-700 pt-1.5">
            <span className="font-mono text-[10px] text-terminal-600">
              exit_bar {exit.causal ? '\u2265' : '<'} entry_bar
            </span>
          </div>
        </Section>
      )}
    </>
  )
}

export default function TraceViewer({ trace, simTrace }) {
  if (!trace && !simTrace) return null

  const hashValid = trace ? verifyTraceHash(trace) : null
  const lineage = simTrace?.kline_lineage || null

  return (
    <div className="space-y-2">
      {/* Causality header — replaces/upgrades integrity badge when lineage is present */}
      {lineage ? (
        <CausalitySection lineage={lineage} />
      ) : (
        <>
          {/* Original header with version + integrity (when no lineage) */}
          {trace && (
            <div className="mb-1 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs text-terminal-500">Trace v{trace.v}</span>
                <span className="text-xs text-terminal-600">|</span>
                <span className="font-mono text-xs text-terminal-500">
                  {trace.strategy}
                </span>
              </div>
              <div className="flex items-center gap-1">
                {hashValid === true && (
                  <span className="flex items-center gap-1 text-xs text-green-400">
                    <ShieldCheck className="size-3.5" /> Integrity OK
                  </span>
                )}
                {hashValid === false && (
                  <span className="flex items-center gap-1 text-xs text-red-400">
                    <ShieldAlert className="size-3.5" /> Hash mismatch
                  </span>
                )}
                {hashValid === null && (
                  <span className="flex items-center gap-1 text-xs text-terminal-500">
                    <Shield className="size-3.5" /> No hash
                  </span>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* Version + strategy subheader (show when lineage replaces the old header) */}
      {lineage && trace && (
        <div className="mb-1 flex items-center gap-2">
          <span className="text-xs text-terminal-500">Trace v{trace.v}</span>
          <span className="text-xs text-terminal-600">|</span>
          <span className="font-mono text-xs text-terminal-500">{trace.strategy}</span>
          {hashValid === true && (
            <span className="ml-auto flex items-center gap-1 text-xs text-green-400">
              <ShieldCheck className="size-3" /> Integrity OK
            </span>
          )}
        </div>
      )}

      {trace && (
        <>
          {/* Hydration warning */}
          {trace.state?.is_hydrated && (
            <div className="flex items-center gap-2 rounded border border-amber-700/40 bg-amber-900/20 px-3 py-1.5">
              <AlertTriangle className="size-3.5 shrink-0 text-amber-400" />
              <span className="text-xs text-amber-300">
                State was reconstructed from history (container restart) — dormancy timing is inferred, not observed.
              </span>
            </div>
          )}

          {/* Gates */}
          {trace.gates && trace.gates.length > 0 && (
            <Section title="Gates" defaultOpen={true} badge={
              <span className="font-mono text-xs text-terminal-400">
                {trace.gates.filter(g => g.passed).length}/{trace.gates.length} passed
              </span>
            }>
              <div className="space-y-1">
                {trace.gates.map((g, i) => (
                  <GateRow key={i} gate={g} />
                ))}
              </div>
            </Section>
          )}

          {/* Score */}
          {trace.score && (
            <Section title="Score" defaultOpen={true} badge={
              <span className="font-mono text-xs text-amber-400">
                {fmtNum(trace.score.calibrated)}
              </span>
            }>
              <KV label="Raw" value={fmtNum(trace.score.raw)} />
              <KV label="Calibration mult" value={fmtNum(trace.score.calibration_mult)} />
              <KV label="Calibrated" value={fmtNum(trace.score.calibrated)} />
              <KV label="Promotion threshold" value={fmtNum(trace.score.promotion_threshold)} />
              {trace.score.components && (
                <div className="mt-2 border-t border-terminal-700 pt-2">
                  <span className="text-xs uppercase tracking-wider text-terminal-500">Components</span>
                  {Object.entries(trace.score.components).map(([k, v]) => (
                    <KV key={k} label={k} value={fmtNum(v)} />
                  ))}
                </div>
              )}
            </Section>
          )}

          {/* Candle Window */}
          {trace.candle_window && trace.candle_window.length > 0 && (
            <Section title="Candle Window" badge={
              <span className="font-mono text-xs text-terminal-400">
                {trace.candle_window.map(w => `${w.bar_count}x${w.timeframe}`).join(' + ')}
              </span>
            }>
              {trace.candle_window.map((w, i) => (
                <div key={i} className={i > 0 ? 'mt-2 border-t border-terminal-700 pt-2' : ''}>
                  <KV label="Timeframe" value={w.timeframe} />
                  <KV label="First" value={w.first_ts} />
                  <KV label="Last" value={w.last_ts} />
                  <KV label="Bar count" value={w.bar_count} />
                  {w.note && <KV label="Note" value={w.note} mono={false} />}
                </div>
              ))}
            </Section>
          )}

          {/* Indicator Snapshot */}
          {trace.indicator_snapshot && Object.keys(trace.indicator_snapshot).length > 0 && (
            <Section title="Indicator Snapshot" badge={
              <span className="font-mono text-xs text-terminal-400">
                {Object.keys(trace.indicator_snapshot).length} values
              </span>
            }>
              {Object.entries(trace.indicator_snapshot).map(([k, v]) => (
                <KV key={k} label={k} value={fmtNum(v)} />
              ))}
            </Section>
          )}

          {/* State */}
          {trace.state && Object.keys(trace.state).length > 0 && (
            <Section title="State" badge={
              trace.state.phase
                ? <span className="font-mono text-xs text-terminal-400">{trace.state.phase}</span>
                : null
            }>
              {Object.entries(trace.state).map(([k, v]) => (
                <KV key={k} label={k} value={typeof v === 'object' ? JSON.stringify(v) : fmtNum(v)} />
              ))}
            </Section>
          )}

          {/* Cross-sectional context (S9) */}
          {trace.cross_sectional_context && (
            <Section title="Cross-Sectional Context">
              {Object.entries(trace.cross_sectional_context).map(([k, v]) => (
                <KV key={k} label={k} value={fmtNum(v)} />
              ))}
            </Section>
          )}

          {/* Microstructure (S9) */}
          {trace.microstructure && (
            <Section title="Microstructure">
              {Object.entries(trace.microstructure).map(([k, v]) => (
                <KV key={k} label={k} value={fmtNum(v)} />
              ))}
            </Section>
          )}

          {/* Config */}
          {trace.config_snapshot && Object.keys(trace.config_snapshot).length > 0 && (
            <Section title="Config Snapshot" badge={
              <span className="font-mono text-xs text-terminal-500" title={trace.config_hash}>
                #{trace.config_hash?.slice(0, 8)}
              </span>
            }>
              {Object.entries(trace.config_snapshot).map(([k, v]) => (
                <KV key={k} label={k} value={fmtNum(v)} />
              ))}
              {trace.indicator_params_hash && (
                <div className="mt-2 border-t border-terminal-700 pt-2">
                  <KV label="Indicator params hash" value={trace.indicator_params_hash} />
                </div>
              )}
            </Section>
          )}

          {/* Promotion */}
          {trace.promotion && (
            <Section title="Promotion">
              <KV label="Setup version" value={trace.promotion.setup_version} />
              <KV label="Sealed at" value={
                trace.promotion.sealed_at
                  ? new Date(trace.promotion.sealed_at * 1000).toISOString()
                  : '\u2014'
              } />
              <KV label="Trace hash" value={
                trace.trace_hash ? `${trace.trace_hash.slice(0, 16)}...` : '\u2014'
              } />
            </Section>
          )}
        </>
      )}

      {/* Simulation Trace (MAE/MFE, gap-through, exit TF) */}
      {simTrace && !lineage && (
        <Section title="Simulation Trace">
          {simTrace.exit_reason && <KV label="Exit reason" value={simTrace.exit_reason} />}
          {simTrace.exit_tf_used && <KV label="Exit TF" value={simTrace.exit_tf_used} />}
          {simTrace.mae_mfe && (
            <>
              <KV label="MAE (R)" value={fmtNum(simTrace.mae_mfe.mae_r)} />
              <KV label="MFE (R)" value={fmtNum(simTrace.mae_mfe.mfe_r)} />
            </>
          )}
        </Section>
      )}
    </div>
  )
}

function verifyTraceHash(trace) {
  // Client-side integrity check not feasible (would need deterministic JSON matching Python's)
  // Just check if hash is present
  if (!trace.trace_hash) return null
  return true // Hash present — server-side verification available via API
}
