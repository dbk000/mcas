"""
Seed the meta schema with the initial MCAS pipeline topology.

Run:
  docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/seed_pipeline_topology.py

Or locally:
  cd Backend && python3 scripts/seed_pipeline_topology.py
"""

import os
import sys
import json
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Local dev fallback
    DATABASE_URL = "postgresql://trading:trading@localhost:5432/trading"


def seed():
    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()

    # Check if already seeded
    cur.execute("SELECT COUNT(*) FROM meta.node")
    if cur.fetchone()[0] > 0:
        print("Pipeline already seeded. Skipping.")
        conn.close()
        return

    print("Seeding initial pipeline topology...")

    # =========================================================================
    # NODES
    # =========================================================================
    nodes = {}

    def add_node(key, name, node_type, subsystem, description, source_file=None, owner=None):
        cur.execute("""
            INSERT INTO meta.node (name, node_type, subsystem, description, source_file, owner)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING node_id
        """, (name, node_type, subsystem, description, source_file, owner))
        nid = str(cur.fetchone()[0])
        nodes[key] = nid
        return nid

    # -- Ingestion layer
    add_node('ohlcv_feed', 'OHLCV Data Feed', 'data_source', 'ingestion',
             'Fetches OHLCV candles from Binance REST + WebSocket for Known and Monitored universe.',
             'worker/app/ingestor.py')

    add_node('derivatives_feed', 'Derivatives Data Feed', 'data_source', 'ingestion',
             'Collects funding rates, open interest from Binance futures.',
             'worker/app/derivatives_collector.py')

    add_node('social_feed', 'Social Sentiment Feed', 'data_source', 'ingestion',
             'Collects Telegram sentiment signals via social_collector.',
             'social_collector/app/brain1.py')

    # -- Feature engineering
    add_node('indicator_engine', 'Indicator Engine', 'feature_engine', 'feature',
             'Feature Factory: computes ATR, RSI, BBands, Keltner, EMAs, SMAs, slopes, percentiles, volume z-scores. 201-bar warmup.',
             'worker/app/indicators.py')

    add_node('rolling_stats', 'Rolling Statistics', 'feature_engine', 'feature',
             'Computes rolling percentile ranks, momentum, volatility regime features from OHLCV data.',
             'worker/app/indicators.py')

    # -- Signal generation (the 44 high-quality signals from Phase 1 live in setups)
    add_node('s9_setup', 'S9 Short Reversion', 'signal_generator', 'signal',
             'Short-term mean reversion setup evaluator.',
             'worker/app/setups/s9_short_reversion.py')

    add_node('s10_setup', 'S10 MACD Scalp', 'signal_generator', 'signal',
             'MACD crossover scalp setup evaluator.',
             'worker/app/setups/s10_macd_scalp.py')

    add_node('u1_setup', 'U1 Unicorn Ignition', 'signal_generator', 'signal',
             'Unicorn Ignition: volume anomaly + momentum confluence signal generator. The flagship setup.',
             'worker/app/setups/u1_unicorn_ignition.py')

    add_node('s1_setup', 'S1 BB Squeeze (Legacy)', 'signal_generator', 'signal',
             'Bollinger Band Squeeze v2.1 setup evaluator. Legacy status.',
             'worker/app/setups/s1_bb_squeeze.py')

    add_node('s2_setup', 'S2 Golden Cross (Legacy)', 'signal_generator', 'signal',
             'Golden Cross v1.4 with BTC shock filter. Legacy status.',
             'worker/app/setups/s2_golden_cross.py')

    # -- Signal filtering
    add_node('structural_filter', 'Structural Filters', 'signal_filter', 'filtering',
             'Volume regime gate, BB width gate, correlation gate. Filters signals that dont meet structural criteria.',
             'worker/app/signal_engine.py')

    add_node('universe_filter', 'Universe Filter', 'signal_filter', 'filtering',
             'Filters signals to Monitored Universe only. Applies MIN_24H_QUOTE_VOLUME_USD threshold with hysteresis.',
             'worker/app/ingestor.py')

    # -- Strategy assembly
    add_node('u1_strategy', 'U1 Strategy Assembly', 'strategy', 'strategy',
             'Combines U1 signal generator with parameter nodes into a complete trading strategy.',
             'worker/app/unicorn_pipeline.py')

    # -- Parameter nodes
    add_node('tp1_config', 'TP1 Take-Profit', 'parameter', 'parameter',
             'First take-profit target configuration. R-multiple based.',
             'worker/app/opportunity_simulator.py')

    add_node('sl_config', 'Stop-Loss Config', 'parameter', 'parameter',
             'Stop-loss distance and fail-fast configuration.',
             'worker/app/opportunity_simulator.py')

    add_node('trail_config', 'Trailing Stop Config', 'parameter', 'parameter',
             'Trailing stop activation and offset parameters.',
             'worker/app/opportunity_simulator.py')

    add_node('volume_z_threshold', 'Volume Z Threshold', 'parameter', 'parameter',
             'Minimum volume z-score for U1 signal qualification.',
             'worker/app/setups/u1_unicorn_ignition.py')

    add_node('time_stop', 'Time Stop Config', 'parameter', 'parameter',
             'Maximum bars held before time-based exit triggers.',
             'worker/app/opportunity_simulator.py')

    # -- Backtest engine
    add_node('backtest_engine', 'Walk-Forward Validator', 'backtest_engine', 'backtest',
             'Walk-forward validation engine with purged folds, holdout set, DSR correction.',
             'worker/app/opportunity_simulator.py')

    add_node('slippage_model', 'Slippage Model', 'backtest_engine', 'backtest',
             'Timeframe-scaled slippage model: 4h=3bps, 1h=5bps, 15m=10bps, 5m=15bps. Fee: 4bps taker.',
             'worker/app/opportunity_simulator.py')

    # -- Metric aggregator
    add_node('metric_aggregator', 'Metric Aggregator', 'metric_aggregator', 'metrics',
             'Computes PF, win rate, drawdown, R-expectancy, DSR from simulation results.',
             'worker/app/opportunity_simulator.py')

    # -- Risk management
    add_node('risk_manager', 'Risk Manager', 'risk_manager', 'risk',
             'Daily loss circuit breaker (3%), daily trade cap (20), per-asset cooldown (6h), max 3 concurrent positions.',
             'worker/app/paper_trading.py')

    # -- Execution engine
    add_node('execution_engine', 'Execution Engine', 'execution_engine', 'execution',
             'Paper trading engine with persistent state, position management, exit monitoring.',
             'worker/app/paper_trading.py')

    print(f"  Created {len(nodes)} nodes")

    # =========================================================================
    # PORTS (input/output contracts for each node)
    # =========================================================================
    ports = {}

    def add_port(node_key, name, direction, data_type, desc=None):
        nid = nodes[node_key]
        cur.execute("""
            INSERT INTO meta.port (node_id, name, direction, data_type, description)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING port_id
        """, (nid, name, direction, data_type, desc))
        pid = str(cur.fetchone()[0])
        ports[f"{node_key}.{name}"] = pid
        return pid

    # Ingestion outputs
    add_port('ohlcv_feed', 'ohlcv', 'output', 'DataFrame', 'OHLCV bars with is_final flag')
    add_port('derivatives_feed', 'funding_rates', 'output', 'DataFrame', 'Funding rate time series')
    add_port('social_feed', 'sentiment', 'output', 'DataFrame', 'Sentiment scores per symbol')

    # Feature engine
    add_port('indicator_engine', 'ohlcv_in', 'input', 'DataFrame', 'Raw OHLCV bars')
    add_port('indicator_engine', 'features', 'output', 'DataFrame', 'Computed indicator features')

    add_port('rolling_stats', 'ohlcv_in', 'input', 'DataFrame')
    add_port('rolling_stats', 'stats', 'output', 'DataFrame', 'Rolling statistical features')

    # Signal generators
    for sk in ['s9_setup', 's10_setup', 'u1_setup', 's1_setup', 's2_setup']:
        add_port(sk, 'features_in', 'input', 'DataFrame', 'Indicator features')
        add_port(sk, 'signals', 'output', 'Signal[]', 'Qualified setup signals')

    # Filters
    add_port('structural_filter', 'signals_in', 'input', 'Signal[]', 'Raw signals from setup evaluators')
    add_port('structural_filter', 'filtered', 'output', 'Signal[]', 'Structurally qualified signals')
    add_port('universe_filter', 'signals_in', 'input', 'Signal[]')
    add_port('universe_filter', 'filtered', 'output', 'Signal[]', 'Universe-qualified signals')

    # Strategy assembly
    add_port('u1_strategy', 'signals_in', 'input', 'Signal[]', 'Filtered U1 signals')
    add_port('u1_strategy', 'tp1_in', 'input', 'Config', 'TP1 parameters')
    add_port('u1_strategy', 'sl_in', 'input', 'Config', 'Stop-loss parameters')
    add_port('u1_strategy', 'trail_in', 'input', 'Config', 'Trailing stop parameters')
    add_port('u1_strategy', 'vol_z_in', 'input', 'Config', 'Volume Z threshold')
    add_port('u1_strategy', 'time_stop_in', 'input', 'Config', 'Time stop parameters')
    add_port('u1_strategy', 'opportunities', 'output', 'Opportunity[]', 'Scored opportunities')

    # Parameter nodes (output only)
    add_port('tp1_config', 'config', 'output', 'Config')
    add_port('sl_config', 'config', 'output', 'Config')
    add_port('trail_config', 'config', 'output', 'Config')
    add_port('volume_z_threshold', 'config', 'output', 'Config')
    add_port('time_stop', 'config', 'output', 'Config')

    # Backtest engine
    add_port('backtest_engine', 'opportunities_in', 'input', 'Opportunity[]')
    add_port('backtest_engine', 'slippage_in', 'input', 'Config', 'Slippage model config')
    add_port('backtest_engine', 'results', 'output', 'SimResult[]', 'Simulation results')
    add_port('slippage_model', 'config', 'output', 'Config', 'Slippage parameters')

    # Metric aggregator
    add_port('metric_aggregator', 'results_in', 'input', 'SimResult[]')
    add_port('metric_aggregator', 'metrics', 'output', 'Metrics', 'Aggregated performance metrics')

    # Risk manager
    add_port('risk_manager', 'opportunities_in', 'input', 'Opportunity[]')
    add_port('risk_manager', 'approved', 'output', 'Opportunity[]', 'Risk-approved opportunities')

    # Execution engine
    add_port('execution_engine', 'approved_in', 'input', 'Opportunity[]')
    add_port('execution_engine', 'trades', 'output', 'Trade[]', 'Executed trades')

    print(f"  Created {len(ports)} ports")

    # =========================================================================
    # EDGES (wiring the DAG)
    # =========================================================================
    edge_count = 0

    def add_edge(src_key, tgt_key):
        nonlocal edge_count
        src_pid = ports.get(src_key)
        tgt_pid = ports.get(tgt_key)
        if not src_pid or not tgt_pid:
            print(f"  WARNING: Missing port for edge {src_key} -> {tgt_key}")
            return
        cur.execute("""
            INSERT INTO meta.edge (source_port_id, target_port_id)
            VALUES (%s, %s)
        """, (src_pid, tgt_pid))
        edge_count += 1

    # Data flow: ingestion → features
    add_edge('ohlcv_feed.ohlcv', 'indicator_engine.ohlcv_in')
    add_edge('ohlcv_feed.ohlcv', 'rolling_stats.ohlcv_in')

    # Features → signal generators
    for sk in ['s9_setup', 's10_setup', 'u1_setup', 's1_setup', 's2_setup']:
        add_edge('indicator_engine.features', f'{sk}.features_in')

    # Signal generators → structural filter
    for sk in ['s9_setup', 's10_setup', 'u1_setup', 's1_setup', 's2_setup']:
        add_edge(f'{sk}.signals', 'structural_filter.signals_in')

    # Structural filter → universe filter
    add_edge('structural_filter.filtered', 'universe_filter.signals_in')

    # Universe filter → strategy assembly
    add_edge('universe_filter.filtered', 'u1_strategy.signals_in')

    # Parameter nodes → strategy assembly
    add_edge('tp1_config.config', 'u1_strategy.tp1_in')
    add_edge('sl_config.config', 'u1_strategy.sl_in')
    add_edge('trail_config.config', 'u1_strategy.trail_in')
    add_edge('volume_z_threshold.config', 'u1_strategy.vol_z_in')
    add_edge('time_stop.config', 'u1_strategy.time_stop_in')

    # Strategy → backtest
    add_edge('u1_strategy.opportunities', 'backtest_engine.opportunities_in')
    add_edge('slippage_model.config', 'backtest_engine.slippage_in')

    # Backtest → metrics
    add_edge('backtest_engine.results', 'metric_aggregator.results_in')

    # Strategy → risk manager → execution
    add_edge('u1_strategy.opportunities', 'risk_manager.opportunities_in')
    add_edge('risk_manager.approved', 'execution_engine.approved_in')

    print(f"  Created {edge_count} edges")

    # =========================================================================
    # INITIAL VERSIONS for parameter nodes (baseline v1)
    # =========================================================================
    param_versions = {
        'tp1_config': {'tp1_r': 2.0, 'description': 'Default TP1 at 2R'},
        'sl_config': {'sl_r': 1.0, 'fail_fast_bars': 3, 'fail_fast_threshold': 0.3},
        'trail_config': {'trail_activation_r': 1.5, 'trail_offset_r': 0.5},
        'volume_z_threshold': {'volume_z_threshold': 3.0},
        'time_stop': {'time_stop_bars': 12},
    }

    for node_key, params in param_versions.items():
        nid = nodes[node_key]
        cur.execute("""
            INSERT INTO meta.node_version (node_id, version_number, status, parameters, change_reason)
            VALUES (%s, 1, 'deployed', %s, 'Initial baseline configuration')
        """, (nid, json.dumps(params)))

    # Add baseline version for U1 strategy
    cur.execute("""
        INSERT INTO meta.node_version (node_id, version_number, status, parameters, change_reason,
                                       metrics, agg_pf, trade_count)
        VALUES (%s, 1, 'deployed', %s, 'U1 v4.0: WS2 audit-driven refactor',
                %s, 1.49, 454)
    """, (
        nodes['u1_setup'],
        json.dumps({
            'volume_z_threshold': 3.0,
            'bb_width_gate': True,
            'reversion_boost': True,
            'express_lane': True,
        }),
        json.dumps({
            'agg_pf': 1.49,
            'trade_count': 454,
            'win_rate': 0.42,
            'avg_daily_pf': 1.12,
        }),
    ))

    print("  Created baseline versions for parameter nodes and U1")

    # =========================================================================
    # PIPELINE record
    # =========================================================================
    cur.execute("""
        INSERT INTO meta.pipeline (name, description)
        VALUES ('MCAS Crypto Pipeline v1', 'Primary crypto trading pipeline: ingestion → features → signals → strategy → backtest → execution')
        RETURNING pipeline_id
    """)
    pipeline_id = str(cur.fetchone()[0])

    for i, nid in enumerate(nodes.values()):
        cur.execute("""
            INSERT INTO meta.pipeline_node (pipeline_id, node_id, ordinal)
            VALUES (%s, %s, %s)
        """, (pipeline_id, nid, i))

    print(f"  Created pipeline record with {len(nodes)} nodes")

    conn.commit()
    cur.close()
    conn.close()
    print("Seed complete.")


if __name__ == "__main__":
    seed()
