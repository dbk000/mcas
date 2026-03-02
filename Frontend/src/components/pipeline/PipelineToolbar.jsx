import { useState, useEffect } from 'react'
// Panel removed — toolbar now positioned absolutely in Pipeline canvas container
import { Beaker, Layers, BookOpen, X, ArrowRight, Play, ShieldCheck, ShieldAlert, ShieldX } from 'lucide-react'
import { useApi } from '../../context/ApiContext'

// Significance badge component
function SignificanceBadge({ level, small = false }) {
  const config = {
    significant: { bg: 'bg-green-500/20', text: 'text-green-400', border: 'border-green-500/30', label: 'Significant', Icon: ShieldCheck },
    marginal: { bg: 'bg-amber-500/20', text: 'text-amber-400', border: 'border-amber-500/30', label: 'Marginal', Icon: ShieldAlert },
    not_significant: { bg: 'bg-red-500/20', text: 'text-red-400', border: 'border-red-500/30', label: 'Not Significant', Icon: ShieldX },
  }
  const c = config[level] || config.not_significant
  const size = small ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5'
  return (
    <span className={`inline-flex items-center gap-0.5 ${size} rounded border ${c.bg} ${c.text} ${c.border}`}>
      <c.Icon className={small ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
      {c.label}
    </span>
  )
}

// Validation gate checklist
function ValidationGateChecklist({ gates }) {
  const gateLabels = {
    walk_forward_pass: 'Walk-Forward',
    holdout_pass: 'Holdout',
    dsr_positive: 'DSR > 0',
    slippage_1_5x_pass: 'Slippage 1.5x',
    n_trials_corrected: 'Trials Corrected',
  }
  return (
    <div className="space-y-0.5">
      {Object.entries(gateLabels).map(([key, label]) => (
        <div key={key} className="flex items-center gap-1.5 text-[10px]">
          <span className={gates?.[key] ? 'text-green-400' : 'text-red-400'}>
            {gates?.[key] ? '\u2713' : '\u2717'}
          </span>
          <span className={gates?.[key] ? 'text-terminal-300' : 'text-terminal-500'}>{label}</span>
        </div>
      ))}
    </div>
  )
}

export default function PipelineToolbar({ topologyData, graph, onExperimentOverlay, onCompositionOverlay }) {
  const { get, post } = useApi()
  const [mode, setMode] = useState(null) // 'experiment' | 'composition' | 'compare' | 'learning'
  const [experiments, setExperiments] = useState([])
  const [compositions, setCompositions] = useState([])
  const [corrections, setCorrections] = useState([])
  const [selectedExperiment, setSelectedExperiment] = useState(null)
  const [selectedComp, setSelectedComp] = useState(null)
  const [compSignificance, setCompSignificance] = useState(null)
  const [compareA, setCompareA] = useState(null)
  const [compareB, setCompareB] = useState(null)
  const [compareData, setCompareData] = useState(null)

  // Fetch data when mode activates
  useEffect(() => {
    if (mode === 'experiment' && experiments.length === 0) {
      get('/v1/meta/experiments?limit=50').then(resp => {
        setExperiments(resp?.data?.experiments || resp?.experiments || [])
      }).catch(() => {})
    }
    if ((mode === 'composition' || mode === 'compare') && compositions.length === 0) {
      get('/v1/meta/compositions?limit=50').then(resp => {
        setCompositions(resp?.data?.compositions || resp?.compositions || [])
      }).catch(() => {})
    }
  }, [mode, get, experiments.length, compositions.length])

  const clearMode = () => {
    setMode(null)
    setSelectedExperiment(null)
    setSelectedComp(null)
    setCompareA(null)
    setCompareB(null)
    setCompareData(null)
    graph.clearOverlay()
  }

  // Select experiment → highlight its nodes
  const handleSelectExperiment = async (exp) => {
    setSelectedExperiment(exp)
    try {
      const resp = await get(`/v1/meta/experiment/${exp.experiment_id}`)
      const detail = resp?.data || resp
      const nodeIds = (detail.versions || []).map(v => v.node_id)
      onExperimentOverlay(exp.experiment_id, nodeIds)
    } catch {
      // fallback: just set overlay without nodes
      onExperimentOverlay(exp.experiment_id, [])
    }
  }

  // Select composition → highlight its nodes + fetch significance
  const handleSelectComposition = async (comp) => {
    setSelectedComp(comp)
    setCompSignificance(null)
    try {
      const [detailResp, sigResp] = await Promise.all([
        get(`/v1/meta/composition/${comp.composition_id}`),
        get(`/v1/meta/composition/${comp.composition_id}/significance`).catch(() => null),
      ])
      const detail = detailResp?.data || detailResp
      onCompositionOverlay(comp.composition_id, detail.node_versions || {})
      if (sigResp) {
        setCompSignificance(sigResp?.data || sigResp)
      }
    } catch {
      onCompositionOverlay(comp.composition_id, {})
    }
  }

  // Compare two compositions
  const handleCompare = async () => {
    if (!compareA || !compareB) return
    try {
      const resp = await get(`/v1/meta/compare/compositions/${compareA}/${compareB}`)
      setCompareData(resp?.data || resp)
      // Highlight differing nodes
      const diffNodes = Object.keys((resp?.data || resp)?.different_nodes || {})
      graph.highlightNodes(diffNodes)
    } catch { /* ignore */ }
  }

  // Deploy composition
  const handleDeploy = async (compId) => {
    if (!window.confirm('Deploy this composition? This will promote its versions to deployed status.')) return
    try {
      await post(`/v1/meta/composition/${compId}/deploy`, {})
    } catch { /* ignore */ }
  }

  return (
    <div className="absolute top-1 left-1/2 -translate-x-1/2 z-[5]">
      <div className="flex items-center gap-1.5">
        {/* Mode buttons */}
        {!mode && (
          <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg flex items-center gap-1 p-1">
            <button
              onClick={() => setMode('experiment')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 rounded transition-colors"
              title="View experiment footprints"
            >
              <Beaker className="w-3.5 h-3.5" />
              Experiments
            </button>
            <button
              onClick={() => setMode('composition')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 rounded transition-colors"
              title="View composition assemblies"
            >
              <Layers className="w-3.5 h-3.5" />
              Compositions
            </button>
            <button
              onClick={() => setMode('compare')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 rounded transition-colors"
              title="Compare two compositions"
            >
              <ArrowRight className="w-3.5 h-3.5" />
              Compare
            </button>
            <button
              onClick={() => setMode('learning')}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 rounded transition-colors"
              title="View correction markers"
            >
              <BookOpen className="w-3.5 h-3.5" />
              Corrections
            </button>
          </div>
        )}

        {/* Experiment mode */}
        {mode === 'experiment' && (
          <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg p-2 max-w-md">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-amber-400">Experiment Footprint</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            {selectedExperiment ? (
              <div className="space-y-1.5">
                <div className="text-xs text-terminal-100 font-medium">{selectedExperiment.name}</div>
                {selectedExperiment.hypothesis && (
                  <div className="text-[10px] text-terminal-400">H: {selectedExperiment.hypothesis}</div>
                )}
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                    selectedExperiment.status === 'completed' ? 'bg-green-500/20 text-green-400' :
                    selectedExperiment.status === 'running' ? 'bg-amber-500/20 text-amber-400' :
                    'bg-terminal-600 text-terminal-400'
                  }`}>{selectedExperiment.status}</span>
                  {selectedExperiment.outcome_class && (
                    <span className="text-[10px] text-terminal-400">{selectedExperiment.outcome_class}</span>
                  )}
                </div>
                <button
                  onClick={() => { setSelectedExperiment(null); graph.clearOverlay() }}
                  className="text-[10px] text-amber-400 hover:text-amber-300"
                >
                  Clear selection
                </button>
              </div>
            ) : (
              <div className="max-h-48 overflow-y-auto space-y-1">
                {experiments.length === 0 ? (
                  <div className="text-[10px] text-terminal-500 py-2 text-center">No experiments found</div>
                ) : experiments.map(exp => (
                  <button
                    key={exp.experiment_id}
                    onClick={() => handleSelectExperiment(exp)}
                    className="w-full text-left px-2 py-1.5 rounded hover:bg-terminal-700/50 transition-colors"
                  >
                    <div className="text-xs text-terminal-200 truncate">{exp.name}</div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className={`text-[10px] ${
                        exp.status === 'completed' ? 'text-green-400' :
                        exp.status === 'running' ? 'text-amber-400' : 'text-terminal-500'
                      }`}>{exp.status}</span>
                      {exp.version_count > 0 && (
                        <span className="text-[10px] text-terminal-500">{exp.version_count} versions</span>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Composition mode */}
        {mode === 'composition' && (
          <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg p-2 max-w-md">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-amber-400">Composition View</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            {selectedComp ? (
              <div className="space-y-1.5">
                <div className="text-xs text-terminal-100 font-medium">{selectedComp.name}</div>
                <div className="flex items-center gap-2 text-[10px] flex-wrap">
                  <span className={`px-1.5 py-0.5 rounded ${
                    selectedComp.status === 'deployed' ? 'bg-green-500/20 text-green-400' :
                    selectedComp.status === 'validated' ? 'bg-blue-500/20 text-blue-400' :
                    'bg-terminal-600 text-terminal-400'
                  }`}>{selectedComp.status}</span>
                  {selectedComp.agg_pf != null && (
                    <span className={selectedComp.agg_pf >= 1 ? 'text-green-400' : 'text-red-400'}>
                      PF {selectedComp.agg_pf.toFixed(2)}
                    </span>
                  )}
                  {compSignificance && (
                    <SignificanceBadge level={compSignificance.significance_level} small />
                  )}
                </div>
                {/* Statistical context summary */}
                {compSignificance?.statistical_context?.n_compositions_tested && (
                  <div className="text-[10px] text-terminal-500 space-y-0.5">
                    <div>Tested: {compSignificance.statistical_context.n_compositions_tested} compositions</div>
                    {compSignificance.statistical_context.deflated_sharpe_ratio != null && (
                      <div>DSR: {compSignificance.statistical_context.deflated_sharpe_ratio.toFixed(3)}</div>
                    )}
                    {compSignificance.statistical_context.corrected_sharpe != null && (
                      <div>Corrected Sharpe: {compSignificance.statistical_context.corrected_sharpe.toFixed(3)}</div>
                    )}
                  </div>
                )}
                {/* Validation gate checklist */}
                {compSignificance && (
                  <div className="border-t border-terminal-700 pt-1.5">
                    <div className="text-[10px] text-terminal-500 mb-1">Validation Gates</div>
                    <ValidationGateChecklist gates={compSignificance.gates_passing} />
                  </div>
                )}
                <div className="flex items-center gap-2">
                  {compSignificance?.all_gates_pass ? (
                    <button
                      onClick={() => handleDeploy(selectedComp.composition_id)}
                      className="text-[10px] px-2 py-1 bg-green-600/20 text-green-400 rounded hover:bg-green-600/30"
                    >
                      Deploy
                    </button>
                  ) : (
                    <span
                      className="text-[10px] px-2 py-1 bg-terminal-700 text-terminal-500 rounded cursor-not-allowed"
                      title={compSignificance?.failing_gates?.length
                        ? `Failing: ${compSignificance.failing_gates.join(', ')}`
                        : 'Gates not evaluated'}
                    >
                      Deploy (gates failing)
                    </span>
                  )}
                  <button
                    onClick={() => { setSelectedComp(null); setCompSignificance(null); graph.clearOverlay() }}
                    className="text-[10px] text-amber-400 hover:text-amber-300"
                  >
                    Clear
                  </button>
                </div>
              </div>
            ) : (
              <div className="max-h-48 overflow-y-auto space-y-1">
                {compositions.length === 0 ? (
                  <div className="text-[10px] text-terminal-500 py-2 text-center">No compositions found</div>
                ) : compositions.map(c => (
                  <button
                    key={c.composition_id}
                    onClick={() => handleSelectComposition(c)}
                    className="w-full text-left px-2 py-1.5 rounded hover:bg-terminal-700/50 transition-colors"
                  >
                    <div className="text-xs text-terminal-200 truncate">{c.name}</div>
                    <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                      <span className="text-[10px] text-terminal-500">{c.status}</span>
                      {c.agg_pf != null && (
                        <span className={`text-[10px] font-mono ${c.agg_pf >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                          PF {c.agg_pf.toFixed(2)}
                        </span>
                      )}
                      {c.statistical_context?.is_significant_after_correction && (
                        <SignificanceBadge level="significant" small />
                      )}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Compare mode */}
        {mode === 'compare' && (
          <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg p-2 max-w-lg">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-amber-400">Compare Compositions</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="flex items-center gap-2 mb-2">
              <select
                value={compareA || ''}
                onChange={e => setCompareA(e.target.value || null)}
                className="flex-1 text-[10px] bg-terminal-900 border border-terminal-600 rounded px-1.5 py-1 text-terminal-200"
              >
                <option value="">Composition A</option>
                {compositions.map(c => (
                  <option key={c.composition_id} value={c.composition_id}>{c.name}</option>
                ))}
              </select>
              <span className="text-terminal-500 text-[10px]">vs</span>
              <select
                value={compareB || ''}
                onChange={e => setCompareB(e.target.value || null)}
                className="flex-1 text-[10px] bg-terminal-900 border border-terminal-600 rounded px-1.5 py-1 text-terminal-200"
              >
                <option value="">Composition B</option>
                {compositions.map(c => (
                  <option key={c.composition_id} value={c.composition_id}>{c.name}</option>
                ))}
              </select>
              <button
                onClick={handleCompare}
                disabled={!compareA || !compareB || compareA === compareB}
                className="text-[10px] px-2 py-1 bg-amber-600/20 text-amber-400 rounded hover:bg-amber-600/30 disabled:opacity-30"
              >
                Diff
              </button>
            </div>
            {compareData && (
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                <div className="text-[10px] text-terminal-400">
                  {compareData.diff_count} node{compareData.diff_count !== 1 ? 's' : ''} differ
                </div>
                <div className="grid grid-cols-2 gap-2 text-[10px]">
                  <div>
                    <div className="text-terminal-500">A: {compareData.composition_a?.name}</div>
                    {compareData.composition_a?.agg_pf != null && (
                      <div className={compareData.composition_a.agg_pf >= 1 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>
                        PF {compareData.composition_a.agg_pf.toFixed(2)}
                      </div>
                    )}
                    {compareData.composition_a?.statistical_context?.deflated_sharpe_ratio != null && (
                      <div className="text-terminal-500 font-mono">
                        DSR {compareData.composition_a.statistical_context.deflated_sharpe_ratio.toFixed(3)}
                      </div>
                    )}
                  </div>
                  <div>
                    <div className="text-terminal-500">B: {compareData.composition_b?.name}</div>
                    {compareData.composition_b?.agg_pf != null && (
                      <div className={compareData.composition_b.agg_pf >= 1 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>
                        PF {compareData.composition_b.agg_pf.toFixed(2)}
                      </div>
                    )}
                    {compareData.composition_b?.statistical_context?.deflated_sharpe_ratio != null && (
                      <div className="text-terminal-500 font-mono">
                        DSR {compareData.composition_b.statistical_context.deflated_sharpe_ratio.toFixed(3)}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Learning overlay mode */}
        {mode === 'learning' && (
          <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg p-2 max-w-md">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-amber-400">Corrections Log</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="text-[10px] text-terminal-400 py-2 text-center">
              Correction markers overlay in development. See CLAUDE.md Section 5 for current corrections log.
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
