import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Activity,
  ArrowUpRight,
  Check,
  ExternalLink,
  Link2,
  Maximize,
  Monitor,
  Moon,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Sun,
  Trash2,
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { Modal } from '../components/ui'
import {
  getPaginationLimit,
  getSignalMaxAgeS,
  getStalenessThresholdS,
  getTimeframes,
} from '../config/governed'
import { STORAGE_KEYS } from '../config/storageKeys'
import {
  ChartPanel,
  DerivativesPanel,
  DiscoveryPanel,
  Empty,
  EquitiesPanel,
  EvidencePanel,
  HealthPanel,
  SignalsPanel,
  UniversePanel,
} from './trading-lab/LabPanels'
import {
  useLabClock,
  useLabDemo,
  useLabFeed,
  useLinkedSelection,
} from './trading-lab/useLabRuntime'
import {
  ageLabel,
  cleanDisplays,
  DEFAULT_DISPLAYS,
  format,
  isStale,
  number,
  price,
  readStored,
  screenUrl,
  signed,
  timestamp,
  tone,
  VIEWS,
  writeStored,
} from './trading-lab/model'
import './trading-lab/trading-lab.css'

export default function TradingLab({ demo = false }) {
  const [params, setParams] = useSearchParams()
  const { config, connectionStatus } = useApi()
  const saved = useMemo(() => readStored(STORAGE_KEYS.TRADING_LAB, {}), [])
  const view = VIEWS.some((v) => v.id === params.get('view'))
    ? params.get('view')
    : 'command'
  const [theme, setTheme] = useState(() =>
    (params.get('theme') || saved.theme) === 'light' ? 'light' : 'dark'
  )
  const [scale, setScale] = useState(() =>
    [100, 125, 150].includes(Number(params.get('scale') || saved.scale))
      ? Number(params.get('scale') || saved.scale)
      : 100
  )
  const [displays, setDisplays] = useState(() =>
    cleanDisplays(saved.displays || DEFAULT_DISPLAYS)
  )
  const [displayModal, setDisplayModal] = useState(false)
  const [paused, setPaused] = useState(false)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('volume')
  const [feedback, setFeedback] = useState('')
  const [fullScreen, setFullScreen] = useState(
    Boolean(document.fullscreenElement)
  )
  const follow = params.get('follow') !== '0'
  const timeframes = useMemo(() => getTimeframes(), [])
  const initialTf = timeframes.includes(params.get('tf'))
    ? params.get('tf')
    : timeframes.includes('1h')
      ? '1h'
      : timeframes[0]
  const scope = demo ? 'sample' : config.baseUrl
  const [selection, select] = useLinkedSelection({
    scope,
    follow,
    initialSymbol: params.get('symbol'),
    initialTf,
    timeframes,
  })
  const { symbol, tf } = selection
  const now = useLabClock()
  const preview = useLabDemo(demo)
  const limit = getPaginationLimit('opportunities_default')
  const common = { demo, paused }
  const heat = useLabFeed(
    '/v1/universe/heatmap',
    { period: '1h', tf },
    {
      ...common,
      cadence: 'signals_top_ms',
      demoData: preview.data?.heatmap,
      enabled: view !== 'equities',
    }
  )
  const signals = useLabFeed(
    '/v1/opportunities',
    { status: 'active', limit },
    {
      ...common,
      demoData: preview.data?.signals,
      enabled: ['command', 'investigation', 'health'].includes(view),
    }
  )
  const deriv = useLabFeed(
    '/v1/derivatives/dashboard',
    { limit },
    {
      ...common,
      cadence: 'signals_top_ms',
      demoData: preview.data?.derivatives,
      enabled: ['command', 'investigation', 'derivatives', 'health'].includes(
        view
      ),
    }
  )
  const chart = useLabFeed(
    '/v1/ohlcv',
    { symbol, tf, limit: getPaginationLimit('opportunities_max') },
    {
      ...common,
      cadence: 'ohlcv_ms',
      demoData: preview.data?.candles?.[`${symbol}:${tf}`],
      enabled:
        Boolean(symbol) &&
        ['command', 'investigation', 'health'].includes(view),
    }
  )
  const equities = useLabFeed(
    '/v1/equities/opportunities',
    { status: 'active', limit },
    {
      ...common,
      demoData: preview.data?.equities,
      enabled: ['equities', 'health'].includes(view),
    }
  )
  const tiles = useMemo(
    () =>
      (heat.data?.tiles || []).filter((row) => typeof row.symbol === 'string'),
    [heat.data]
  )
  const field = useMemo(
    () => [...tiles].sort((a, b) => a.symbol.localeCompare(b.symbol)),
    [tiles]
  )
  const rows = useMemo(
    () =>
      tiles
        .filter((row) =>
          row.symbol.toLowerCase().includes(search.toLowerCase())
        )
        .sort((a, b) =>
          sort === 'symbol'
            ? a.symbol.localeCompare(b.symbol)
            : (sort === 'move'
                ? Math.abs(number(b.pct_change) || 0) -
                  Math.abs(number(a.pct_change) || 0)
                : (number(b.volume_z) ?? -Infinity) -
                  (number(a.volume_z) ?? -Infinity)) ||
              a.symbol.localeCompare(b.symbol)
        ),
    [search, sort, tiles]
  )
  const tile = tiles.find((row) => row.symbol === symbol)
  const selectedSignal = signals.data?.opportunities?.find(
    (row) => row.symbol === symbol && row.timeframe === tf
  )
  const selectedDerivative = deriv.data?.symbols?.find(
    (row) => row.symbol === symbol
  )
  const newest = (values) =>
    values.reduce(
      (result, value) =>
        (timestamp(value) || 0) > (timestamp(result) || 0) ? value : result,
      null
    )
  const feeds = [
    {
      name: 'Monitored universe',
      feed: heat,
      asOf: newest(tiles.map((row) => row.as_of_ts)),
      tf,
    },
    {
      name: 'Finalized OHLCV',
      feed: chart,
      asOf: chart.data?.collector_as_of_ts,
      tf,
    },
    {
      name: 'Active opportunities',
      feed: signals,
      asOf: newest(
        (signals.data?.opportunities || []).map(
          (row) => row.updated_ts ?? row.candle_ts ?? row.as_of_ts
        )
      ),
      tf,
    },
    {
      name: 'Derivatives',
      feed: deriv,
      asOf: deriv.data?.data_as_of_ts,
      tf: '1h',
    },
    {
      name: 'Equities opportunities',
      feed: equities,
      asOf: newest(
        (equities.data?.opportunities || []).map((row) => row.updated_at)
      ),
      tf: '1d',
    },
  ]

  useEffect(() => {
    if (!symbol && field.length) select(field[0].symbol)
  }, [field, select, symbol])
  useEffect(() => {
    const onChange = () => setFullScreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])
  useEffect(() => {
    const previous = document.title
    document.title = `${params.get('name') || 'Trading lab'} · MCAS${demo ? ' · SAMPLE' : ''}`
    return () => {
      document.title = previous
    }
  }, [demo, params])

  const updateUrl = (key, value) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        next.set(key, String(value))
        return next
      },
      { replace: true }
    )
  const save = () =>
    setFeedback(
      writeStored(STORAGE_KEYS.TRADING_LAB, { theme, scale, displays })
        ? 'Display profile saved on this browser.'
        : 'Browser storage is unavailable; keep the screen URLs to restore this profile.'
    )
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await document.documentElement.requestFullscreen()
    } catch {
      setFeedback(
        'Fullscreen is unavailable here. Open this screen in a separate browser tab or use F11.'
      )
    }
  }
  const choose = useCallback(
    (nextSymbol, nextTf) =>
      select(nextSymbol, timeframes.includes(nextTf) ? nextTf : tf),
    [select, tf, timeframes]
  )
  const refresh = () => feeds.forEach(({ feed }) => feed.refresh())
  const rowCount = tiles.length
  const upCount = tiles.filter((row) => number(row.pct_change) > 0).length
  const downCount = tiles.filter((row) => number(row.pct_change) < 0).length
  const options = { demo, tf, scale, theme }

  return (
    <main
      className="trading-lab"
      data-lab-theme={theme}
      data-lab-view={view}
      style={{ '--lab-scale': scale / 100 }}
    >
      <header className="lab-topbar">
        <Link
          className="lab-brand"
          to={demo ? '/lab-preview' : '/app'}
          aria-label={demo ? 'Trading lab preview' : 'Back to MCAS'}
        >
          <span className="lab-mark">M</span>
          <strong>
            MCAS<span> / TRADING LAB</span>
          </strong>
        </Link>
        <div className="lab-top-status">
          {demo ? (
            <span className="lab-demo-label">SAMPLE DATA · NOT LIVE</span>
          ) : (
            <span className={paused ? 'lab-warning' : ''}>
              <Activity size={15} />
              {paused ? 'UPDATES PAUSED' : 'MCAS FEEDS'}
            </span>
          )}
          <time>
            {new Date(now).toLocaleTimeString('en-GB', {
              timeZone: 'UTC',
              hour12: false,
            })}
            <small>UTC</small>
          </time>
        </div>
        <div className="lab-top-actions">
          <button
            onClick={() => {
              setTheme(theme === 'dark' ? 'light' : 'dark')
              updateUrl('theme', theme === 'dark' ? 'light' : 'dark')
            }}
            aria-label="Toggle light and dark theme"
          >
            {theme === 'dark' ? <Sun /> : <Moon />}
          </button>
          <button
            onClick={toggleFullscreen}
            aria-label={fullScreen ? 'Exit fullscreen' : 'Enter fullscreen'}
          >
            <Maximize />
          </button>
          <button
            className="lab-button-accent"
            aria-label="Displays"
            onClick={() => setDisplayModal(true)}
          >
            <Monitor /> <span>Displays</span>
          </button>
        </div>
      </header>

      <div className="lab-workbar">
        <nav aria-label="Trading lab views">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => updateUrl('view', v.id)}
              aria-current={view === v.id ? 'page' : undefined}
            >
              {v.label}
            </button>
          ))}
        </nav>
        <div className="lab-work-actions">
          <button
            onClick={() => updateUrl('follow', follow ? '0' : '1')}
            aria-pressed={follow}
            title="Link selection between same-origin windows in this browser"
          >
            <Link2 size={15} />
            {follow ? 'Linked' : 'Pinned'}
          </button>
          <label className="lab-scale-label">
            Text
            <select
              aria-label="Projection text size"
              value={scale}
              onChange={(e) => {
                setScale(Number(e.target.value))
                updateUrl('scale', e.target.value)
              }}
            >
              <option value="100">Desk</option>
              <option value="125">Wall</option>
              <option value="150">Large wall</option>
            </select>
          </label>
          <button
            onClick={() => setPaused(!paused)}
            disabled={demo}
            aria-label={paused ? 'Resume feed updates' : 'Pause feed updates'}
          >
            {paused ? <Play size={16} /> : <Pause size={16} />}
          </button>
          <button onClick={refresh} disabled={demo} aria-label="Refresh feeds">
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {feedback && (
        <div className="lab-feedback" role="status">
          {feedback}
          <button onClick={() => setFeedback('')} aria-label="Dismiss status">
            ×
          </button>
        </div>
      )}
      {demo && (
        <div className="lab-preview-note">
          <span>
            Workspace preview · illustrative prices, signals and volume · fixed
            sample snapshot
          </span>
          <Link to="/settings?returnTo=%2Fapp%2Flab">
            Connect your MCAS backend <ArrowUpRight size={14} />
          </Link>
        </div>
      )}
      {paused && !demo && (
        <div className="lab-inline-warning">
          Automatic updates paused. Source ages continue to advance. Use refresh
          for a single update.
        </div>
      )}

      {!['equities', 'health'].includes(view) && (
        <>
          <section className="lab-context" aria-label="Selected market context">
            <div className="lab-context-title">
              <span className="lab-eyebrow">
                {params.get('name') || 'ROOM OVERVIEW'}
              </span>
              <div>
                <strong data-testid="selected-symbol">
                  {symbol || 'Awaiting markets'}
                </strong>
                <span className="lab-badge muted">{tf} / collector</span>
              </div>
            </div>
            <div className="lab-context-metric">
              <span>Snapshot price</span>
              <strong>{price(tile?.close)}</strong>
            </div>
            <div className="lab-context-metric">
              <span>{tf} bar change</span>
              <strong className={tone(tile?.pct_change)}>
                {signed(tile?.pct_change)}
              </strong>
            </div>
            <div className="lab-context-metric">
              <span>Volume Z</span>
              <strong>{format(tile?.volume_z)}</strong>
            </div>
            <div className="lab-breadth">
              <span>
                Returned universe <strong>{rowCount || '—'}</strong>
              </span>
              <div>
                <span className="lab-positive">{upCount} up</span>
                <span className="lab-negative">{downCount} down</span>
              </div>
            </div>
          </section>
          <div className="lab-context-age">
            {demo
              ? 'All displayed values are sample data.'
              : `${tile && (tile.stale || isStale(tile.as_of_ts, getStalenessThresholdS(tf), now)) ? 'STALE · ' : ''}Selected snapshot: ${ageLabel(tile?.as_of_ts, now)} · indicators and scores supplied by MCAS`}
          </div>
        </>
      )}

      {demo && preview.error ? (
        <Empty
          feed={{
            error: preview.error,
            refresh: () => window.location.reload(),
          }}
        />
      ) : (
        <div className={`lab-workspace lab-layout-${view}`}>
          {['command', 'discovery'].includes(view) && (
            <DiscoveryPanel
              feed={heat}
              rows={rows}
              selected={symbol}
              onSelect={choose}
              tf={tf}
              now={now}
              search={search}
              onSearch={setSearch}
              sort={sort}
              onSort={setSort}
            />
          )}
          {['command', 'investigation'].includes(view) && (
            <ChartPanel
              feed={chart}
              symbol={symbol}
              tf={tf}
              timeframes={timeframes}
              onTf={(next) => choose(symbol, next)}
              theme={theme}
              now={now}
              scale={scale}
            />
          )}
          {['command', 'investigation'].includes(view) && (
            <SignalsPanel
              feed={signals}
              symbol={symbol}
              onSelect={choose}
              now={now}
              maxAge={getSignalMaxAgeS()}
            />
          )}
          {['command', 'discovery'].includes(view) && (
            <UniversePanel
              feed={heat}
              rows={field}
              selected={symbol}
              onSelect={choose}
              tf={tf}
              now={now}
            />
          )}
          {['command', 'derivatives'].includes(view) && (
            <DerivativesPanel
              feed={deriv}
              symbol={symbol}
              onSelect={choose}
              now={now}
              compact={view === 'command'}
            />
          )}
          {view === 'investigation' && (
            <EvidencePanel
              symbol={symbol}
              tile={tile}
              signal={selectedSignal}
              derivative={selectedDerivative}
              demo={demo}
            />
          )}
          {view === 'equities' && <EquitiesPanel feed={equities} now={now} />}
          {view === 'health' && (
            <HealthPanel
              feeds={feeds}
              now={now}
              demo={demo}
              baseUrl={config.baseUrl}
              health={connectionStatus.health}
            />
          )}
        </div>
      )}

      <footer className="lab-statusbar">
        <span>
          {demo ? 'SAMPLE ENVIRONMENT' : 'READ-ONLY WORKSPACE'}
          <span className="lab-status-separator">/</span>
          {follow ? 'Room selection linked' : 'This display is pinned'}
        </span>
        <span>
          MCAS · {VIEWS.find((v) => v.id === view)?.label}
          <span className="lab-status-separator">/</span>
          {demo
            ? 'No live connection'
            : `${feeds.filter(({ feed }) => feed.error).length} feed errors`}
        </span>
      </footer>

      <Modal
        isOpen={displayModal}
        onClose={() => setDisplayModal(false)}
        title="Display desk"
        size="xl"
      >
        <div className="lab-display-manager">
          <p>
            Open one view per display, move its window onto the monitor or
            projector, then enter fullscreen. Linked windows share the selected
            pair in this browser.
          </p>
          <div className="lab-display-list">
            {displays.map((display, index) => {
              const edit = (patch) =>
                setDisplays((current) =>
                  current.map((d) =>
                    d.id === display.id ? { ...d, ...patch } : d
                  )
                )
              return (
                <div className="lab-display-row" key={display.id}>
                  <span className="lab-display-number">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <label>
                    Display name
                    <input
                      aria-label={`Display ${index + 1} name`}
                      value={display.name}
                      maxLength={60}
                      onChange={(e) => edit({ name: e.target.value })}
                    />
                  </label>
                  <label>
                    View
                    <select
                      aria-label={`Display ${index + 1} view`}
                      value={display.view}
                      onChange={(e) => edit({ view: e.target.value })}
                    >
                      {VIEWS.map((v) => (
                        <option value={v.id} key={v.id}>
                          {v.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Selection
                    <select
                      aria-label={`Display ${index + 1} selection`}
                      value={display.follow ? 'linked' : 'pinned'}
                      onChange={(e) =>
                        edit({
                          follow: e.target.value === 'linked',
                          symbol: symbol || '',
                        })
                      }
                    >
                      <option value="linked">Follow room</option>
                      <option value="pinned">Pin pair</option>
                    </select>
                  </label>
                  {!display.follow && (
                    <label>
                      Pair
                      <input
                        aria-label={`Display ${index + 1} pair`}
                        value={display.symbol}
                        maxLength={40}
                        onChange={(e) =>
                          edit({
                            symbol: e.target.value
                              .toUpperCase()
                              .replace(/[^A-Z0-9._:/-]/g, ''),
                          })
                        }
                      />
                    </label>
                  )}
                  <a
                    className="lab-open-display"
                    href={screenUrl(
                      {
                        ...display,
                        symbol: display.follow ? '' : display.symbol,
                      },
                      options
                    )}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Open ${display.name}`}
                  >
                    Open <ExternalLink size={14} />
                  </a>
                  <button
                    onClick={() =>
                      setDisplays((current) =>
                        current.filter((d) => d.id !== display.id)
                      )
                    }
                    disabled={displays.length === 1}
                    aria-label={`Remove ${display.name}`}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              )
            })}
          </div>
          <div className="lab-display-footer">
            <button
              onClick={() =>
                setDisplays((current) => [
                  ...current,
                  {
                    id: crypto.randomUUID(),
                    name: `Display ${current.length + 1}`,
                    view: 'investigation',
                    follow: true,
                    symbol: '',
                  },
                ])
              }
            >
              <Plus size={16} /> Add display
            </button>
            <button onClick={save} className="lab-button-accent">
              <Check size={16} /> Save profile
            </button>
          </div>
          <p className="lab-note">
            Saved on this browser. Independent devices need their own sessions;
            Sunshine/Moonlight can carry an already linked PC window. No API key
            is placed in a screen URL.
          </p>
          {feedback && <p role="status">{feedback}</p>}
        </div>
      </Modal>
    </main>
  )
}
