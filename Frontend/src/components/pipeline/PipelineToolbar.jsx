import { useState, useEffect } from 'react'
// Panel removed — toolbar now positioned absolutely in Pipeline canvas container
import { Beaker, Layers, BookOpen, X, ArrowRight, ShieldCheck, ShieldAlert, ShieldX, PlusCircle } from 'lucide-react'
import { useApi } from '../../context/ApiContext'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'
import { FeedbackOnset } from '../FeedbackOnset'

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
      <c.Icon className={small ? 'size-2.5' : 'size-3'} />
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

export default function PipelineToolbar({ topologyData: _topologyData, graph, onExperimentOverlay, onCompositionOverlay }) {
  const { get, post } = useApi()
  const [mode, setMode] = useState(null) // 'experiment' | 'composition' | 'compare' | 'learning'
  const [experiments, setExperiments] = useState([])
  const [compositions, setCompositions] = useState([])
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
    <div className="absolute left-1/2 top-1 z-[5] -translate-x-1/2">
      <div className="flex items-center gap-1.5">
        {/* Mode buttons */}
        {!mode && (
          <div className="flex items-center gap-1 rounded-lg border border-terminal-600 bg-terminal-800/90 p-1 backdrop-blur">
            <button
              onClick={() => setMode('experiment')}
              className="flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs text-terminal-300 transition-colors hover:bg-terminal-700 hover:text-terminal-100"
              title="View experiment footprints"
            >
              <Beaker className="size-3.5" />
              Experiments
            </button>
            <button
              onClick={() => setMode('composition')}
              className="flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs text-terminal-300 transition-colors hover:bg-terminal-700 hover:text-terminal-100"
              title="View composition assemblies"
            >
              <Layers className="size-3.5" />
              Compositions
            </button>
            <button
              onClick={() => setMode('compare')}
              className="flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs text-terminal-300 transition-colors hover:bg-terminal-700 hover:text-terminal-100"
              title="Compare two compositions"
            >
              <ArrowRight className="size-3.5" />
              Compare
            </button>
            <button
              onClick={() => setMode('learning')}
              className="flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs text-terminal-300 transition-colors hover:bg-terminal-700 hover:text-terminal-100"
              title="View correction markers"
            >
              <BookOpen className="size-3.5" />
              Corrections
            </button>
            {PIPELINE_FEATURES.compositionDraftUI && (
              <button
                onClick={() => setMode('draft')}
                className="flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs text-terminal-300 transition-colors hover:bg-terminal-700 hover:text-terminal-100"
                title="Create new composition from selected versions"
              >
                <PlusCircle className="size-3.5" />
                New Comp
              </button>
            )}
          </div>
        )}

        {/* Experiment mode */}
        {mode === 'experiment' && (
          <div className="max-w-md rounded-lg border border-terminal-600 bg-terminal-800/90 p-2 backdrop-blur">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-amber-400">Experiment Footprint</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="size-3.5" />
              </button>
            </div>
            {selectedExperiment ? (
              <div className="space-y-1.5">
                <div className="text-xs font-medium text-terminal-100">{selectedExperiment.name}</div>
                {selectedExperiment.hypothesis && (
                  <div className="text-[10px] text-terminal-400">H: {selectedExperiment.hypothesis}</div>
                )}
                <div className="flex items-center gap-2">
                  <span className={`rounded px-1.5 py-0.5 text-[10px] ${
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
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {experiments.length === 0 ? (
                  <div className="py-2 text-center text-[10px] text-terminal-500">No experiments found</div>
                ) : experiments.map(exp => (
                  <button
                    key={exp.experiment_id}
                    onClick={() => handleSelectExperiment(exp)}
                    className="w-full rounded px-2 py-1.5 text-left transition-colors hover:bg-terminal-700/50"
                  >
                    <div className="truncate text-xs text-terminal-200">{exp.name}</div>
                    <div className="mt-0.5 flex items-center gap-2">
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
          <div className="max-w-md rounded-lg border border-terminal-600 bg-terminal-800/90 p-2 backdrop-blur">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-amber-400">Composition View</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="size-3.5" />
              </button>
            </div>
            {selectedComp ? (
              <div className="space-y-1.5">
                <div className="text-xs font-medium text-terminal-100">{selectedComp.name}</div>
                <div className="flex flex-wrap items-center gap-2 text-[10px]">
                  <span className={`rounded px-1.5 py-0.5 ${
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
                  <div className="space-y-0.5 text-[10px] text-terminal-500">
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
                    <div className="mb-1 text-[10px] text-terminal-500">Validation Gates</div>
                    <ValidationGateChecklist gates={compSignificance.gates_passing} />
                  </div>
                )}
                <div className="flex items-center gap-2">
                  {compSignificance?.all_gates_pass ? (
                    <FeedbackOnset 
                      onClick={() => handleDeploy(selectedComp.composition_id)}
                      latencyLabel="Deploy"
                    >
                      <button
                        className="rounded bg-green-600/20 px-2 py-1 text-[10px] text-green-400 hover:bg-green-600/30"
                      >
                        Deploy
                      </button>
                    </FeedbackOnset>
                  ) : (
                    <span
                      className="cursor-not-allowed rounded bg-terminal-700 px-2 py-1 text-[10px] text-terminal-500"
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
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {compositions.length === 0 ? (
                  <div className="py-2 text-center text-[10px] text-terminal-500">No compositions found</div>
                ) : compositions.map(c => (
                  <button
                    key={c.composition_id}
                    onClick={() => handleSelectComposition(c)}
                    className="w-full rounded px-2 py-1.5 text-left transition-colors hover:bg-terminal-700/50"
                  >
                    <div className="truncate text-xs text-terminal-200">{c.name}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-2">
                      <span className="text-[10px] text-terminal-500">{c.status}</span>
                      {c.agg_pf != null && (
                        <span className={`font-mono text-[10px] ${c.agg_pf >= 1 ? 'text-green-400' : 'text-red-400'}`}>
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
          <div className="max-w-lg rounded-lg border border-terminal-600 bg-terminal-800/90 p-2 backdrop-blur">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-amber-400">Compare Compositions</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="size-3.5" />
              </button>
            </div>
            <div className="mb-2 flex items-center gap-2">
              <select
                value={compareA || ''}
                onChange={e => setCompareA(e.target.value || null)}
                className="flex-1 rounded border border-terminal-600 bg-terminal-900 px-1.5 py-1 text-[10px] text-terminal-200"
              >
                <option value="">Composition A</option>
                {compositions.map(c => (
                  <option key={c.composition_id} value={c.composition_id}>{c.name}</option>
                ))}
              </select>
              <span className="text-[10px] text-terminal-500">vs</span>
              <select
                value={compareB || ''}
                onChange={e => setCompareB(e.target.value || null)}
                className="flex-1 rounded border border-terminal-600 bg-terminal-900 px-1.5 py-1 text-[10px] text-terminal-200"
              >
                <option value="">Composition B</option>
                {compositions.map(c => (
                  <option key={c.composition_id} value={c.composition_id}>{c.name}</option>
                ))}
              </select>
              <FeedbackOnset 
                onClick={handleCompare}
                latencyLabel="Diff"
              >
                <button
                  disabled={!compareA || !compareB || compareA === compareB}
                  className="rounded bg-amber-600/20 px-2 py-1 text-[10px] text-amber-400 hover:bg-amber-600/30 disabled:opacity-30"
                >
                  Diff
                </button>
              </FeedbackOnset>
            </div>
            {compareData && (
              <div className="max-h-48 space-y-1.5 overflow-y-auto">
                <div className="text-[10px] text-terminal-400">
                  {compareData.diff_count} node{compareData.diff_count !== 1 ? 's' : ''} differ
                </div>
                <div className="grid grid-cols-2 gap-2 text-[10px]">
                  <div>
                    <div className="text-terminal-500">A: {compareData.composition_a?.name}</div>
                    {compareData.composition_a?.agg_pf != null && (
                      <div className={compareData.composition_a.agg_pf >= 1 ? 'font-mono text-green-400' : 'font-mono text-red-400'}>
                        PF {compareData.composition_a.agg_pf.toFixed(2)}
                      </div>
                    )}
                    {compareData.composition_a?.statistical_context?.deflated_sharpe_ratio != null && (
                      <div className="font-mono text-terminal-500">
                        DSR {compareData.composition_a.statistical_context.deflated_sharpe_ratio.toFixed(3)}
                      </div>
                    )}
                  </div>
                  <div>
                    <div className="text-terminal-500">B: {compareData.composition_b?.name}</div>
                    {compareData.composition_b?.agg_pf != null && (
                      <div className={compareData.composition_b.agg_pf >= 1 ? 'font-mono text-green-400' : 'font-mono text-red-400'}>
                        PF {compareData.composition_b.agg_pf.toFixed(2)}
                      </div>
                    )}
                    {compareData.composition_b?.statistical_context?.deflated_sharpe_ratio != null && (
                      <div className="font-mono text-terminal-500">
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
          <div className="max-w-md rounded-lg border border-terminal-600 bg-terminal-800/90 p-2 backdrop-blur">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium text-amber-400">Corrections Log</span>
              <button onClick={clearMode} className="text-terminal-500 hover:text-terminal-100">
                <X className="size-3.5" />
              </button>
            </div>
            <div className="py-2 text-center text-[10px] text-terminal-400">
              Correction markers overlay in development. See CLAUDE.md Section 5 for current corrections log.
            </div>
          </div>
        )}

        {/* Composition draft mode (Wave 4.9) */}
        {mode === 'draft' && (
          <CompositionDraftPanel
            graph={graph}
            onClose={clearMode}
          />
        )}
      </div>
    </div>
  )
}

// Wave 4.9: Composition draft lifecycle panel
function CompositionDraftPanel({ graph, onClose }) {
  const { post } = useApi()
  const [draftName, setDraftName] = useState('')
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState(null)

  const draftEntries = [...graph.compositionDraft.entries()]
  const nodeMap = graph.nodeMap || {}

  const handleSave = async () => {
    if (!draftName.trim() || draftEntries.length === 0) return
    setSaving(true)
    try {
      const nodeVersions = {}
      for (const [nodeId, versionId] of draftEntries) {
        nodeVersions[nodeId] = versionId
      }
      const resp = await post('/v1/meta/composition/draft', {
        name: draftName,
        node_versions: nodeVersions,
      })
      setResult(resp?.data || resp)
    } catch (err) {
      setResult({ error: err.message })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-md rounded-lg border border-terminal-600 bg-terminal-800/90 p-2 backdrop-blur">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-amber-400">
          New Composition Draft
          {draftEntries.length > 0 && (
            <span className="ml-1 text-terminal-500">({draftEntries.length} nodes)</span>
          )}
        </span>
        <button onClick={onClose} className="text-terminal-500 hover:text-terminal-100">
          <X className="size-3.5" />
        </button>
      </div>

      {result ? (
        <div className="space-y-1.5">
          {result.error ? (
            <div className="text-[10px] text-red-400">{result.error}</div>
          ) : result.action === 'duplicate' ? (
            <div className="text-[10px] text-amber-400">{`Duplicate: matches existing composition "${result.existing_name}"`}</div>
          ) : (
            <div className="text-[10px] text-green-400">
              Created composition with {result.node_count} nodes
            </div>
          )}
          <button
            onClick={() => { setResult(null); graph.clearDraft() }}
            className="text-[10px] text-amber-400 hover:text-amber-300"
          >
            Start new draft
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-[10px] text-terminal-400">
            Click nodes on the canvas to add them to the draft.
            Each node will use its current deployed version.
          </div>

          {draftEntries.length > 0 && (
            <div className="max-h-32 space-y-0.5 overflow-y-auto">
              {draftEntries.map(([nodeId]) => {
                const node = nodeMap[nodeId]
                return (
                  <div key={nodeId} className="flex items-center justify-between rounded bg-terminal-900 px-1.5 py-0.5 text-[10px]">
                    <span className="truncate text-terminal-200">{node?.name || nodeId.slice(0, 8)}</span>
                    <button
                      onClick={() => graph.setVersionForNode(nodeId, null)}
                      className="ml-1 text-terminal-500 hover:text-red-400"
                    >
                      ✕
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          <input
            type="text"
            value={draftName}
            onChange={e => setDraftName(e.target.value)}
            placeholder="Composition name..."
            className="w-full rounded border border-terminal-600 bg-terminal-900 px-2 py-1 text-[10px] text-terminal-200 placeholder:text-terminal-600 focus:border-amber-500/50 focus:outline-none"
          />

          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={saving || !draftName.trim() || draftEntries.length === 0}
              className="rounded bg-green-600/20 px-2 py-1 text-[10px] text-green-400 hover:bg-green-600/30 disabled:opacity-30"
            >
              {saving ? 'Saving...' : 'Save Draft'}
            </button>
            <button
              onClick={() => graph.clearDraft()}
              className="text-[10px] text-terminal-500 hover:text-terminal-300"
            >
              Clear all
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
