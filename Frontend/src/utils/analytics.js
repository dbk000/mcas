import { getLogger } from './logger'

const log = getLogger('analytics')
const BUFFER_LIMIT = 250
const BUFFER_KEY = '__mcasAnalyticsBuffer'

export const FRONTEND_ANALYTICS_EVENTS = {
  landingCtaClick: 'landing_cta_click',
  winnersCtaClick: 'winners_cta_click',
  pricingCtaClick: 'pricing_cta_click',
  paywallCtaClick: 'paywall_cta_click',
  checkoutSuccessLanded: 'checkout_success_landed',
  checkoutCancelLanded: 'checkout_cancel_landed',
  settingsConfigured: 'settings_configured',
  strategyCardClicked: 'strategy_card_clicked',
}

export const ANALYTICS_EVENTS = {
  ...FRONTEND_ANALYTICS_EVENTS,
  LANDING_CTA_CLICK: FRONTEND_ANALYTICS_EVENTS.landingCtaClick,
  WINNERS_CTA_CLICK: FRONTEND_ANALYTICS_EVENTS.winnersCtaClick,
  PRICING_CTA_CLICK: FRONTEND_ANALYTICS_EVENTS.pricingCtaClick,
  PAYWALL_CTA_CLICK: FRONTEND_ANALYTICS_EVENTS.paywallCtaClick,
  CHECKOUT_SUCCESS_LANDED: FRONTEND_ANALYTICS_EVENTS.checkoutSuccessLanded,
  CHECKOUT_CANCEL_LANDED: FRONTEND_ANALYTICS_EVENTS.checkoutCancelLanded,
  SETTINGS_CONFIGURED: FRONTEND_ANALYTICS_EVENTS.settingsConfigured,
  STRATEGY_CARD_CLICKED: FRONTEND_ANALYTICS_EVENTS.strategyCardClicked,
}

export const SOURCE_STATUS_LABELS = {
  live: 'Live feed',
  placeholder: 'Authored preview set',
  degraded: 'Live feed temporarily unavailable, showing authored preview set',
}

function ensureBuffer() {
  if (typeof window === 'undefined') {
    return []
  }

  if (!Array.isArray(window[BUFFER_KEY])) {
    window[BUFFER_KEY] = []
  }

  const buffer = window[BUFFER_KEY]
  const existingMcas = window.__mcas || {}
  window.__mcas = {
    ...existingMcas,
    analyticsBuffer: buffer,
    getAnalyticsEvents: () => [...buffer],
    clearAnalyticsEvents: () => {
      buffer.length = 0
    },
  }

  return buffer
}

function getViewportBucket() {
  if (typeof window === 'undefined') return 'server'
  if (window.innerWidth < 768) return 'mobile'
  if (window.innerWidth < 1280) return 'tablet'
  return 'desktop'
}

export function emitAnalyticsEvent(name, payload = {}) {
  const buffer = ensureBuffer()
  const event = {
    name,
    route: payload.route || (typeof window !== 'undefined' ? window.location.pathname : ''),
    surface: payload.surface || 'unknown',
    placement: payload.placement || 'unknown',
    source_status: payload.sourceStatus || payload.source_status || 'live',
    viewport_bucket: payload.viewport_bucket || getViewportBucket(),
    ts: payload.ts || new Date().toISOString(),
    ...payload,
  }

  buffer.push(event)
  if (buffer.length > BUFFER_LIMIT) {
    buffer.splice(0, buffer.length - BUFFER_LIMIT)
  }

  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mcas:analytics', { detail: event }))
    if (typeof window.__mcasAnalyticsSink === 'function') {
      try {
        window.__mcasAnalyticsSink(event)
      } catch (error) {
        log.warn('Analytics sink failed', {
          event: 'analytics_sink_error',
          error_message: error.message,
          analytics_name: name,
        })
      }
    }
  }

  log.debug('Frontend analytics event emitted', {
    event: 'frontend_analytics_emit',
    analytics_name: name,
    analytics_surface: event.surface,
    analytics_placement: event.placement,
  })

  return event
}

export function emitCtaEvent(name, {
  ctaId,
  ctaLabel,
  cta_id,
  cta_label,
  href,
  returnToPresent = false,
  return_to_present,
  tier = 'free',
  ...payload
}) {
  return emitAnalyticsEvent(name, {
    ...payload,
    cta_id: ctaId || cta_id,
    cta_label: ctaLabel || cta_label,
    href,
    return_to_present: Boolean(returnToPresent || return_to_present),
    tier,
  })
}

export function trackCtaClick(name, payload) {
  return emitCtaEvent(name, {
    ctaId: payload.cta_id || payload.ctaId,
    ctaLabel: payload.cta_label || payload.ctaLabel,
    href: payload.href,
    returnToPresent: payload.return_to_present || payload.returnToPresent,
    tier: payload.tier,
    ...payload,
  })
}

export function trackSettingsConfigured({
  sourceStatus = 'live',
  hasBaseUrl = false,
  hasApiKey = false,
  origin = 'settings',
}) {
  return emitAnalyticsEvent(FRONTEND_ANALYTICS_EVENTS.settingsConfigured, {
    surface: 'settings',
    placement: 'save_success',
    source_status: sourceStatus,
    has_base_url: Boolean(hasBaseUrl),
    has_api_key: Boolean(hasApiKey),
    origin,
  })
}
