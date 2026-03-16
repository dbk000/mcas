import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useRun, useRunTrades } from '../hooks/useData'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { usePublicJson } from '../utils/publicJson'
import { auditIntegrity, IntegrityStatus } from '../utils/integrity'
import {
  Card,
  CardContent,
  Skeleton,
  Tabs,
} from '../components/ui'
import RunDetailHeader from './run-detail/RunDetailHeader'
import RunOverviewTab from './run-detail/RunOverviewTab'
import RunTradesTab from './run-detail/RunTradesTab'
import RunEventsTab from './run-detail/RunEventsTab'
import RunCancelModal from './run-detail/RunCancelModal'

const RUN_DETAIL_TAB_ID = 'run-detail-tabs'

export default function RunDetail() {
  const { id } = useParams()
  const { post } = useApi()
  const { addToast } = useToast()
  const { data: metricsDemo } = usePublicJson('/curation/metrics-demo.v1.json', {
    run_detail: {
      trades: [],
      expected: {
        timestamp_field: 'exit_ts',
        pnl_field: 'pnl_net',
      },
    },
  })

  const { data: run, loading, error, refetch } = useRun(id)
  const { data: runTradesData } = useRunTrades(id, { limit: 500 })
  const [activeTab, setActiveTab] = useState('overview')
  const [showCancelModal, setShowCancelModal] = useState(false)
  const [canceling, setCanceling] = useState(false)

  const tradesIntegrity = useMemo(() => {
    if (!runTradesData?.trades) return IntegrityStatus.OK
    return auditIntegrity(runTradesData.trades, 'simulation')
  }, [runTradesData?.trades])

  const handleCancel = async () => {
    setCanceling(true)
    try {
      await post(`/v1/control/runs/${id}/cancel`)
      addToast('Run canceled', 'success')
      setShowCancelModal(false)
      refetch()
    } catch (cancelError) {
      addToast(`Failed to cancel: ${cancelError.message}`, 'error')
    } finally {
      setCanceling(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton variant="title" className="w-48" />
        <Card>
          <CardContent className="space-y-4">
            <Skeleton />
            <Skeleton />
            <Skeleton className="w-2/3" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !run) {
    return (
      <div className="py-12 text-center">
        <p className="text-terminal-400">Run not found</p>
        <Link to="/app/runs" className="btn btn-secondary mt-4">
          <ArrowLeft className="size-4" />
          Back to Runs
        </Link>
      </div>
    )
  }

  const canCancel = run.status === 'PENDING' || run.status === 'RUNNING'
  const hasIntegrityWarning = tradesIntegrity !== IntegrityStatus.OK && (runTradesData?.trades?.length || 0) > 0

  return (
    <div className="space-y-6" data-operator-surface>
      <RunDetailHeader
        run={run}
        runId={id}
        tradesIntegrity={tradesIntegrity}
        hasIntegrityWarning={hasIntegrityWarning}
        canCancel={canCancel}
        onRequestCancel={() => setShowCancelModal(true)}
      />

      <Tabs
        tabs={[
          { value: 'overview', label: 'Overview' },
          { value: 'trades', label: 'Trades' },
          { value: 'events', label: 'Events' },
        ]}
        activeTab={activeTab}
        onChange={setActiveTab}
        idBase={RUN_DETAIL_TAB_ID}
      />

      <div
        role="tabpanel"
        id={`${RUN_DETAIL_TAB_ID}-panel-${activeTab}`}
        aria-labelledby={`${RUN_DETAIL_TAB_ID}-tab-${activeTab}`}
      >
        {activeTab === 'overview' && (
          <RunOverviewTab
            run={run}
            trades={runTradesData?.trades}
            metricsDemo={metricsDemo}
          />
        )}
        {activeTab === 'trades' && <RunTradesTab runId={id} />}
        {activeTab === 'events' && <RunEventsTab runId={id} />}
      </div>

      <RunCancelModal
        isOpen={showCancelModal}
        canceling={canceling}
        onClose={() => setShowCancelModal(false)}
        onConfirm={handleCancel}
      />
    </div>
  )
}
