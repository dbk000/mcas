import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardContent,
  Spinner
} from '../components/ui'
import AnalysisResults from './correlation-analysis/AnalysisResults'
import RunSelectionPanel from './correlation-analysis/RunSelectionPanel'
import { useCorrelationAnalysis } from './correlation-analysis/useCorrelationAnalysis'

export default function CorrelationAnalysis() {
  const { post, get, isConnected } = useApi()
  const { addToast } = useToast()
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
    </div>
  )
}
