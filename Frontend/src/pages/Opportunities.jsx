import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useOpportunities, useStrategies } from '../hooks/useData'
import { useToast } from '../context/ToastContext'
import { useApi } from '../context/ApiContext'
import { normalizeRequestError } from '../api/errors'
import { downloadResponseBlob } from '../api/downloads'
import { auditIntegrity, IntegrityStatus } from '../utils/integrity'
import {
  formatTradePlan,
  copyToClipboard,
} from '../utils/formatters'
import { getPaginationLimit } from '../config/governed'
import CurveViewerModal from './opportunities/CurveViewerModal'
import SimulationModal from './opportunities/SimulationModal'
import OpportunitiesToolbar from './opportunities/OpportunitiesToolbar'
import OpportunitiesResults from './opportunities/OpportunitiesResults'
import { sortOpportunities } from './opportunities/opportunitiesUtils'

export default function Opportunities() {
  const { addToast } = useToast()
  const { post, request } = useApi()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const exportAbortRef = useRef(null)
  const [filters, setFilters] = useState({
    status: 'active',
    setup_id: searchParams.get('setup_id') || '',
    min_score: 0,
    limit: getPaginationLimit('opportunities_default'),
  })
  const [sortBy, setSortBy] = useState('score')
  const [showSimModal, setShowSimModal] = useState(false)
  const [simulating, setSimulating] = useState(false)
  const [simConfig, setSimConfig] = useState({
    position_size_usd: 100,
    limit: getPaginationLimit('opportunities_max'),
    min_score: 0,
    setup_ids: [],
    timeframes: [],
    directions: [],
    from_date: '',
    to_date: '',
    exclude_dates: [],
    include_dates: [],
  })
  const [showDateFilters, setShowDateFilters] = useState(false)
  const [excludeDateInput, setExcludeDateInput] = useState('')
  const [includeDateInput, setIncludeDateInput] = useState('')
  const [curveOpp, setCurveOpp] = useState(null)
  const [selectedOppIds, setSelectedOppIds] = useState([])

  const { data: strategyData } = useStrategies()
  const setupOptions = [
    { value: '', label: 'All Setups' },
    ...(strategyData?.strategies || []).map((strategy) => ({
      value: strategy.id,
      label: strategy.label,
    })),
  ]
  const simulationSetupOptions = (strategyData?.strategies || []).map((strategy) => ({
    value: strategy.id,
    label: strategy.id,
  }))

  const apiParams = {
    status: filters.status,
    min_score: filters.min_score,
    limit: filters.limit,
  }

  if (filters.setup_id) apiParams.setup_id = filters.setup_id
  if (filters.status === 'expired') apiParams.include_outcome = true

  const { data, loading, lastUpdated, refetch, meta } = useOpportunities(apiParams)
  const rawOpportunities = useMemo(() => data?.opportunities || [], [data?.opportunities])
  const opportunitiesIntegrity = useMemo(() => {
    if (rawOpportunities.length === 0) return IntegrityStatus.OK
    return auditIntegrity(rawOpportunities, 'opportunity')
  }, [rawOpportunities])
  const opportunities = sortOpportunities(rawOpportunities, sortBy)
  const selectedOppSet = useMemo(() => new Set(selectedOppIds), [selectedOppIds])
  const showIntegrityWarning = opportunitiesIntegrity !== IntegrityStatus.OK && rawOpportunities.length > 0

  useEffect(() => {
    if (filters.status !== 'expired') {
      setSelectedOppIds([])
      return
    }

    const visibleIds = new Set(
      opportunities
        .map((opportunity) => (opportunity?.id != null ? String(opportunity.id) : null))
        .filter(Boolean)
    )

    setSelectedOppIds((current) => current.filter((id) => visibleIds.has(id)))
  }, [filters.status, opportunities])

  useEffect(() => (
    () => {
      exportAbortRef.current?.abort()
    }
  ), [])

  const handleCopyTradePlan = async (opportunity) => {
    const plan = formatTradePlan({
      symbol: opportunity.symbol,
      timeframe: opportunity.timeframe,
      setup_id: opportunity.setup_id,
      direction: opportunity.direction,
      entry_price: opportunity.payload?.entry_price,
      stop_price: opportunity.payload?.stop_price,
      tp1_price: opportunity.payload?.tp1_price,
      tp2_price: opportunity.payload?.tp2_price,
      r_value: opportunity.payload?.r_value,
      score: opportunity.score,
      reason: opportunity.reason,
    })

    await copyToClipboard(plan)
    addToast('Trade plan copied to clipboard', 'success')
  }

  const handleExportCsv = useCallback(async () => {
    exportAbortRef.current?.abort()
    const controller = new AbortController()
    exportAbortRef.current = controller

    try {
      const params = {
        status: filters.status,
        min_score: filters.min_score,
        limit: 10000,
      }
      if (filters.setup_id) params.setup_id = filters.setup_id

      const response = await request('/v1/opportunities/csv', {
        params,
        responseType: 'raw',
        signal: controller.signal,
      })

      await downloadResponseBlob(response, 'opportunities.csv')
      addToast('CSV downloaded', 'success')
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (!normalized.isAbort) {
        addToast(`CSV export failed: ${normalized.message}`, 'error')
      }
    } finally {
      if (exportAbortRef.current === controller) {
        exportAbortRef.current = null
      }
    }
  }, [addToast, filters.min_score, filters.setup_id, filters.status, request])

  const handleSimulateExpired = async () => {
    setSimulating(true)
    try {
      const hasIncludeDates = simConfig.include_dates.length > 0
      const payload = {
        position_size_usd: simConfig.position_size_usd,
        limit: simConfig.limit,
        setup_ids: simConfig.setup_ids.length ? simConfig.setup_ids : (filters.setup_id ? [filters.setup_id] : null),
        min_score: simConfig.min_score,
        timeframes: simConfig.timeframes.length ? simConfig.timeframes : null,
        directions: simConfig.directions.length ? simConfig.directions : null,
        from_date: hasIncludeDates ? null : (simConfig.from_date || null),
        to_date: hasIncludeDates ? null : (simConfig.to_date || null),
        include_dates: hasIncludeDates ? simConfig.include_dates : null,
        exclude_dates: simConfig.exclude_dates.length ? simConfig.exclude_dates : null,
      }
      const response = await post('/v1/control/runs/simulation', payload)

      if (response.run_id) {
        addToast(`Simulation started: ${response.message}`, 'success')
        setShowSimModal(false)
        navigate(`/app/runs/${response.run_id}`)
      }
    } catch (error) {
      addToast(`Failed to start simulation: ${error.message}`, 'error')
    } finally {
      setSimulating(false)
    }
  }

  const toggleOppSelection = (oppId) => {
    if (oppId == null) return
    const key = String(oppId)
    setSelectedOppIds((current) => (
      current.includes(key) ? current.filter((id) => id !== key) : [...current, key]
    ))
  }

  const openSurveyMode = () => {
    const params = new URLSearchParams({ mode: 'selected' })
    if (selectedOppIds.length > 0) params.set('ids', selectedOppIds.join(','))
    navigate(`/app/opportunities/survey?${params.toString()}`)
  }

  return (
    <div className="space-y-6" data-operator-surface>
      <OpportunitiesToolbar
        filters={filters}
        setFilters={setFilters}
        sortBy={sortBy}
        setSortBy={setSortBy}
        setupOptions={setupOptions}
        opportunitiesCount={opportunities.length}
        selectedCount={selectedOppIds.length}
        lastUpdated={lastUpdated}
        meta={meta}
        loading={loading}
        refetch={refetch}
        integrityStatus={opportunitiesIntegrity}
        showIntegrityWarning={showIntegrityWarning}
        onExportCsv={handleExportCsv}
        onOpenSurvey={openSurveyMode}
        onOpenSimulation={() => setShowSimModal(true)}
      />

      <SimulationModal
        isOpen={showSimModal}
        onClose={() => setShowSimModal(false)}
        onSimulate={handleSimulateExpired}
        simulating={simulating}
        simConfig={simConfig}
        setSimConfig={setSimConfig}
        showDateFilters={showDateFilters}
        setShowDateFilters={setShowDateFilters}
        excludeDateInput={excludeDateInput}
        setExcludeDateInput={setExcludeDateInput}
        includeDateInput={includeDateInput}
        setIncludeDateInput={setIncludeDateInput}
        setupOptions={simulationSetupOptions}
      />

      <OpportunitiesResults
        loading={loading}
        hasData={!!data}
        opportunities={opportunities}
        filters={filters}
        selectedOppSet={selectedOppSet}
        onCopyTradePlan={handleCopyTradePlan}
        onOpenCurve={setCurveOpp}
        onToggleSelection={toggleOppSelection}
      />

      <CurveViewerModal opportunity={curveOpp} onClose={() => setCurveOpp(null)} />
    </div>
  )
}
