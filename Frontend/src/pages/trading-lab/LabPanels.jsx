import { Link } from 'react-router-dom'
import { ArrowUpRight, ChevronRight, Radio, Search } from 'lucide-react'
import LabChart from './LabChart'
import { ageLabel, format, isStale, number, price, signed, tone } from './model'
import { feedThreshold } from './useLabRuntime'

export function FeedBadge({ feed, asOf, tf, now }) {
  let label = 'Waiting',
    kind = 'muted'
  if (feed.demo) {
    label = 'Sample'
    kind = 'sample'
  } else if (feed.error) {
    label = feed.data ? 'Error · cached' : 'Unavailable'
    kind = 'warning'
  } else if (feed.paused) {
    label = 'Paused'
    kind = 'warning'
  } else if (feed.loading) label = 'Loading'
  else if (feed.data) {
    label = !asOf
      ? 'Age unknown'
      : isStale(asOf, feedThreshold(tf), now)
        ? 'Stale'
        : 'Current'
    kind = label === 'Current' ? 'current' : 'warning'
  }
  return (
    <span
      className={`lab-badge ${kind}`}
      title={
        feed.error ||
        (asOf ? `Source timestamp: ${asOf}` : 'No source timestamp supplied')
      }
    >
      {label}
    </span>
  )
}

export function Panel({
  title,
  number: n,
  meta,
  children,
  className = '',
  action,
}) {
  return (
    <section className={`lab-panel ${className}`} aria-label={title}>
      <header className="lab-panel-heading">
        <h2>
          <span>{n}</span>
          {title}
        </h2>
        <div className="lab-panel-meta">
          {meta}
          {action}
        </div>
      </header>
      {children}
    </section>
  )
}

export function Empty({
  feed,
  children = 'No records returned for this view.',
}) {
  return (
    <div className="lab-empty" role="status">
      <Radio aria-hidden="true" />
      <strong>
        {feed?.loading
          ? 'Connecting to the feed…'
          : feed?.error
            ? 'Feed unavailable'
            : 'No data to display'}
      </strong>
      <p>{feed?.error || children}</p>
      {feed?.error && (
        <button type="button" onClick={feed.refresh}>
          Try again
        </button>
      )}
    </div>
  )
}

export function DiscoveryPanel({
  feed,
  rows,
  selected,
  onSelect,
  tf,
  now,
  search,
  onSearch,
  sort,
  onSort,
}) {
  return (
    <Panel
      title="Discovery radar"
      number="01"
      className="lab-discovery"
      meta={<span>{rows.length} pairs</span>}
    >
      <div className="lab-filters">
        <label className="lab-search">
          <Search size={16} />
          <input
            aria-label="Search markets"
            placeholder="Find a pair…"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
          />
        </label>
        <select
          aria-label="Rank markets by"
          value={sort}
          onChange={(e) => onSort(e.target.value)}
        >
          <option value="volume">Volume Z</option>
          <option value="move">Absolute move</option>
          <option value="symbol">Symbol A–Z</option>
        </select>
      </div>
      {feed.error && feed.data && (
        <div className="lab-inline-warning">
          Update failed · showing cached records
        </div>
      )}
      <div className="lab-table-scroll">
        <table className="lab-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>{tf} bar Δ</th>
              <th>Vol Z</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const stale =
                row.stale || isStale(row.as_of_ts, feedThreshold(tf), now)
              return (
                <tr
                  key={row.symbol}
                  className={`${row.symbol === selected ? 'selected' : ''} ${stale && !feed.demo ? 'aged' : ''}`}
                >
                  <td>
                    <button
                      onClick={() => onSelect(row.symbol)}
                      aria-label={`Investigate ${row.symbol}`}
                      aria-pressed={selected === row.symbol}
                    >
                      <strong>{row.symbol.replace(/USDT$/, '')}</strong>
                      <span>
                        {' '}
                        / {row.symbol.endsWith('USDT') ? 'USDT' : row.symbol}
                      </span>
                    </button>
                    <small>
                      {feed.demo
                        ? 'Sample data'
                        : stale
                          ? `Stale · ${ageLabel(row.as_of_ts, now)}`
                          : ageLabel(row.as_of_ts, now)}
                    </small>
                  </td>
                  <td className={tone(row.pct_change)}>
                    {signed(row.pct_change)}
                  </td>
                  <td>
                    {format(row.volume_z)}
                    <span
                      className="lab-meter"
                      style={{
                        '--meter': `${Math.min(100, Math.abs(number(row.volume_z) || 0) * 10)}%`,
                      }}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!rows.length && (
          <Empty feed={feed}>
            {search
              ? 'No pairs match this search.'
              : 'The monitored universe has not returned any snapshots.'}
          </Empty>
        )}
      </div>
      <footer className="lab-panel-footer">
        Candle open → close · volume Z supplied by MCAS
      </footer>
    </Panel>
  )
}

export function UniversePanel({ feed, rows, selected, onSelect, tf, now }) {
  return (
    <Panel
      title="Market field"
      number="02"
      className="lab-universe"
      meta={<span>{tf} candle change</span>}
    >
      {rows.length ? (
        <div className="lab-tiles">
          {rows.map((row) => (
            <button
              key={row.symbol}
              onClick={() => onSelect(row.symbol)}
              className={`lab-tile ${tone(row.pct_change)} ${selected === row.symbol ? 'selected' : ''} ${!feed.demo && (row.stale || isStale(row.as_of_ts, feedThreshold(tf), now)) ? 'aged' : ''}`}
              aria-label={`Select ${row.symbol}, ${signed(row.pct_change)}`}
              title={`${row.symbol} · ${feed.demo ? 'sample' : ageLabel(row.as_of_ts, now)}`}
            >
              <strong>{row.symbol.replace(/USDT$/, '')}</strong>
              <span>{signed(row.pct_change)}</span>
            </button>
          ))}
        </div>
      ) : (
        <Empty feed={feed} />
      )}
      <footer className="lab-panel-footer">
        Fixed alphabetical positions · select a pair to link the room
      </footer>
    </Panel>
  )
}

export function ChartPanel({
  feed,
  symbol,
  tf,
  timeframes,
  onTf,
  theme,
  now,
  scale,
}) {
  const asOf = feed.data?.collector_as_of_ts
  const candles =
    feed.data?.symbol === symbol && feed.data?.timeframe === tf
      ? feed.data.candles
      : []
  const hasFinal = candles?.some((c) => c.is_final === true)
  return (
    <Panel
      title="Price / volume"
      number="03"
      className="lab-price"
      meta={<FeedBadge feed={feed} asOf={asOf} tf={tf} now={now} />}
    >
      <div className="lab-chart-heading">
        <div>
          <strong>{symbol || 'Select a market'}</strong>
          <span>Persisted collector · finalized bars</span>
        </div>
        <label>
          Timeframe
          <select
            aria-label="Chart timeframe"
            value={tf}
            onChange={(e) => onTf(e.target.value)}
          >
            {timeframes.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      </div>
      {hasFinal ? (
        <LabChart
          candles={candles}
          theme={theme}
          marketKey={`collector:${symbol}:${tf}`}
          scale={scale}
        />
      ) : (
        <Empty feed={feed}>
          {symbol
            ? 'No finalized collector bars returned for this market and timeframe.'
            : 'Select a pair from the discovery radar.'}
        </Empty>
      )}
      <footer className="lab-panel-footer">
        <span>
          {feed.demo
            ? 'Illustrative sample · not market prices'
            : `Collector: ${asOf ? ageLabel(asOf, now) : 'age unknown'}`}
        </span>
        <span>UTC · drag / zoom</span>
      </footer>
    </Panel>
  )
}

export function SignalsPanel({ feed, symbol, onSelect, now, maxAge }) {
  const rows = feed.data?.opportunities || []
  return (
    <Panel
      title="Signal watch"
      number="04"
      className="lab-signals"
      meta={<span>{rows.length} returned</span>}
    >
      {feed.error && feed.data && (
        <div className="lab-inline-warning">Update failed · cached signals</div>
      )}
      <div className="lab-signal-list">
        {rows.map((row) => {
          const asOf = row.updated_ts ?? row.candle_ts ?? row.as_of_ts
          const stale = isStale(asOf, maxAge, now)
          return (
            <article
              key={row.id ?? `${row.symbol}:${row.setup_id}:${row.timeframe}`}
              className={symbol === row.symbol ? 'selected' : ''}
            >
              <button
                className="lab-signal-button"
                onClick={() => onSelect(row.symbol, row.timeframe)}
              >
                <strong>{row.symbol}</strong>
                <span
                  className={
                    row.direction === 'long'
                      ? 'lab-positive'
                      : row.direction === 'short'
                        ? 'lab-negative'
                        : ''
                  }
                >
                  {row.direction?.toUpperCase() || '—'}{' '}
                  <ChevronRight size={15} />
                </span>
              </button>
              <div className="lab-signal-meta">
                <span>
                  {row.setup_id} · {row.timeframe}
                </span>
                <span>Score {format(row.score)}</span>
              </div>
              <p>
                {typeof row.reason === 'string'
                  ? row.reason
                  : 'Open the opportunity for its full rationale.'}
              </p>
              <div className="lab-signal-meta">
                <span className={!feed.demo && stale ? 'lab-warning' : ''}>
                  {feed.demo
                    ? 'Sample signal'
                    : `${stale ? 'Stale · ' : ''}${ageLabel(asOf, now)}`}
                </span>
                {!feed.demo && row.id != null && (
                  <Link
                    to={`/app/opportunities/${encodeURIComponent(row.id)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Open ${row.symbol} evidence`}
                  >
                    Evidence <ArrowUpRight size={13} />
                  </Link>
                )}
              </div>
            </article>
          )
        })}
        {!rows.length && (
          <Empty feed={feed}>No active MCAS opportunities returned.</Empty>
        )}
      </div>
    </Panel>
  )
}

export function DerivativesPanel({
  feed,
  symbol,
  onSelect,
  now,
  compact = false,
}) {
  const rows = feed.data?.symbols || []
  return (
    <Panel
      title="Derivatives flow"
      number="05"
      className="lab-derivatives"
      meta={
        <FeedBadge
          feed={feed}
          asOf={feed.data?.data_as_of_ts}
          tf="1h"
          now={now}
        />
      }
    >
      <div className="lab-table-scroll">
        <table className="lab-table">
          <thead>
            <tr>
              <th>Pair</th>
              <th>Funding</th>
              <th>OI 1h</th>
              {!compact && (
                <>
                  <th>OI 24h</th>
                  <th>Taker buy</th>
                  <th>Bar age</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.symbol}
                className={row.symbol === symbol ? 'selected' : ''}
              >
                <td>
                  <button onClick={() => onSelect(row.symbol)}>
                    {row.symbol.replace(/USDT$/, '')}
                  </button>
                </td>
                <td className={tone(row.funding_rate)}>
                  {signed(
                    number(row.funding_rate) === null
                      ? null
                      : Number(row.funding_rate) * 10000,
                    ' bp'
                  )}
                </td>
                <td className={tone(row.oi_change_pct_1h)}>
                  {signed(row.oi_change_pct_1h)}
                </td>
                {!compact && (
                  <>
                    <td className={tone(row.oi_change_pct_24h)}>
                      {signed(row.oi_change_pct_24h)}
                    </td>
                    <td>
                      {number(row.taker_buy_ratio) === null
                        ? '—'
                        : `${format(Number(row.taker_buy_ratio) * 100, 1)}%`}
                    </td>
                    <td>
                      {feed.demo ? 'Sample' : ageLabel(row.bar_start_ts, now)}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <Empty feed={feed}>No derivatives snapshots returned.</Empty>
        )}
      </div>
      <footer className="lab-panel-footer">
        Funding in basis points · open-interest changes from MCAS
      </footer>
    </Panel>
  )
}

export function EvidencePanel({ symbol, tile, signal, derivative, demo }) {
  const payload = signal?.payload || signal || {}
  return (
    <Panel
      title="Selected context"
      number="06"
      className="lab-evidence"
      meta={<span>{demo ? 'Sample' : 'MCAS values'}</span>}
    >
      <div className="lab-evidence-body">
        <p className="lab-eyebrow">{symbol || 'NO SELECTION'}</p>
        <p className="lab-big-price">{price(tile?.close)}</p>
        <div className="lab-evidence-stats">
          {[
            ['Volume Z', format(tile?.volume_z)],
            ['Entry', price(payload.entry_price)],
            ['Stop', price(payload.stop_price)],
            ['Target 1', price(payload.tp1_price)],
            ['OI change · 1h', signed(derivative?.oi_change_pct_1h)],
            ['Setup', signal?.setup_id || '—'],
          ].map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
        <p className="lab-note">
          {signal
            ? typeof signal.reason === 'string'
              ? signal.reason
              : 'Rationale available in the opportunity detail.'
            : 'No returned active signal for this pair. Prices alone are not a signal.'}
        </p>
      </div>
    </Panel>
  )
}

export function EquitiesPanel({ feed, now }) {
  const rows = feed.data?.opportunities || []
  return (
    <Panel
      title="Equities intelligence"
      number="07"
      className="lab-equities"
      meta={<span>Backend opportunity ranking</span>}
    >
      <div className="lab-table-scroll">
        <table className="lab-table">
          <thead>
            <tr>
              <th>Symbol / model</th>
              <th>Expected return</th>
              <th>Probability up</th>
              <th>Confidence</th>
              <th>Regime</th>
              <th>Horizon</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.symbol}</strong>
                  <small>
                    {row.model_name} {row.model_version}
                  </small>
                </td>
                <td className={tone(row.expected_return)}>
                  {signed(
                    number(row.expected_return) === null
                      ? null
                      : Number(row.expected_return) * 100
                  )}
                </td>
                <td>
                  {number(row.probability_up) === null
                    ? '—'
                    : `${format(Number(row.probability_up) * 100, 1)}%`}
                </td>
                <td>
                  {number(row.confidence) === null
                    ? '—'
                    : `${format(Number(row.confidence) * 100, 1)}%`}
                </td>
                <td>{row.regime_label || '—'}</td>
                <td>
                  {row.timeframe} / {row.horizon}
                </td>
                <td>{feed.demo ? 'Sample' : ageLabel(row.updated_at, now)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <Empty feed={feed}>
            The equities pipeline has not returned active opportunities.
          </Empty>
        )}
      </div>
      <footer className="lab-panel-footer">
        Model estimates supplied by MCAS · not observed future returns
      </footer>
    </Panel>
  )
}

export function HealthPanel({ feeds, now, demo, baseUrl, health }) {
  return (
    <Panel
      title="Feed status"
      number="08"
      className="lab-health"
      meta={
        <span>{demo ? 'Preview environment' : 'Connection diagnostics'}</span>
      }
    >
      <div className="lab-health-intro">
        <Radio />
        <div>
          <strong>
            {demo
              ? 'Sample data is active'
              : baseUrl || 'No backend configured'}
          </strong>
          <p>
            {demo
              ? 'Every value in this preview is illustrative. No live trading feed is connected.'
              : `Backend health: ${health || 'unknown'}. A successful request does not prove that source data is fresh.`}
          </p>
        </div>
      </div>
      <div className="lab-table-scroll">
        <table className="lab-table">
          <thead>
            <tr>
              <th>Feed</th>
              <th>State</th>
              <th>Source age</th>
              <th>Last response</th>
            </tr>
          </thead>
          <tbody>
            {feeds.map(({ name, feed, asOf, tf }) => (
              <tr key={name}>
                <td>
                  {name}
                  {feed.error && (
                    <small className="lab-warning">{feed.error}</small>
                  )}
                </td>
                <td>
                  <FeedBadge feed={feed} asOf={asOf} tf={tf} now={now} />
                </td>
                <td>{demo ? 'Sample' : ageLabel(asOf, now)}</td>
                <td>
                  {demo
                    ? 'Sample'
                    : feed.receivedAt
                      ? ageLabel(feed.receivedAt, now)
                      : 'Awaiting response'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="lab-note">
        News, social chatter and historical analogues need additional read APIs.
        This checkout does not expose those feeds to the lab.
      </div>
    </Panel>
  )
}
