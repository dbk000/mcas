export function getCurrentNodeVersion(node) {
  return node.current_version || node.currentVersion || null
}

export function getActiveNodeVersion(versions) {
  return versions.find((version) => version.status === 'deployed') || versions[0] || null
}

export function buildNodeContextMarkdown(node, versions) {
  const currentVersion = getCurrentNodeVersion(node)
  const activeVersion = getActiveNodeVersion(versions)
  const lines = [
    `## Node Context: ${node.name || node.label}`,
    '',
    `**Type:** ${node.type || node.nodeType}`,
    `**Subsystem:** ${node.subsystem}`,
    node.description ? `**Description:** ${node.description}` : null,
    node.source_file ? `**Source:** ${node.source_file}` : null,
    node.owner ? `**Owner:** ${node.owner}` : null,
    '',
  ].filter(Boolean)

  if (currentVersion) {
    lines.push(`### Current Version: v${currentVersion.version_number} (${currentVersion.status})`)
    if (currentVersion.agg_pf != null) lines.push(`- PF: ${currentVersion.agg_pf.toFixed(2)}`)
    if (currentVersion.win_rate != null) lines.push(`- Win Rate: ${(currentVersion.win_rate * 100).toFixed(1)}%`)
    if (currentVersion.trade_count != null) lines.push(`- Trades: ${currentVersion.trade_count}`)
    if (currentVersion.net_pnl != null) lines.push(`- PnL: $${currentVersion.net_pnl.toFixed(0)}`)
    lines.push('')
  }

  if (activeVersion) {
    lines.push('### Data Provenance')
    const context = activeVersion.metrics_context || {}
    if (activeVersion.metrics_run_id) {
      lines.push(`- **Simulation Run ID:** \`${activeVersion.metrics_run_id}\``)
      lines.push(`- **Results Query:** \`SELECT * FROM simulation_results WHERE run_id = '${activeVersion.metrics_run_id}'\``)
    } else {
      lines.push('- **Simulation Run ID:** not recorded')
    }

    const runDetail = activeVersion.run_detail
    if (runDetail) {
      lines.push(`- **Run Type:** ${runDetail.run_type}`)
      if (runDetail.run_completed_at) lines.push(`- **Completed:** ${runDetail.run_completed_at}`)
    }

    const start = context?.date_range?.start_ts || context?.date_range?.start || context?.start_date
    const end = context?.date_range?.end_ts || context?.date_range?.end || context?.end_date
    if (start || end) {
      const formatDate = (value) => {
        if (!value) return '?'
        const date = new Date(typeof value === 'number' ? value * 1000 : value)
        return Number.isNaN(date.getTime()) ? String(value) : date.toISOString().slice(0, 10)
      }
      lines.push(`- **Date Range:** ${formatDate(start)} to ${formatDate(end)}`)
    } else {
      lines.push('- **Date Range:** not recorded')
    }

    const symbols = context?.symbols_tested || context?.symbols
    if (Array.isArray(symbols)) {
      lines.push(`- **Symbols:** ${symbols.length} (${symbols.slice(0, 10).join(', ')}${symbols.length > 10 ? '...' : ''})`)
    } else if (typeof symbols === 'number') {
      lines.push(`- **Symbols:** ${symbols}`)
    } else {
      lines.push('- **Symbols:** not recorded')
    }

    if (context?.timeframe) lines.push(`- **Timeframe:** ${context.timeframe}`)
    if (context?.slippage_model || context?.slippage) lines.push(`- **Slippage:** ${context.slippage_model || context.slippage}`)
    if (activeVersion.source_hash) lines.push(`- **Source Hash:** \`${activeVersion.source_hash}\``)
    if (context?.reproduce_cmd) lines.push(`- **Reproduce:** \`${context.reproduce_cmd}\``)
    lines.push('')
  }

  const ports = node.ports || {}
  if (ports.inputs?.length || ports.outputs?.length) {
    lines.push('### Ports')
    if (ports.inputs?.length) {
      lines.push(`**Inputs:** ${ports.inputs.map((port) => `${port.name} (${port.data_type})`).join(', ')}`)
    }
    if (ports.outputs?.length) {
      lines.push(`**Outputs:** ${ports.outputs.map((port) => `${port.name} (${port.data_type})`).join(', ')}`)
    }
    lines.push('')
  }

  if (versions.length > 0) {
    lines.push('### Recent Versions')
    versions.slice(0, 5).forEach((version) => {
      lines.push(`- v${version.version_number} (${version.status}): ${version.change_reason || 'no reason'} — PF ${version.agg_pf?.toFixed(2) || '?'}`)
    })
  }

  return lines.join('\n')
}
