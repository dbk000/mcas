/**
 * All localStorage / sessionStorage keys — single source of truth.
 * No string literals at point of use.
 */
export const STORAGE_KEYS = {
  // Session & connection (SessionContext.jsx)
  API_BASE_URL: 'mcas_api_base_url',
  LEGACY_API_CONFIG: 'mcas_api_config',

  // Dev overrides (ApiContext.jsx)
  CHAOS_CONFIG: 'mcas_chaos_config',
  FIXTURE_CONFIG: 'mcas_fixture_config',

  // Subscription tier (SubscriptionContext.jsx)
  ACCESS_TIER: 'mcas_local_access_tier',
  ACCESS_ACTIVATED_AT: 'mcas_local_access_activated_at',
  LEGACY_TIER: 'mcas_tier',
  LEGACY_TIER_ACTIVATED_AT: 'mcas_tier_activated_at',

  // Logging (logger.js)
  LOG_LEVEL: 'mcas_log_level',

  // Pipeline layout (layoutConfig.js)
  PIPELINE_LAYOUT_POSITIONS: 'pipeline-layout-positions',

  // Theme (ThemeSwitcher.jsx)
  THEME: 'mcas_theme',

  // Checkout flow (returnTo.js) — sessionStorage
  CHECKOUT_RETURN_TO: 'mcas_checkout_return_to',
}
