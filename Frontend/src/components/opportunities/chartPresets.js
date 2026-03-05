export const SURVEY_PRESENTATION_PRICE_SPACING_TABLE = Object.freeze([
  { maxPrice: 0.02, spacing: 20 },
  { maxPrice: 0.1, spacing: 16 },
  { maxPrice: 1, spacing: 12 },
  { maxPrice: 10, spacing: 10 },
  { maxPrice: 100, spacing: 8 },
  { maxPrice: 1000, spacing: 6.5 },
  { maxPrice: 1000000, spacing: 5.5 },
])

export const SURVEY_ROW_CHART_PROPS_STANDARD = Object.freeze({
  compact: true,
  height: 190,
  chartVariant: 'terminal',
  showMovingAverages: false,
  thinTemporalMarkers: true,
  showVolume: false,
  showCloseLine: false,
  targetBarSpacing: 10,
  autoBarSpacingByPrice: false,
  priceBarSpacingTable: SURVEY_PRESENTATION_PRICE_SPACING_TABLE,
})

export const SURVEY_ROW_CHART_PROPS_PRESENTATION = Object.freeze({
  compact: true,
  height: 210,
  chartVariant: 'classic',
  showMovingAverages: false,
  thinTemporalMarkers: true,
  showVolume: false,
  showCloseLine: true,
  targetBarSpacing: 8,
  autoBarSpacingByPrice: true,
  priceBarSpacingTable: SURVEY_PRESENTATION_PRICE_SPACING_TABLE,
})

export const CURVE_VIEWER_CHART_PROPS = Object.freeze({
  height: 420,
})

export const MARKET_LIVE_COLLECTOR_CHART_PROPS = Object.freeze({
  height: 400,
})

export function getSurveyRowChartProps(presentationMode) {
  return presentationMode ? SURVEY_ROW_CHART_PROPS_PRESENTATION : SURVEY_ROW_CHART_PROPS_STANDARD
}
