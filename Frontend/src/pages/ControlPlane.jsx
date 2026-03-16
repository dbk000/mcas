import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clock, Database, TrendingUp, Zap } from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  useQueueStatus,
  useMarkets,
  usePaperStatus,
  useStrategies,
} from '../hooks/useData'
import { getLogger } from '../utils/logger'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  EmptyState,
  RefreshControl,
  Tabs,
} from '../components/ui'
import QueueItem from './control-plane/QueueItem'
import ScanForm from './control-plane/ScanForm'
import BacktestForm from './control-plane/BacktestForm'
import PaperForm from './control-plane/PaperForm'

const log = getLogger('control_plane')
const CONTROL_PLANE_TAB_ID = 'control-plane-tabs'

export default function ControlPlane() {
  const { post } = useApi()
  const { addToast } = useToast()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('scan')
  const [loading, setLoading] = useState(false)

  const { data: queue, refetch: refetchQueue } = useQueueStatus()
  const { data: markets } = useMarkets()
  const { data: paperStatus, refetch: refetchPaper } = usePaperStatus()
  const { data: strategyData } = useStrategies()
  const strategies = (strategyData?.strategies || []).map((strategy) => ({
    id: strategy.id,
    label: strategy.label,
  }))

  const handleScan = async (payload) => {
    setLoading(true)
    try {
      const result = await post('/v1/control/runs/scan', payload)
      log.info('Scan queued', { event: 'scan_queued', run_id: result.run_id })
      addToast(`Scan queued: ${result.fingerprint?.slice(0, 8) || result.run_id?.slice(0, 8)}`, 'success')
      refetchQueue()
      navigate(`/app/runs/${result.run_id}`)
    } catch (error) {
      log.error('Scan queue failed', { event: 'scan_queue_error', error_message: error.message })
      addToast(`Failed to create scan: ${error.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleBacktest = async (payload) => {
    setLoading(true)
    try {
      const result = await post('/v1/control/runs/backtest', payload)
      log.info('Backtest queued', { event: 'backtest_queued', run_id: result.run_id })
      addToast(`Backtest queued: ${result.fingerprint?.slice(0, 8) || result.run_id?.slice(0, 8)}`, 'success')
      refetchQueue()
      navigate(`/app/runs/${result.run_id}`)
    } catch (error) {
      log.error('Backtest queue failed', { event: 'backtest_queue_error', error_message: error.message })
      addToast(`Failed to create backtest: ${error.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const handlePaperStart = async (payload) => {
    setLoading(true)
    try {
      await post('/v1/control/runs/paper/start', payload)
      log.info('Paper started', { event: 'paper_start' })
      addToast('Paper trading started', 'success')
      refetchPaper()
      navigate('/app/paper')
    } catch (error) {
      log.error('Paper start failed', { event: 'paper_start_error', error_message: error.message })
      addToast(`Failed to start paper trading: ${error.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const handlePaperStop = async () => {
    setLoading(true)
    try {
      await post('/v1/control/runs/paper/stop', {})
      log.info('Paper stopped', { event: 'paper_stop' })
      addToast('Paper trading stopped', 'success')
      refetchPaper()
    } catch (error) {
      log.error('Paper stop failed', { event: 'paper_stop_error', error_message: error.message })
      addToast(`Failed to stop paper trading: ${error.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const handleCancelRun = async (runId) => {
    try {
      await post(`/v1/control/runs/${runId}/cancel`, {})
      log.info('Run canceled', { event: 'run_canceled', run_id: runId })
      addToast('Run canceled', 'success')
      refetchQueue()
    } catch (error) {
      log.error('Run cancel failed', { event: 'run_cancel_error', run_id: runId, error_message: error.message })
      addToast(`Failed to cancel: ${error.message}`, 'error')
    }
  }

  const tabs = [
    { value: 'scan', label: 'Scan', icon: Zap },
    { value: 'backtest', label: 'Backtest', icon: Database },
    { value: 'paper', label: 'Paper Trade', icon: TrendingUp },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Control Plane</h1>
          <p className="text-terminal-400">Create and manage trading runs</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <Tabs
                tabs={tabs}
                activeTab={activeTab}
                onChange={setActiveTab}
                idBase={CONTROL_PLANE_TAB_ID}
              />
            </CardHeader>
            <CardContent>
              <div
                role="tabpanel"
                id={`${CONTROL_PLANE_TAB_ID}-panel-${activeTab}`}
                aria-labelledby={`${CONTROL_PLANE_TAB_ID}-tab-${activeTab}`}
              >
                {activeTab === 'scan' && (
                  <ScanForm
                    onSubmit={handleScan}
                    loading={loading}
                    strategies={strategies}
                  />
                )}
                {activeTab === 'backtest' && (
                  <BacktestForm
                    onSubmit={handleBacktest}
                    loading={loading}
                    markets={markets}
                    strategies={strategies}
                  />
                )}
                {activeTab === 'paper' && (
                  <PaperForm
                    onSubmit={handlePaperStart}
                    onStop={handlePaperStop}
                    loading={loading}
                    paperStatus={paperStatus}
                    strategies={strategies}
                  />
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        <div>
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Clock className="size-4 text-amber-500" />
                Run Queue
              </CardTitle>
              <RefreshControl lastUpdated={null} onRefresh={refetchQueue} />
            </CardHeader>
            <CardContent>
              {!queue?.queue || queue.queue.length === 0 ? (
                <EmptyState
                  icon={Clock}
                  title="Queue empty"
                  description="No pending or running jobs"
                />
              ) : (
                <div className="space-y-2">
                  {queue.queue.map((item) => (
                    <QueueItem
                      key={item.run_id}
                      item={item}
                      onCancel={handleCancelRun}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
