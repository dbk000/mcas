import { useCallback, useEffect, useState } from 'react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Badge,
  Card,
  CardContent,
  Select,
  Skeleton,
  Spinner,
  Tabs,
} from '../components/ui'
import AnalysisResults from './correlation-analysis/AnalysisResults'
import RunSelectionPanel from './correlation-analysis/RunSelectionPanel'
import { useCorrelationAnalysis } from './correlation-analysis/useCorrelationAnalysis'

const TIMEFRAME_OPTIONS = [
  { value: '1h', label: '1h' },
  { value: '4h', label: '4h' },
  { value: '1d', label: '1d' },
]

const VIEW_TABS = [
  { value: 'runs', label: 'Run Correlations' },
  { value: 'indicators', label: 'Indicator Matrix' },
]

function corrColor(val) {
  if (val > 0.7) return 'bg-blue-500'
  if (val > 0.3) return 'bg-blue-800/60'
  if (val > -0.3) return 'bg-terminal-700'
  if (val > -0.7) return 'bg-red-800/60'
  return 'bg-red-500'
}

function corrTextColor(val) {
  if (val > 0.7) return 'text-white'
  if (val > 0.3) return 'text-blue-200'
  if (val > -0.3) return 'text-terminal-300'
  if (val > -0.7) return 'text-red-200'
  return 'text-white'
}

function IndicatorCorrelationMatrix({ fields, matrix }) {
  const [hoveredCell, setHoveredCell] = useState(null)
  const cellSize = 20
  const labelWidth = 90

  if (!fields || !matrix || fields.length === 0) {
    return (
      <Card>
        <CardContent>
          <p className="text-terminal-400">No correlation data available.</p>
        </CardContent>
      </Card>
    )
  }

  const n = fields.length
  const gridWidth = labelWidth + n * cellSize
  const topLabelHeight = 80

  return (
    <Card>
      <CardContent className="overflow-auto p-2">
        <div
          className="relative"
          style={{
            width: gridWidth,
            height: topLabelHeight + n * cellSize,
            minWidth: gridWidth,
          }}
        >
          {/* Top axis labels (rotated) */}
          {fields.map((field, colIdx) => (
            <div
              key={`top-${colIdx}`}
              className="absolute text-[9px] text-terminal-400 origin-bottom-left"
              style={{
                left: labelWidth + colIdx * cellSize + cellSize / 2,
                top: topLabelHeight - 4,
                transform: 'rotate(-45deg)',
                whiteSpace: 'nowrap',
                width: 70,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              title={field}
            >
              {field}
            </div>
          ))}

          {/* Rows */}
          {fields.map((rowField, rowIdx) => (
            <div
              key={`row-${rowIdx}`}
              className="absolute flex items-center"
              style={{
                top: topLabelHeight + rowIdx * cellSize,
                left: 0,
                height: cellSize,
              }}
            >
              {/* Row label */}
              <div
                className="text-[9px] text-terminal-400 pr-1 text-right truncate"
                style={{ width: labelWidth }}
                title={rowField}
              >
                {rowField}
              </div>

              {/* Cells */}
              {matrix[rowIdx].map((val, colIdx) => (
                <div
                  key={`cell-${rowIdx}-${colIdx}`}
                  className={`${corrColor(val)} border border-terminal-900/30 cursor-crosshair relative`}
                  style={{
                    width: cellSize,
                    height: cellSize,
                    flexShrink: 0,
                  }}
                  onMouseEnter={() => setHoveredCell({ rowIdx, colIdx, val })}
                  onMouseLeave={() => setHoveredCell(null)}
                />
              ))}
            </div>
          ))}

        </div>

        {/* Floating tooltip via state */}
        {hoveredCell && (
          <HeatmapTooltip
            row={fields[hoveredCell.rowIdx]}
            col={fields[hoveredCell.colIdx]}
            val={hoveredCell.val}
          />
        )}

        {/* Legend */}
        <div className="mt-4 flex items-center gap-4 text-[10px] text-terminal-400">
          <span className="font-medium text-terminal-300">Correlation scale:</span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm bg-red-500" /> {'< -0.7'}
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm bg-red-800/60" /> -0.7 to -0.3
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm bg-terminal-700" /> -0.3 to 0.3
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm bg-blue-800/60" /> 0.3 to 0.7
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-3 w-3 rounded-sm bg-blue-500" /> {'> 0.7'}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

function HeatmapTooltip({ row, col, val }) {
  const [pos, setPos] = useState({ x: 0, y: 0 })

  useEffect(() => {
    function handleMove(e) {
      setPos({ x: e.clientX + 12, y: e.clientY - 10 })
    }
    window.addEventListener('mousemove', handleMove)
    return () => window.removeEventListener('mousemove', handleMove)
  }, [])

  return (
    <div
      className="fixed z-50 pointer-events-none rounded border border-terminal-600 bg-terminal-900 px-2.5 py-1.5 text-xs shadow-lg"
      style={{ left: pos.x, top: pos.y }}
    >
      <span className="text-terminal-300">{row}</span>
      <span className="text-terminal-500 mx-1">&times;</span>
      <span className="text-terminal-300">{col}</span>
      <span className={`ml-2 font-mono font-bold ${corrTextColor(val)}`}>
        {val.toFixed(3)}
      </span>
    </div>
  )
}

function IndicatorMatrixView({ get }) {
  const [timeframe, setTimeframe] = useState('4h')
  const [matrixData, setMatrixData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchMatrix = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await get(`/v1/analytics/indicator-correlations?tf=${timeframe}`)
      if (result && result.data) {
        setMatrixData(result.data)
      } else {
        setError('No data returned')
      }
    } catch (err) {
      setError(err.message || 'Failed to load indicator correlations')
    } finally {
      setLoading(false)
    }
  }, [get, timeframe])

  useEffect(() => {
    fetchMatrix()
  }, [fetchMatrix])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <label className="text-sm text-terminal-400">Timeframe</label>
          <Select
            options={TIMEFRAME_OPTIONS}
            value={timeframe}
            onChange={(e) => setTimeframe(e.target.value)}
            className="text-sm"
          />
        </div>
        {matrixData && (
          <Badge variant="info">
            Based on {matrixData.symbol_count} symbols
          </Badge>
        )}
      </div>

      {loading && (
        <Card>
          <CardContent className="space-y-3">
            <Skeleton variant="title" />
            <Skeleton variant="card" className="h-64" />
          </CardContent>
        </Card>
      )}

      {error && !loading && (
        <Card>
          <CardContent>
            <p className="text-red-400">{error}</p>
          </CardContent>
        </Card>
      )}

      {matrixData && !loading && (
        <IndicatorCorrelationMatrix
          fields={matrixData.fields}
          matrix={matrixData.matrix}
        />
      )}
    </div>
  )
}

export default function CorrelationAnalysis() {
  const { post, get, isConnected } = useApi()
  const { addToast } = useToast()
  const [view, setView] = useState('runs')
  const {
    analysisData,
    analysisStatus,
    availableRuns,
    handleAnalyze,
    loading,
    minTrades,
    polling,
    runsLoading,
    selectedRunCount,
    selectedRunIds,
    setupFilter,
    strategyOptions,
    toggleRun,
    setMinTrades,
    setSetupFilter,
  } = useCorrelationAnalysis({ addToast, get, isConnected, post })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-terminal-100">Correlation Analysis</h1>
        <p className="mt-1 text-sm text-terminal-400">
          Identify patterns in losing trades by analyzing indicator and signal components
        </p>
      </div>

      <Tabs tabs={VIEW_TABS} activeTab={view} onChange={setView} />

      {view === 'runs' && (
        <>
          <RunSelectionPanel
            availableRuns={availableRuns}
            handleAnalyze={handleAnalyze}
            isConnected={isConnected}
            loading={loading}
            minTrades={minTrades}
            polling={polling}
            runsLoading={runsLoading}
            selectedRunCount={selectedRunCount}
            selectedRunIds={selectedRunIds}
            setMinTrades={setMinTrades}
            setSetupFilter={setSetupFilter}
            setupFilter={setupFilter}
            strategyOptions={strategyOptions}
            toggleRun={toggleRun}
          />

          {analysisStatus && analysisStatus !== 'SUCCESS' && (
            <Card>
              <CardContent className="flex items-center gap-4">
                <Spinner size="md" />
                <div>
                  <p className="text-terminal-100">
                    {analysisStatus === 'PENDING' ? 'Waiting for worker...' : 'Running analysis...'}
                  </p>
                  <p className="text-sm text-terminal-400">This may take a moment</p>
                </div>
              </CardContent>
            </Card>
          )}

          {analysisData && analysisStatus === 'SUCCESS' && (
            <AnalysisResults data={analysisData} />
          )}
        </>
      )}

      {view === 'indicators' && (
        <IndicatorMatrixView get={get} />
      )}
    </div>
  )
}
