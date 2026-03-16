/**
 * Hardening Registry for DevHUD.
 * Provides a functional way to surface build/runtime warnings.
 */

export const HARDENING_FLAGS = {
  KNIP_DEAD_CODE: 'KNIP_DEAD_CODE',
  BUNDLE_OVER_BUDGET: 'BUNDLE_OVER_BUDGET',
  PRESENTATION_INTEGRITY: 'PRESENTATION_INTEGRITY',
};

const isBrowser = typeof window !== 'undefined';
const _registry = new Set();

export const setHardeningWarning = (flag, active = true) => {
  if (active) {
    _registry.add(flag);
    if (isBrowser) window[`__${flag}__`] = true;
  } else {
    _registry.delete(flag);
    if (isBrowser) window[`__${flag}__`] = false;
  }
};

export const getHardeningWarnings = () => {
  const warnings = [];
  const win = isBrowser ? window : {};
  
  if (win.__KNIP_DEAD_CODE__ || _registry.has(HARDENING_FLAGS.KNIP_DEAD_CODE)) {
    warnings.push('Dead Code Detected (Knip)');
  }
  if (win.__BUNDLE_OVER_BUDGET__ || _registry.has(HARDENING_FLAGS.BUNDLE_OVER_BUDGET)) {
    warnings.push('Bundle Over Budget (>5%)');
  }
  if (win.__INTEGRITY_FAILURES__ || _registry.has(HARDENING_FLAGS.PRESENTATION_INTEGRITY)) {
    warnings.push('Presentation Integrity Failures');
  }
  return warnings;
};
