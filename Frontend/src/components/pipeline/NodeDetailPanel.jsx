import { useState, useEffect } from 'react'
import { X, Check, Clipboard } from 'lucide-react'
import { useApi } from '../../context/ApiContext'
import VersionsTab from './VersionsTab'
import BiographyTab from './BiographyTab'
import ParamsTab from './ParamsTab'
import ContextTab from './ContextTab'
import { buildNodeContextMarkdown, getCurrentNodeVersion } from './nodeDetailUtils'

const STATUS_COLORS = {
  deployed: 'bg-green-500/20 text-green-400 border-green-500/30',
  promising: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  tested: 'bg-terminal-600 text-terminal-300 border-terminal-500',
  baseline: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  retired: 'bg-red-500/20 text-red-400 border-red-500/30',
}

export default function NodeDetailPanel({ node, onClose, graph }) {
  const { get, post } = useApi()
  const [versions, setVersions] = useState([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [showFrontierOnly, setShowFrontierOnly] = useState(false)
  const [expandedVersion, setExpandedVersion] = useState(null)
  const [activeTab, setActiveTab] = useState('versions')
  const [selectedVersionForBio, setSelectedVersionForBio] = useState(null)
  const [diffVersionA, setDiffVersionA] = useState(null)
  const [diffVersionB, setDiffVersionB] = useState(null)
  const [diffData, setDiffData] = useState(null)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [editParams, setEditParams] = useState({})
  const [changeReason, setChangeReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)
  const [pointInTime, setPointInTime] = useState('')

  const nodeId = node.id || node.node_id

  // Fetch versions when node changes (Wave 4.6: check cache first)
  useEffect(() => {
    if (!nodeId) return
    let cancelled = false

    // Check version cache
    const cached = graph?.getCachedVersions?.(nodeId)
    if (cached) {
      setVersions(cached)
      if (cached.length > 0) setSelectedVersionForBio(cached[0]?.version_id)
      setLoadingVersions(false)
      setActiveTab('versions')
      setDiffData(null)
      setDiffVersionA(null)
      setDiffVersionB(null)
      return
    }

    async function fetchVersions() {
      setLoadingVersions(true)
      try {
        const resp = await get(`/v1/meta/node/${nodeId}/versions?limit=100`)
        const data = resp?.versions || resp?.data?.versions || []
        if (!cancelled) {
          setVersions(data)
          // Store in cache
          graph?.setCachedVersions?.(nodeId, data)
          if (data.length > 0) {
            setSelectedVersionForBio(data[0]?.version_id)
          }
        }
      } catch (err) {
        console.error('Failed to fetch versions:', err)
      } finally {
        if (!cancelled) setLoadingVersions(false)
      }
    }
    fetchVersions()
    setActiveTab('versions')
    setDiffData(null)
    setDiffVersionA(null)
    setDiffVersionB(null)
    return () => { cancelled = true }
  }, [nodeId, get, graph])

  // Init edit params from current version
  useEffect(() => {
    const cv = node.current_version || node.currentVersion
    if (cv?.parameters && Object.keys(cv.parameters).length > 0) {
      setEditParams({ ...cv.parameters })
    } else {
      setEditParams({})
    }
    setChangeReason('')
  }, [node])

  // Populate from versions list if editParams is empty (fallback)
  useEffect(() => {
    if (Object.keys(editParams).length > 0 || versions.length === 0) return
    const active = versions.find(v => v.status === 'deployed') || versions[0]
    if (active?.parameters && Object.keys(active.parameters).length > 0) {
      setEditParams({ ...active.parameters })
    }
  }, [editParams, versions])

  const cv = getCurrentNodeVersion(node)

  // Diff two versions
  const handleDiff = async () => {
    if (!diffVersionA || !diffVersionB) return
    setLoadingDiff(true)
    try {
      const resp = await get(`/v1/meta/compare/versions/${diffVersionA}/${diffVersionB}`)
      setDiffData(resp?.data || resp)
    } catch (err) {
      console.error('Diff failed:', err)
    } finally {
      setLoadingDiff(false)
    }
  }

  // Save new version from edited params
  const handleSaveVersion = async () => {
    if (!changeReason.trim()) return
    setSaving(true)
    try {
      const nodeType = String(node.type || node.nodeType || '').toLowerCase()
      const nodeSubsystem = String(node.subsystem || '').toLowerCase()
      const versionStatus = (nodeType === 'parameter' || nodeSubsystem === 'parameter')
        ? 'deployed'
        : 'tested'

      await post('/v1/meta/node/version', {
        node_id: nodeId,
        parameters: editParams,
        change_reason: changeReason,
        status: versionStatus,
      })
      const resp = await get(`/v1/meta/node/${nodeId}/versions?limit=100`)
      setVersions(resp?.versions || resp?.data?.versions || [])
      setChangeReason('')
      graph?.triggerPressureWave?.(nodeId)
    } catch (err) {
      console.error('Save version failed:', err)
    } finally {
      setSaving(false)
    }
  }

  // Copy context to clipboard
  const handleCopyContext = () => {
    navigator.clipboard.writeText(buildNodeContextMarkdown(node, versions)).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="fixed right-0 top-0 z-50 flex h-full w-[400px] flex-col overflow-hidden border-l border-terminal-600 bg-terminal-800 shadow-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-terminal-600 bg-terminal-800/95 px-4 py-3 backdrop-blur">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-terminal-100">{node.name || node.label}</h2>
          <p className="text-xs capitalize text-terminal-400">{node.subsystem} / {node.type || node.nodeType}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleCopyContext}
            className="p-1 text-terminal-400 transition-colors hover:text-terminal-100"
            title="Copy context to clipboard"
          >
            {copied ? <Check className="size-4 text-green-400" /> : <Clipboard className="size-4" />}
          </button>
          <button
            onClick={onClose}
            className="p-1 text-terminal-400 transition-colors hover:text-terminal-100"
          >
            <X className="size-4" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-terminal-700 px-2">
        {['versions', 'biography', 'params', 'context'].map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`border-b-2 px-3 py-2 text-[10px] font-medium capitalize transition-colors ${
              activeTab === tab
                ? 'border-amber-400 text-amber-400'
                : 'border-transparent text-terminal-500 hover:text-terminal-300'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {/* Node info (always visible) */}
        <div className="space-y-2 border-b border-terminal-700 px-4 py-3">
          {node.description && (
            <p className="text-xs text-terminal-300">{node.description}</p>
          )}
          {node.source_file && (
            <p className="truncate font-mono text-xs text-terminal-500" title={node.source_file}>
              {node.source_file}
            </p>
          )}
          {node.owner && (
            <p className="text-xs text-terminal-400">Owner: <span className="text-terminal-300">{node.owner}</span></p>
          )}
        </div>

        {/* Current version */}
        {cv && (
          <div className="border-b border-terminal-700 px-4 py-3">
            <div className="mb-2 text-xs font-medium text-terminal-300">Active Version</div>
            <div className="space-y-1.5 rounded-lg bg-terminal-900 p-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-terminal-100">v{cv.version_number}</span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] ${STATUS_COLORS[cv.status] || STATUS_COLORS.tested}`}>
                  {cv.status}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                {cv.agg_pf != null && (
                  <div>
                    <span className="text-terminal-500">PF </span>
                    <span className={cv.agg_pf >= 1.0 ? 'font-mono text-green-400' : 'font-mono text-red-400'}>
                      {cv.agg_pf.toFixed(2)}
                    </span>
                  </div>
                )}
                {cv.win_rate != null && (
                  <div>
                    <span className="text-terminal-500">Win </span>
                    <span className="font-mono text-terminal-200">{(cv.win_rate * 100).toFixed(1)}%</span>
                  </div>
                )}
                {cv.trade_count != null && (
                  <div>
                    <span className="text-terminal-500">Trades </span>
                    <span className="font-mono text-terminal-200">{cv.trade_count}</span>
                  </div>
                )}
                {cv.net_pnl != null && (
                  <div>
                    <span className="text-terminal-500">PnL </span>
                    <span className={cv.net_pnl >= 0 ? 'font-mono text-green-400' : 'font-mono text-red-400'}>
                      ${cv.net_pnl.toFixed(0)}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Tab content */}
        {activeTab === 'versions' && (
          <VersionsTab
            versions={versions}
            loadingVersions={loadingVersions}
            pointInTime={pointInTime}
            setPointInTime={setPointInTime}
            showAll={showAll}
            setShowAll={setShowAll}
            showFrontierOnly={showFrontierOnly}
            setShowFrontierOnly={setShowFrontierOnly}
            expandedVersion={expandedVersion}
            setExpandedVersion={setExpandedVersion}
            selectedVersionForBio={selectedVersionForBio}
            setSelectedVersionForBio={setSelectedVersionForBio}
            setActiveTab={setActiveTab}
            diffVersionA={diffVersionA}
            setDiffVersionA={setDiffVersionA}
            diffVersionB={diffVersionB}
            setDiffVersionB={setDiffVersionB}
            diffData={diffData}
            setDiffData={setDiffData}
            loadingDiff={loadingDiff}
            handleDiff={handleDiff}
          />
        )}

        {activeTab === 'biography' && (
          <BiographyTab
            versions={versions}
            selectedVersionForBio={selectedVersionForBio}
            setSelectedVersionForBio={setSelectedVersionForBio}
            graph={graph}
            nodeId={nodeId}
            get={get}
          />
        )}

        {activeTab === 'params' && (
          <ParamsTab
            node={node}
            editParams={editParams}
            setEditParams={setEditParams}
            changeReason={changeReason}
            setChangeReason={setChangeReason}
            saving={saving}
            handleSaveVersion={handleSaveVersion}
          />
        )}

        {activeTab === 'context' && (
          <ContextTab
            node={node}
            nodeId={nodeId}
            versions={versions}
            graph={graph}
            copied={copied}
            handleCopyContext={handleCopyContext}
          />
        )}
      </div>
    </div>
  )
}
