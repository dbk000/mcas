import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  XCircle,
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import {
  Card,
  CardContent,
  EmptyState,
} from '../components/ui'
import SurveyControls from './opportunity-survey/SurveyControls'
import SurveyStack from './opportunity-survey/SurveyStack'
import { useOpportunitySurveyData } from './opportunity-survey/useOpportunitySurveyData'

export default function OpportunitySurvey() {
  const { get } = useApi()
  const [searchParams] = useSearchParams()
  const survey = useOpportunitySurveyData({ get, searchParams })
  const {
    activeBatch,
    applyRevealEntry,
    applyRevealExit,
    applyRevealExitPlusHours,
    batchOffset,
    batchSize,
    bounds,
    columns,
    currentPage,
    dateFrom,
    dateTo,
    directionFilter,
    directionOptions,
    exitPlusHoursApplied,
    fetchCandidates,
    filteredCount,
    maxRevealAllowed,
    mode,
    oppsError,
    oppsLoading,
    postExitHours,
    presentationMode,
    randomSample,
    revealMode,
    revealOffsetMin,
    strategyFilter,
    strategyOptions,
    topPanelsCollapsed,
    totalPages,
    windowErrorsById,
    windowsById,
    setBatchOffset,
    setBatchSize,
    setDateFrom,
    setDateTo,
    setDirectionFilter,
    setMode,
    setPostExitHours,
    setPresentationMode,
    setRandomSample,
    setRevealMode,
    setRevealOffsetMin,
    setStrategyFilter,
    setTopPanelsCollapsed,
  } = survey

  if (oppsError) {
    return (
      <div className="space-y-4">
        <Link to="/app/opportunities" className="btn btn-ghost btn-sm">
          <ArrowLeft className="size-4" />
          Back to Opportunities
        </Link>
        <EmptyState icon={XCircle} title="Failed to load survey" description={oppsError} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/app/opportunities" className="btn btn-ghost btn-sm">
          <ArrowLeft className="size-4" />
          Back
        </Link>
      </div>

      <SurveyControls
        applyRevealEntry={applyRevealEntry}
        applyRevealExit={applyRevealExit}
        applyRevealExitPlusHours={applyRevealExitPlusHours}
        batchOffset={batchOffset}
        batchSize={batchSize}
        bounds={bounds}
        currentPage={currentPage}
        dateFrom={dateFrom}
        dateTo={dateTo}
        directionFilter={directionFilter}
        directionOptions={directionOptions}
        exitPlusHoursApplied={exitPlusHoursApplied}
        fetchCandidates={fetchCandidates}
        filteredCount={filteredCount}
        maxRevealAllowed={maxRevealAllowed}
        mode={mode}
        postExitHours={postExitHours}
        presentationMode={presentationMode}
        randomSample={randomSample}
        revealMode={revealMode}
        revealOffsetMin={revealOffsetMin}
        strategyFilter={strategyFilter}
        strategyOptions={strategyOptions}
        topPanelsCollapsed={topPanelsCollapsed}
        totalPages={totalPages}
        setBatchOffset={setBatchOffset}
        setBatchSize={setBatchSize}
        setDateFrom={setDateFrom}
        setDateTo={setDateTo}
        setDirectionFilter={setDirectionFilter}
        setMode={setMode}
        setPostExitHours={setPostExitHours}
        setPresentationMode={setPresentationMode}
        setRandomSample={setRandomSample}
        setRevealMode={setRevealMode}
        setRevealOffsetMin={setRevealOffsetMin}
        setStrategyFilter={setStrategyFilter}
        setTopPanelsCollapsed={setTopPanelsCollapsed}
      />

      <Card>
        <CardContent className="space-y-3">
          <SurveyStack
            activeBatch={activeBatch}
            columns={columns}
            exitPlusHoursApplied={exitPlusHoursApplied}
            mode={mode}
            oppsLoading={oppsLoading}
            presentationMode={presentationMode}
            revealMode={revealMode}
            revealOffsetMin={revealOffsetMin}
            windowErrorsById={windowErrorsById}
            windowsById={windowsById}
          />
        </CardContent>
      </Card>
    </div>
  )
}
