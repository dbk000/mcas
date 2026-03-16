/**
 * Presentation Integrity Audit (Workstream 2)
 * 
 * Focus: Schema conformance, timestamp ordering, and trace completeness.
 * Non-Goals: NO strategy math, NO PF recalculation, NO causality inference.
 */

import { setHardeningWarning, HARDENING_FLAGS } from './hardening';

export const IntegrityStatus = {
  OK: 'ok',
  WARN_SHAPE: 'warn_shape',
  WARN_ORDER: 'warn_order',
  WARN_INCOMPLETE: 'warn_incomplete',
};

/**
 * Audits a data object or array for presentation integrity.
 * 
 * @param {any} data The data to audit.
 * @param {string} type The type of data ('opportunity', 'simulation', 'ohlcv', etc.)
 * @returns {string} One of IntegrityStatus enums.
 */
export function auditIntegrity(data, type) {
  if (!data) return IntegrityStatus.OK;

  let resultStatus = IntegrityStatus.OK;

  if (Array.isArray(data)) {
    if (data.length === 0) return IntegrityStatus.OK;
    
    // For large arrays, we sample to avoid blocking the main thread
    const sampleSize = Math.min(data.length, 100);
    const step = Math.max(1, Math.floor(data.length / sampleSize));
    
    for (let i = 0; i < data.length; i += step) {
      const status = auditItem(data[i], type);
      if (status === IntegrityStatus.WARN_SHAPE) {
        resultStatus = status;
        break;
      }
      if (statusPriority(status) > statusPriority(resultStatus)) {
        resultStatus = status;
      }
    }
  } else {
    resultStatus = auditItem(data, type);
  }

  if (resultStatus !== IntegrityStatus.OK) {
    setHardeningWarning(HARDENING_FLAGS.PRESENTATION_INTEGRITY, true);
  } else {
    setHardeningWarning(HARDENING_FLAGS.PRESENTATION_INTEGRITY, false);
  }

  return resultStatus;
}

function statusPriority(status) {
  switch (status) {
    case IntegrityStatus.WARN_SHAPE: return 3;
    case IntegrityStatus.WARN_ORDER: return 2;
    case IntegrityStatus.WARN_INCOMPLETE: return 1;
    default: return 0;
  }
}

function auditItem(item, type) {
  if (!item || typeof item !== 'object') return IntegrityStatus.WARN_SHAPE;

  switch (type) {
    case 'opportunity':
      return auditOpportunity(item);
    case 'simulation':
      return auditSimulation(item);
    case 'ohlcv':
      return auditOHLCV(item);
    case 'strategy':
      return auditStrategy(item);
    default:
      return IntegrityStatus.OK;
  }
}

function auditStrategy(item) {
  const required = ['id', 'label'];
  for (const field of required) {
    if (item[field] === undefined || item[field] === null) return IntegrityStatus.WARN_INCOMPLETE;
  }
  return IntegrityStatus.OK;
}

function auditOpportunity(item) {
  // Required fields for Glass Box Presentation
  const required = ['id', 'symbol', 'timeframe', 'setup_id', 'signal_bar_close_ts', 'decision_ts'];
  for (const field of required) {
    if (item[field] === undefined || item[field] === null) return IntegrityStatus.WARN_INCOMPLETE;
  }

  // Order audit: decision_ts >= signal_bar_close_ts
  // (Signal must close before or at the moment a decision is made)
  if (Number(item.decision_ts) < Number(item.signal_bar_close_ts)) {
    return IntegrityStatus.WARN_ORDER;
  }

  return IntegrityStatus.OK;
}

function auditSimulation(item) {
  // Required fields for Simulation Trace
  const required = ['opportunity_id', 'exit_type', 'exit_ts', 'pnl_net'];
  for (const field of required) {
    if (item[field] === undefined || item[field] === null) return IntegrityStatus.WARN_INCOMPLETE;
  }
  
  // If we have kline_lineage, it must be an array (even if empty, though empty is a warning)
  if (item.kline_lineage !== undefined && !Array.isArray(item.kline_lineage)) {
    return IntegrityStatus.WARN_SHAPE;
  }

  return IntegrityStatus.OK;
}

function auditOHLCV(item) {
  const required = ['ts', 'o', 'h', 'l', 'c', 'v'];
  for (const field of required) {
    if (item[field] === undefined || item[field] === null) return IntegrityStatus.WARN_INCOMPLETE;
  }
  return IntegrityStatus.OK;
}
