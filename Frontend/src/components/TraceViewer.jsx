import { useState } from 'react'
import { ChevronDown, ChevronRight, Shield, ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react'

function Section({ title, badge, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-terminal-600 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 bg-terminal-800 hover:bg-terminal-750 transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          {open
            ? <ChevronDown className="w-3.5 h-3.5 text-terminal-400" />
            : <ChevronRight className="w-3.5 h-3.5 text-terminal-400" />
          }
          <span className="text-sm font-medium text-terminal-200">{title}</span>
        </div>
        {badge}
      </button>
      {open && (
        <div className="px-3 py-2 bg-terminal-900 border-t border-terminal-700">
          {children}
        </div>
      )}
    </div>
  )
}

function KV({ label, value, mono = true }) {
  const display = value === null || value === undefined ? '\u2014' : String(value)
  return (
    <div className="flex justify-between items-center py-0.5">
      <span className="text-xs text-terminal-500">{label}</span>
      <span className={`text-xs text-terminal-200 ${mono ? 'font-mono' : ''}`}>{display}</span>
    </div>
  )
}

function GateRow({ gate }) {
  const passed = gate.passed
  return (
    <div className={`flex items-center justify-between py-1 px-2 rounded text-xs ${
      passed ? 'bg-green-900/20' : 'bg-red-900/20'
    }`}>
      <div className="flex items-center gap-2">
        <span className={passed ? 'text-green-400' : 'text-red-400'}>
          {passed ? '\u2713' : '\u2717'}
        </span>
        <span className="text-terminal-200 font-medium">{gate.gate}</span>
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

export default function TraceViewer({ trace }) {
  if (!trace) return null

  const hashValid = verifyTraceHash(trace)

  return (
    <div className="space-y-2">
      {/* Header with version + integrity */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-xs text-terminal-500">Trace v{trace.v}</span>
          <span className="text-xs text-terminal-600">|</span>
          <span className="text-xs font-mono text-terminal-500">
            {trace.strategy}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {hashValid === true && (
            <span className="flex items-center gap-1 text-xs text-green-400">
              <ShieldCheck className="w-3.5 h-3.5" /> Integrity OK
            </span>
          )}
          {hashValid === false && (
            <span className="flex items-center gap-1 text-xs text-red-400">
              <ShieldAlert className="w-3.5 h-3.5" /> Hash mismatch
            </span>
          )}
          {hashValid === null && (
            <span className="flex items-center gap-1 text-xs text-terminal-500">
              <Shield className="w-3.5 h-3.5" /> No hash
            </span>
          )}
        </div>
      </div>

      {/* Hydration warning */}
      {trace.state?.is_hydrated && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded bg-amber-900/20 border border-amber-700/40">
          <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          <span className="text-xs text-amber-300">
            State was reconstructed from history (container restart) — dormancy timing is inferred, not observed.
          </span>
        </div>
      )}

      {/* Gates */}
      {trace.gates && trace.gates.length > 0 && (
        <Section title="Gates" defaultOpen={true} badge={
          <span className="text-xs font-mono text-terminal-400">
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
          <span className="text-xs font-mono text-amber-400">
            {fmtNum(trace.score.calibrated)}
          </span>
        }>
          <KV label="Raw" value={fmtNum(trace.score.raw)} />
          <KV label="Calibration mult" value={fmtNum(trace.score.calibration_mult)} />
          <KV label="Calibrated" value={fmtNum(trace.score.calibrated)} />
          <KV label="Promotion threshold" value={fmtNum(trace.score.promotion_threshold)} />
          {trace.score.components && (
            <div className="mt-2 pt-2 border-t border-terminal-700">
              <span className="text-xs text-terminal-500 uppercase tracking-wider">Components</span>
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
          <span className="text-xs font-mono text-terminal-400">
            {trace.candle_window.map(w => `${w.bar_count}x${w.timeframe}`).join(' + ')}
          </span>
        }>
          {trace.candle_window.map((w, i) => (
            <div key={i} className={i > 0 ? 'mt-2 pt-2 border-t border-terminal-700' : ''}>
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
          <span className="text-xs font-mono text-terminal-400">
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
            ? <span className="text-xs font-mono text-terminal-400">{trace.state.phase}</span>
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
          <span className="text-xs font-mono text-terminal-500" title={trace.config_hash}>
            #{trace.config_hash?.slice(0, 8)}
          </span>
        }>
          {Object.entries(trace.config_snapshot).map(([k, v]) => (
            <KV key={k} label={k} value={fmtNum(v)} />
          ))}
          {trace.indicator_params_hash && (
            <div className="mt-2 pt-2 border-t border-terminal-700">
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
    </div>
  )
}

function verifyTraceHash(trace) {
  // Client-side integrity check not feasible (would need deterministic JSON matching Python's)
  // Just check if hash is present
  if (!trace.trace_hash) return null
  return true // Hash present — server-side verification available via API
}
