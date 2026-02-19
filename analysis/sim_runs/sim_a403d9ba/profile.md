# Simulation Profile: a403d9ba

Generated: 2026-02-16 20:42:34 UTC

## A. Run Metadata

- **Run ID:** `a403d9ba-2c85-4086-9c71-fa6e82873d1a`
- **Type:** SIMULATION
- **Status:** SUCCESS
- **Name:** Phase5-trail-and-stops
- **Engine:** 1.0.0
- **Created:** 2026-02-16 20:36:41.717290+00:00
- **Started:** 2026-02-16 20:36:41.828617+00:00
- **Completed:** —
- **Duration:** —s
- **Setups:** S1, S2, S3, S6, S7
- **Timeframes:** 15m, 1h, 4h
- **Symbols:** 392
- **Date range:** 2026-02-09 01:30 UTC → 2026-02-16 16:00 UTC
- **Total opportunities (rows):** 1408
- **Simulated (data_available):** 638
- **Skipped (no data / error):** 770

**Run summary_json:**
```json
{
  "total_trades": 638,
  "winning_trades": 221,
  "losing_trades": 417,
  "win_rate": 0.3464,
  "total_r": -187.38,
  "avg_r": -0.2937,
  "max_r": 0.0,
  "min_r": 0.0,
  "gross_pnl": 0.0,
  "total_fees": 0.0,
  "total_slippage": 0.0,
  "net_pnl": -2021.26,
  "max_drawdown_pct": 0.0,
  "profit_factor": 0.481,
  "expectancy": -0.2937,
  "bars_processed": 0,
  "assets_processed": 0,
  "assets_skipped": 0,
  "signals_generated": 1408,
  "execution_time_seconds": 125.84711861610413,
  "starting_capital": 0.0,
  "final_equity": 0.0,
  "equity_curve": [],
  "sharpe_ratio": 0.0,
  "sortino_ratio": 0.0,
  "calmar_ratio": 0.0,
  "max_consecutive_wins": 0,
  "max_consecutive_losses": 0,
  "avg_holding_bars": 0.0,
  "avg_win_r": 0.0,
  "avg_loss_r": 0.0,
  "payoff_ratio": 0.0,
  "return_pct": 0.0,
  "per_setup": {
    "S6": {
      "trades": 483,
      "wins": 165,
      "pnl_net": -1566.6,
      "total_r": -122.61,
      "win_rate": 0.3416149068322981,
      "avg_r": -0.2538604801164923
    },
    "S3": {
      "trades": 16,
      "wins": 3,
      "pnl_net": -114.74,
      "total_r": -7.98,
      "win_rate": 0.1875,
      "avg_r": -0.49903437267649764
    },
    "S7": {
      "trades": 118,
      "wins": 44,
      "pnl_net": -306.81,
      "total_r": -53.88,
      "win_rate": 0.3728813559322034,
      "avg_r": -0.45658348435957924
    },
    "S1": {
      "trades": 8,
      "wins": 3,
      "pnl_net": -16.09,
      "total_r": -2.07,
      "win_rate": 0.375,
      "avg_r": -0.25911656072502776
    },
    "S2": {
      "trades": 13,
      "wins": 6,
      "pnl_net": -17.03,
      "total_r": -0.83,
      "win_rate": 0.46153846153846156,
      "avg_r": -0.06359174377070062
    }
  },
  "per_symbol": {}
}
```

## B. Data Quality

- **Total rows:** 1408
- **Data available (valid):** 638 (45.3%)
- **Data unavailable:** 770 (54.7%)
- **With error messages:** 770 (54.7%)

### Data availability by setup

| Setup | Available | Unavailable | % Available |
|:------|----------:|------------:|------------:|
| S1    |         8 |          19 |       29.6% |
| S2    |        13 |          20 |       39.4% |
| S3    |        16 |          77 |       17.2% |
| S6    |       483 |         467 |       50.8% |
| S7    |       118 |         187 |       38.7% |

### Error taxonomy

| Error | Count | % of Total | Example |
|:------|------:|-----------:|:--------|
| Adverse gap: R degraded 50% (min 50%) |   120 |       8.5% | SPKUSDT/15m/S6 |
| R-value invalid after slippage |    80 |       5.7% | DEXEUSDT/1h/S7 |
| Adverse gap: R degraded 49% (min 50%) |    33 |       2.3% | AXSUSDT/15m/S2 |
| Adverse gap: R degraded 47% (min 50%) |    26 |       1.8% | RENDERUSDT/15m/S6 |
| Adverse gap: R degraded 46% (min 50%) |    17 |       1.2% | NEOUSDT/1h/S6 |
| Adverse gap: R degraded 48% (min 50%) |    16 |       1.1% | FUNUSDT/15m/S6 |
| Adverse gap: R degraded 45% (min 50%) |    15 |       1.1% | FLOWUSDT/15m/S6 |
| Adverse gap: R degraded 41% (min 50%) |    12 |       0.9% | COMPUSDT/15m/S6 |
| Adverse gap: R degraded 34% (min 50%) |    12 |       0.9% | TAOUSDT/1h/S6 |
| Adverse gap: R degraded 31% (min 50%) |    11 |       0.8% | ILVUSDT/15m/S6 |
| Adverse gap: R degraded 40% (min 50%) |    11 |       0.8% | NEWTUSDT/15m/S6 |
| Adverse gap: R degraded 43% (min 50%) |    10 |       0.7% | CYBERUSDT/15m/S6 |
| Adverse gap: R degraded 38% (min 50%) |     9 |       0.6% | LQTYUSDT/15m/S6 |
| Adverse gap: R degraded 36% (min 50%) |     8 |       0.6% | DEXEUSDT/1h/S6 |
| Adverse gap: R degraded 42% (min 50%) |     8 |       0.6% | CITYUSDT/15m/S6 |
| Adverse gap: R degraded 44% (min 50%) |     7 |       0.5% | DEXEUSDT/15m/S6 |
| Adverse gap: R degraded 39% (min 50%) |     6 |       0.4% | STGUSDT/15m/S6 |
| Uneconomic: fees/R=0.41 > max 0.3 |     6 |       0.4% | ICPUSDT/15m/S6 |
| Uneconomic: fees/R=0.42 > max 0.3 |     5 |       0.4% | ETCUSDT/1h/S7 |
| Adverse gap: R degraded 35% (min 50%) |     5 |       0.4% | NMRUSDT/1h/S6 |
| Uneconomic: fees/R=0.47 > max 0.3 |     5 |       0.4% | TRXUSDT/15m/S6 |
| Uneconomic: fees/R=0.49 > max 0.3 |     5 |       0.4% | COSUSDT/15m/S6 |
| Uneconomic: fees/R=0.31 > max 0.3 |     5 |       0.4% | ONGUSDT/15m/S6 |
| Uneconomic: fees/R=3.21 > max 0.3 |     5 |       0.4% | JOEUSDT/15m/S6 |
| Uneconomic: fees/R=0.34 > max 0.3 |     5 |       0.4% | PROVEUSDT/15m/S6 |
| Uneconomic: fees/R=0.39 > max 0.3 |     5 |       0.4% | SANDUSDT/1h/S7 |
| Adverse gap: R degraded 37% (min 50%) |     4 |       0.3% | MANTAUSDT/1h/S3 |
| Adverse gap: R degraded 33% (min 50%) |     4 |       0.3% | XVSUSDT/15m/S3 |
| Uneconomic: fees/R=0.70 > max 0.3 |     4 |       0.3% | BOMEUSDT/1h/S7 |
| Uneconomic: fees/R=0.33 > max 0.3 |     4 |       0.3% | FIOUSDT/1h/S6 |
| Uneconomic: fees/R=0.30 > max 0.3 |     4 |       0.3% | GASUSDT/15m/S6 |
| Uneconomic: fees/R=0.32 > max 0.3 |     4 |       0.3% | THETAUSDT/15m/S6 |
| Uneconomic: fees/R=0.45 > max 0.3 |     3 |       0.2% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=0.90 > max 0.3 |     3 |       0.2% | TAOUSDT/1h/S6 |
| Microcap short blocked: price=0.999700 < min=1.0 |     3 |       0.2% | RLUSDUSDT/1h/S7 |
| Uneconomic: fees/R=0.44 > max 0.3 |     3 |       0.2% | WBTCUSDT/1h/S7 |
| Adverse gap: R degraded 17% (min 50%) |     3 |       0.2% | LSKUSDT/15m/S6 |
| Adverse gap: R degraded 23% (min 50%) |     3 |       0.2% | OPENUSDT/1h/S6 |
| Adverse gap: R degraded 30% (min 50%) |     3 |       0.2% | QTUMUSDT/15m/S6 |
| Adverse gap: R degraded 26% (min 50%) |     3 |       0.2% | TWTUSDT/1h/S6 |
| Uneconomic: fees/R=3.22 > max 0.3 |     3 |       0.2% | DENTUSDT/15m/S6 |
| Uneconomic: fees/R=0.36 > max 0.3 |     3 |       0.2% | NEARUSDT/15m/S6 |
| Microcap short blocked: price=0.999900 < min=1.0 |     2 |       0.1% | RLUSDUSDT/15m/S6 |
| Microcap short blocked: price=0.999300 < min=1.0 |     2 |       0.1% | BFUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.038381 < min=1.0 |     2 |       0.1% | LRCUSDT/15m/S2 |
| Uneconomic: fees/R=0.66 > max 0.3 |     2 |       0.1% | TAOUSDT/1h/S6 |
| Microcap short blocked: price=0.999500 < min=1.0 |     2 |       0.1% | USD1USDT/1h/S7 |
| Microcap short blocked: price=0.025527 < min=1.0 |     2 |       0.1% | GRTUSDT/4h/S7 |
| Microcap short blocked: price=0.045377 < min=1.0 |     2 |       0.1% | PNUTUSDT/1h/S7 |
| Microcap short blocked: price=0.208896 < min=1.0 |     2 |       0.1% | WIFUSDT/4h/S7 |
| Microcap short blocked: price=0.276862 < min=1.0 |     2 |       0.1% | SNXUSDT/1h/S7 |
| Uneconomic: fees/R=0.55 > max 0.3 |     2 |       0.1% | PUNDIXUSDT/15m/S6 |
| Uneconomic: fees/R=0.62 > max 0.3 |     2 |       0.1% | ICPUSDT/15m/S6 |
| Microcap short blocked: price=0.076262 < min=1.0 |     2 |       0.1% | ADXUSDT/1h/S7 |
| Uneconomic: fees/R=1.47 > max 0.3 |     2 |       0.1% | EURUSDT/15m/S6 |
| Adverse gap: R degraded 13% (min 50%) |     2 |       0.1% | PROVEUSDT/1h/S6 |
| Adverse gap: R degraded 29% (min 50%) |     2 |       0.1% | ONGUSDT/15m/S6 |
| Adverse gap: R degraded 22% (min 50%) |     2 |       0.1% | WLDUSDT/1h/S6 |
| Adverse gap: R degraded 32% (min 50%) |     2 |       0.1% | SUSDT/1h/S6 |
| Adverse gap: R degraded 27% (min 50%) |     2 |       0.1% | PARTIUSDT/1h/S6 |
| Uneconomic: fees/R=3.20 > max 0.3 |     2 |       0.1% | HMSTRUSDT/15m/S6 |
| Uneconomic: fees/R=0.71 > max 0.3 |     2 |       0.1% | MDTUSDT/15m/S6 |
| Uneconomic: fees/R=0.54 > max 0.3 |     2 |       0.1% | XTZUSDT/15m/S6 |
| Uneconomic: fees/R=0.35 > max 0.3 |     2 |       0.1% | CTKUSDT/15m/S6 |
| Uneconomic: fees/R=0.46 > max 0.3 |     2 |       0.1% | CVXUSDT/15m/S6 |
| Uneconomic: fees/R=1.17 > max 0.3 |     2 |       0.1% | METUSDT/1h/S6 |
| Uneconomic: fees/R=0.83 > max 0.3 |     1 |       0.1% | EURIUSDT/15m/S6 |
| Microcap short blocked: price=0.026677 < min=1.0 |     1 |       0.1% | SIGNUSDT/15m/S1 |
| Uneconomic: fees/R=2.09 > max 0.3 |     1 |       0.1% | AEURUSDT/15m/S3 |
| Microcap short blocked: price=0.020390 < min=1.0 |     1 |       0.1% | RAREUSDT/15m/S3 |
| Microcap short blocked: price=0.060170 < min=1.0 |     1 |       0.1% | LUNAUSDT/1h/S7 |
| Uneconomic: fees/R=1.87 > max 0.3 |     1 |       0.1% | RLUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.006297 < min=1.0 |     1 |       0.1% | TRUUSDT/1h/S7 |
| Microcap short blocked: price=0.277261 < min=1.0 |     1 |       0.1% | TRXUSDT/1h/S7 |
| Microcap short blocked: price=0.000022 < min=1.0 |     1 |       0.1% | WINUSDT/1h/S7 |
| Microcap short blocked: price=1.000000 < min=1.0 |     1 |       0.1% | USDPUSDT/1h/S7 |
| Microcap short blocked: price=0.039380 < min=1.0 |     1 |       0.1% | ICXUSDT/1h/S7 |
| Microcap short blocked: price=0.079060 < min=1.0 |     1 |       0.1% | REQUSDT/1h/S7 |
| Microcap short blocked: price=0.999800 < min=1.0 |     1 |       0.1% | XUSDUSDT/15m/S6 |
| Uneconomic: fees/R=1.92 > max 0.3 |     1 |       0.1% | EURIUSDT/15m/S6 |
| Uneconomic: fees/R=0.67 > max 0.3 |     1 |       0.1% | ALCXUSDT/15m/S6 |
| Uneconomic: fees/R=0.92 > max 0.3 |     1 |       0.1% | BELUSDT/15m/S3 |
| Uneconomic: fees/R=2.38 > max 0.3 |     1 |       0.1% | USDCUSDT/1h/S7 |
| Microcap short blocked: price=0.009725 < min=1.0 |     1 |       0.1% | ARPAUSDT/1h/S7 |
| Microcap short blocked: price=0.040080 < min=1.0 |     1 |       0.1% | DYMUSDT/1h/S7 |
| Microcap short blocked: price=0.013493 < min=1.0 |     1 |       0.1% | NTRNUSDT/1h/S7 |
| Microcap short blocked: price=0.003088 < min=1.0 |     1 |       0.1% | QKCUSDT/1h/S7 |
| Microcap short blocked: price=0.023088 < min=1.0 |     1 |       0.1% | SXTUSDT/1h/S7 |
| Microcap short blocked: price=0.026537 < min=1.0 |     1 |       0.1% | SIGNUSDT/15m/S1 |
| Microcap short blocked: price=0.452774 < min=1.0 |     1 |       0.1% | ACMUSDT/1h/S3 |
| Microcap short blocked: price=0.026487 < min=1.0 |     1 |       0.1% | AEVOUSDT/1h/S7 |
| Microcap short blocked: price=0.012764 < min=1.0 |     1 |       0.1% | BROCCOLI714USDT/1h/S1 |
| Adverse gap: R degraded 9% (min 50%) |     1 |       0.1% | FTTUSDT/1h/S2 |
| Microcap short blocked: price=0.042079 < min=1.0 |     1 |       0.1% | RIFUSDT/15m/S3 |
| Microcap short blocked: price=0.006997 < min=1.0 |     1 |       0.1% | TUSDT/15m/S3 |
| Microcap short blocked: price=0.309845 < min=1.0 |     1 |       0.1% | DEGOUSDT/1h/S7 |
| Microcap short blocked: price=0.207696 < min=1.0 |     1 |       0.1% | CTKUSDT/1h/S7 |
| Microcap short blocked: price=0.044378 < min=1.0 |     1 |       0.1% | RIFUSDT/15m/S3 |
| Microcap short blocked: price=0.026967 < min=1.0 |     1 |       0.1% | SENTUSDT/1h/S1 |
| Microcap short blocked: price=0.185607 < min=1.0 |     1 |       0.1% | ARKUSDT/1h/S1 |
| Microcap short blocked: price=0.135432 < min=1.0 |     1 |       0.1% | EDUUSDT/1h/S7 |
| Microcap short blocked: price=0.041629 < min=1.0 |     1 |       0.1% | JSTUSDT/1h/S7 |
| Uneconomic: fees/R=0.59 > max 0.3 |     1 |       0.1% | EGLDUSDT/15m/S6 |
| Microcap short blocked: price=0.004348 < min=1.0 |     1 |       0.1% | ANKRUSDT/1h/S7 |
| Microcap short blocked: price=0.007316 < min=1.0 |     1 |       0.1% | ASTRUSDT/1h/S7 |
| Microcap short blocked: price=0.030285 < min=1.0 |     1 |       0.1% | BBUSDT/1h/S7 |
| Microcap short blocked: price=0.038641 < min=1.0 |     1 |       0.1% | CHZUSDT/1h/S7 |
| Microcap short blocked: price=0.204298 < min=1.0 |     1 |       0.1% | CTKUSDT/1h/S7 |
| Microcap short blocked: price=0.526737 < min=1.0 |     1 |       0.1% | CYBERUSDT/1h/S1 |
| Microcap short blocked: price=0.091694 < min=1.0 |     1 |       0.1% | DOGEUSDT/1h/S7 |
| Microcap short blocked: price=0.003908 < min=1.0 |     1 |       0.1% | GALAUSDT/1h/S7 |
| Microcap short blocked: price=0.025187 < min=1.0 |     1 |       0.1% | HOOKUSDT/1h/S7 |
| Microcap short blocked: price=0.028736 < min=1.0 |     1 |       0.1% | KMNOUSDT/1h/S7 |
| Microcap short blocked: price=0.333033 < min=1.0 |     1 |       0.1% | LDOUSDT/1h/S7 |
| Microcap short blocked: price=0.019890 < min=1.0 |     1 |       0.1% | MBOXUSDT/1h/S7 |
| Microcap short blocked: price=0.000654 < min=1.0 |     1 |       0.1% | MEMEUSDT/4h/S7 |
| Microcap short blocked: price=0.123938 < min=1.0 |     1 |       0.1% | MMTUSDT/1h/S7 |
| Microcap short blocked: price=0.977511 < min=1.0 |     1 |       0.1% | NEARUSDT/1h/S7 |
| Microcap short blocked: price=0.043878 < min=1.0 |     1 |       0.1% | OMUSDT/1h/S7 |
| Microcap short blocked: price=0.243978 < min=1.0 |     1 |       0.1% | ONDOUSDT/1h/S7 |
| Microcap short blocked: price=0.000004 < min=1.0 |     1 |       0.1% | PEPEUSDT/1h/S7 |
| Microcap short blocked: price=0.428786 < min=1.0 |     1 |       0.1% | RLCUSDT/1h/S7 |
| Microcap short blocked: price=0.005587 < min=1.0 |     1 |       0.1% | RVNUSDT/1h/S7 |
| Microcap short blocked: price=0.000618 < min=1.0 |     1 |       0.1% | SLPUSDT/1h/S7 |
| Microcap short blocked: price=0.911544 < min=1.0 |     1 |       0.1% | SUIUSDT/1h/S7 |
| Microcap short blocked: price=0.063868 < min=1.0 |     1 |       0.1% | TREEUSDT/1h/S7 |
| Microcap short blocked: price=0.449175 < min=1.0 |     1 |       0.1% | TWTUSDT/4h/S7 |
| Microcap short blocked: price=0.000378 < min=1.0 |     1 |       0.1% | BOMEUSDT/1h/S7 |
| Microcap short blocked: price=0.436782 < min=1.0 |     1 |       0.1% | MASKUSDT/1h/S7 |
| Microcap short blocked: price=0.000667 < min=1.0 |     1 |       0.1% | MEMEUSDT/1h/S7 |
| Microcap short blocked: price=0.264868 < min=1.0 |     1 |       0.1% | MTLUSDT/1h/S7 |
| Microcap short blocked: price=0.000071 < min=1.0 |     1 |       0.1% | NEIROUSDT/1h/S7 |
| Microcap short blocked: price=0.043678 < min=1.0 |     1 |       0.1% | PYTHUSDT/1h/S7 |
| Microcap short blocked: price=0.001641 < min=1.0 |     1 |       0.1% | TLMUSDT/1h/S7 |
| Microcap short blocked: price=0.079860 < min=1.0 |     1 |       0.1% | CELOUSDT/1h/S7 |
| Microcap short blocked: price=0.013893 < min=1.0 |     1 |       0.1% | DODOUSDT/1h/S7 |
| Uneconomic: fees/R=1.01 > max 0.3 |     1 |       0.1% | EURUSDT/15m/S6 |
| Microcap short blocked: price=0.019590 < min=1.0 |     1 |       0.1% | FIDAUSDT/1h/S1 |
| Microcap short blocked: price=0.131934 < min=1.0 |     1 |       0.1% | LSKUSDT/1h/S7 |
| Microcap short blocked: price=0.010685 < min=1.0 |     1 |       0.1% | MDTUSDT/1h/S1 |
| Microcap short blocked: price=0.005987 < min=1.0 |     1 |       0.1% | NOMUSDT/1h/S7 |
| Microcap short blocked: price=0.022489 < min=1.0 |     1 |       0.1% | SXTUSDT/1h/S7 |
| Microcap short blocked: price=0.063668 < min=1.0 |     1 |       0.1% | TREEUSDT/1h/S7 |
| Microcap short blocked: price=0.274563 < min=1.0 |     1 |       0.1% | TRXUSDT/1h/S7 |
| Microcap short blocked: price=0.025887 < min=1.0 |     1 |       0.1% | PHAUSDT/1h/S7 |
| Uneconomic: fees/R=0.95 > max 0.3 |     1 |       0.1% | GNOUSDT/15m/S3 |
| Microcap short blocked: price=0.705647 < min=1.0 |     1 |       0.1% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.005437 < min=1.0 |     1 |       0.1% | IOTXUSDT/1h/S7 |
| Microcap short blocked: price=0.018191 < min=1.0 |     1 |       0.1% | WUSDT/1h/S7 |
| Microcap short blocked: price=0.005407 < min=1.0 |     1 |       0.1% | IOTXUSDT/1h/S7 |
| Uneconomic: fees/R=0.58 > max 0.3 |     1 |       0.1% | SCUSDT/15m/S3 |
| Microcap short blocked: price=0.026287 < min=1.0 |     1 |       0.1% | CHRUSDT/1h/S7 |
| Microcap short blocked: price=0.664468 < min=1.0 |     1 |       0.1% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.150925 < min=1.0 |     1 |       0.1% | XLMUSDT/1h/S7 |
| Microcap short blocked: price=0.181509 < min=1.0 |     1 |       0.1% | YBUSDT/15m/S3 |
| Microcap short blocked: price=0.000000 < min=1.0 |     1 |       0.1% | BTTCUSDT/1h/S7 |
| Microcap short blocked: price=0.072864 < min=1.0 |     1 |       0.1% | ZBTUSDT/15m/S3 |
| Stop too wide: 12.9% > max 10% |     1 |       0.1% | ACEUSDT/1h/S2 |
| Microcap short blocked: price=0.011294 < min=1.0 |     1 |       0.1% | GPSUSDT/1h/S1 |
| Uneconomic: fees/R=0.89 > max 0.3 |     1 |       0.1% | ENJUSDT/1h/S7 |
| Stop too wide: 11.8% > max 10% |     1 |       0.1% | TNSRUSDT/1h/S2 |
| Stop too wide: 20.2% > max 10% |     1 |       0.1% | DYMUSDT/1h/S2 |
| Stop too wide: 20.1% > max 10% |     1 |       0.1% | DYMUSDT/1h/S2 |
| Adverse gap: R degraded 24% (min 50%) |     1 |       0.1% | SOPHUSDT/1h/S6 |
| Microcap short blocked: price=0.188006 < min=1.0 |     1 |       0.1% | OPENUSDT/15m/S3 |
| Uneconomic: fees/R=1.46 > max 0.3 |     1 |       0.1% | SUNUSDT/15m/S6 |
| Microcap short blocked: price=0.277961 < min=1.0 |     1 |       0.1% | TRXUSDT/1h/S3 |
| Uneconomic: fees/R=0.65 > max 0.3 |     1 |       0.1% | ARPAUSDT/15m/S6 |
| Uneconomic: fees/R=0.37 > max 0.3 |     1 |       0.1% | CTKUSDT/15m/S2 |
| Uneconomic: fees/R=0.75 > max 0.3 |     1 |       0.1% | GMXUSDT/15m/S6 |
| Microcap short blocked: price=0.088356 < min=1.0 |     1 |       0.1% | LAYERUSDT/15m/S3 |
| Microcap short blocked: price=0.094553 < min=1.0 |     1 |       0.1% | ZKCUSDT/15m/S3 |
| Microcap short blocked: price=0.002631 < min=1.0 |     1 |       0.1% | CELRUSDT/15m/S3 |
| Uneconomic: fees/R=1.34 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=4.23 > max 0.3 |     1 |       0.1% | ARUSDT/1h/S7 |
| Uneconomic: fees/R=0.43 > max 0.3 |     1 |       0.1% | BARUSDT/1h/S7 |
| Uneconomic: fees/R=0.40 > max 0.3 |     1 |       0.1% | KAIAUSDT/15m/S6 |
| Microcap short blocked: price=0.000387 < min=1.0 |     1 |       0.1% | 1MBABYDOGEUSDT/1h/S3 |
| Microcap short blocked: price=0.999400 < min=1.0 |     1 |       0.1% | USDPUSDT/4h/S7 |
| Microcap short blocked: price=0.596701 < min=1.0 |     1 |       0.1% | RAYUSDT/1h/S3 |
| Microcap short blocked: price=0.184008 < min=1.0 |     1 |       0.1% | KITEUSDT/15m/S3 |
| Microcap short blocked: price=0.161319 < min=1.0 |     1 |       0.1% | XLMUSDT/15m/S3 |
| Microcap short blocked: price=0.112944 < min=1.0 |     1 |       0.1% | ARKMUSDT/1h/S7 |
| Microcap short blocked: price=0.002929 < min=1.0 |     1 |       0.1% | REZUSDT/1h/S7 |
| Microcap short blocked: price=0.386807 < min=1.0 |     1 |       0.1% | RUNEUSDT/1h/S7 |
| Microcap short blocked: price=0.009845 < min=1.0 |     1 |       0.1% | TSTUSDT/1h/S7 |
| Microcap short blocked: price=0.001009 < min=1.0 |     1 |       0.1% | TURBOUSDT/1h/S7 |
| Microcap short blocked: price=0.016392 < min=1.0 |     1 |       0.1% | WOOUSDT/1h/S7 |
| Microcap short blocked: price=0.000362 < min=1.0 |     1 |       0.1% | HOTUSDT/1h/S7 |
| Microcap short blocked: price=0.320839 < min=1.0 |     1 |       0.1% | PYRUSDT/1h/S7 |
| Microcap short blocked: price=0.021789 < min=1.0 |     1 |       0.1% | SXPUSDT/1h/S7 |
| Uneconomic: fees/R=2.78 > max 0.3 |     1 |       0.1% | USDEUSDT/1h/S7 |
| Adverse gap: R degraded 12% (min 50%) |     1 |       0.1% | POLUSDT/1h/S6 |
| Microcap short blocked: price=0.998401 < min=1.0 |     1 |       0.1% | FDUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.022389 < min=1.0 |     1 |       0.1% | SXTUSDT/1h/S7 |
| Microcap short blocked: price=0.001999 < min=1.0 |     1 |       0.1% | ACAUSDT/1h/S7 |
| Microcap short blocked: price=0.734633 < min=1.0 |     1 |       0.1% | ORCAUSDT/1h/S7 |
| Uneconomic: fees/R=0.91 > max 0.3 |     1 |       0.1% | ACHUSDT/15m/S6 |
| Microcap short blocked: price=0.162919 < min=1.0 |     1 |       0.1% | ACEUSDT/1h/S1 |
| Microcap short blocked: price=0.999100 < min=1.0 |     1 |       0.1% | BFUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.090855 < min=1.0 |     1 |       0.1% | LISTAUSDT/1h/S7 |
| Microcap short blocked: price=0.000011 < min=1.0 |     1 |       0.1% | 1000SATSUSDT/1h/S7 |
| Microcap short blocked: price=0.999000 < min=1.0 |     1 |       0.1% | BFUSDUSDT/1h/S7 |
| Uneconomic: fees/R=1.60 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Microcap short blocked: price=0.000006 < min=1.0 |     1 |       0.1% | SHIBUSDT/15m/S1 |
| Microcap short blocked: price=0.101449 < min=1.0 |     1 |       0.1% | WLFIUSDT/1h/S7 |
| Microcap short blocked: price=0.462769 < min=1.0 |     1 |       0.1% | ACMUSDT/15m/S3 |
| Microcap short blocked: price=0.168716 < min=1.0 |     1 |       0.1% | PUNDIXUSDT/15m/S3 |
| Microcap short blocked: price=0.261869 < min=1.0 |     1 |       0.1% | LQTYUSDT/1h/S7 |
| Microcap short blocked: price=0.016172 < min=1.0 |     1 |       0.1% | STRAXUSDT/1h/S3 |
| Microcap short blocked: price=0.027936 < min=1.0 |     1 |       0.1% | HOMEUSDT/15m/S3 |
| Uneconomic: fees/R=0.93 > max 0.3 |     1 |       0.1% | PYTHUSDT/1h/S7 |
| Microcap short blocked: price=0.051174 < min=1.0 |     1 |       0.1% | CFXUSDT/15m/S3 |
| Microcap short blocked: price=0.211894 < min=1.0 |     1 |       0.1% | WIFUSDT/1h/S7 |
| Uneconomic: fees/R=0.85 > max 0.3 |     1 |       0.1% | NOTUSDT/1h/S7 |
| Uneconomic: fees/R=0.96 > max 0.3 |     1 |       0.1% | SUNUSDT/1h/S6 |
| Uneconomic: fees/R=2.29 > max 0.3 |     1 |       0.1% | FDUSDUSDT/15m/S6 |
| Uneconomic: fees/R=1.07 > max 0.3 |     1 |       0.1% | AVAUSDT/1h/S7 |
| Microcap short blocked: price=0.104548 < min=1.0 |     1 |       0.1% | RONINUSDT/15m/S3 |
| Microcap short blocked: price=0.015412 < min=1.0 |     1 |       0.1% | USUALUSDT/15m/S3 |
| Microcap short blocked: price=0.632684 < min=1.0 |     1 |       0.1% | 0GUSDT/15m/S3 |
| Microcap short blocked: price=0.103148 < min=1.0 |     1 |       0.1% | RONINUSDT/15m/S3 |
| Uneconomic: fees/R=3.86 > max 0.3 |     1 |       0.1% | HYPERUSDT/1h/S7 |
| Microcap short blocked: price=0.176812 < min=1.0 |     1 |       0.1% | OPENUSDT/15m/S3 |
| Microcap short blocked: price=0.001623 < min=1.0 |     1 |       0.1% | RSRUSDT/15m/S3 |
| Microcap short blocked: price=0.007516 < min=1.0 |     1 |       0.1% | WAXPUSDT/15m/S3 |
| Microcap short blocked: price=0.263568 < min=1.0 |     1 |       0.1% | ONDOUSDT/1h/S3 |
| Uneconomic: fees/R=0.97 > max 0.3 |     1 |       0.1% | CELOUSDT/1h/S6 |
| Adverse gap: R degraded 25% (min 50%) |     1 |       0.1% | ENSUSDT/1h/S6 |
| Uneconomic: fees/R=1.83 > max 0.3 |     1 |       0.1% | HAEDALUSDT/1h/S7 |
| Uneconomic: fees/R=0.61 > max 0.3 |     1 |       0.1% | REDUSDT/1h/S2 |
| Uneconomic: fees/R=0.98 > max 0.3 |     1 |       0.1% | XTZUSDT/1h/S7 |
| Microcap short blocked: price=0.156922 < min=1.0 |     1 |       0.1% | STGUSDT/1h/S7 |
| Uneconomic: fees/R=1.28 > max 0.3 |     1 |       0.1% | WBTCUSDT/15m/S6 |
| Uneconomic: fees/R=0.51 > max 0.3 |     1 |       0.1% | MDTUSDT/15m/S6 |
| Microcap short blocked: price=0.073863 < min=1.0 |     1 |       0.1% | HIVEUSDT/15m/S3 |
| Uneconomic: fees/R=0.48 > max 0.3 |     1 |       0.1% | TLMUSDT/15m/S6 |
| Uneconomic: fees/R=1.74 > max 0.3 |     1 |       0.1% | EURUSDT/15m/S6 |
| Uneconomic: fees/R=2.59 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=1.65 > max 0.3 |     1 |       0.1% | ILVUSDT/1h/S7 |
| Uneconomic: fees/R=1.50 > max 0.3 |     1 |       0.1% | PUNDIXUSDT/1h/S6 |
| Adverse gap: R degraded 15% (min 50%) |     1 |       0.1% | QNTUSDT/1h/S6 |
| Uneconomic: fees/R=1.94 > max 0.3 |     1 |       0.1% | POLYXUSDT/1h/S7 |
| Uneconomic: fees/R=1.52 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Microcap short blocked: price=0.042419 < min=1.0 |     1 |       0.1% | JSTUSDT/15m/S3 |
| Uneconomic: fees/R=0.80 > max 0.3 |     1 |       0.1% | SLPUSDT/15m/S6 |
| Microcap short blocked: price=0.009025 < min=1.0 |     1 |       0.1% | ALTUSDT/1h/S3 |
| Microcap short blocked: price=0.281259 < min=1.0 |     1 |       0.1% | ONDOUSDT/15m/S3 |
| Microcap short blocked: price=0.993203 < min=1.0 |     1 |       0.1% | SUIUSDT/15m/S3 |
| Microcap short blocked: price=0.514742 < min=1.0 |     1 |       0.1% | UMAUSDT/15m/S3 |
| Microcap short blocked: price=0.998600 < min=1.0 |     1 |       0.1% | FDUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.980510 < min=1.0 |     1 |       0.1% | SUIUSDT/15m/S3 |
| Microcap short blocked: price=0.022879 < min=1.0 |     1 |       0.1% | CGPTUSDT/15m/S3 |
| Microcap short blocked: price=0.226287 < min=1.0 |     1 |       0.1% | METUSDT/15m/S3 |
| Microcap short blocked: price=0.109845 < min=1.0 |     1 |       0.1% | DYDXUSDT/15m/S3 |
| Uneconomic: fees/R=0.72 > max 0.3 |     1 |       0.1% | ALICEUSDT/15m/S6 |
| Microcap short blocked: price=0.071364 < min=1.0 |     1 |       0.1% | INITUSDT/15m/S3 |
| Uneconomic: fees/R=0.81 > max 0.3 |     1 |       0.1% | CVXUSDT/15m/S6 |
| Microcap short blocked: price=0.015302 < min=1.0 |     1 |       0.1% | PORTALUSDT/15m/S3 |
| Microcap short blocked: price=0.000454 < min=1.0 |     1 |       0.1% | BOMEUSDT/15m/S3 |
| Uneconomic: fees/R=1.10 > max 0.3 |     1 |       0.1% | PORTOUSDT/15m/S6 |
| Microcap short blocked: price=0.107846 < min=1.0 |     1 |       0.1% | MANAUSDT/15m/S3 |
| Microcap short blocked: price=0.198001 < min=1.0 |     1 |       0.1% | OPUSDT/1h/S3 |
| Microcap short blocked: price=0.430485 < min=1.0 |     1 |       0.1% | WLDUSDT/1h/S3 |
| Microcap short blocked: price=0.001132 < min=1.0 |     1 |       0.1% | COSUSDT/15m/S3 |
| Microcap short blocked: price=0.222289 < min=1.0 |     1 |       0.1% | CTKUSDT/15m/S3 |
| Microcap short blocked: price=0.661669 < min=1.0 |     1 |       0.1% | RAYUSDT/15m/S3 |
| Microcap short blocked: price=0.070765 < min=1.0 |     1 |       0.1% | POWRUSDT/15m/S3 |
| Microcap short blocked: price=0.016192 < min=1.0 |     1 |       0.1% | ACTUSDT/15m/S3 |
| Microcap short blocked: price=0.015352 < min=1.0 |     1 |       0.1% | PORTALUSDT/15m/S3 |
| Microcap short blocked: price=0.000031 < min=1.0 |     1 |       0.1% | DOGSUSDT/15m/S3 |
| Microcap short blocked: price=0.000005 < min=1.0 |     1 |       0.1% | PEPEUSDT/15m/S3 |
| Microcap short blocked: price=0.423188 < min=1.0 |     1 |       0.1% | XTZUSDT/1h/S3 |
| Microcap short blocked: price=0.110635 < min=1.0 |     1 |       0.1% | DOGEUSDT/15m/S3 |
| Uneconomic: fees/R=0.52 > max 0.3 |     1 |       0.1% | WBETHUSDT/15m/S6 |
| Microcap short blocked: price=0.032684 < min=1.0 |     1 |       0.1% | LRCUSDT/1h/S7 |
| Microcap short blocked: price=0.021939 < min=1.0 |     1 |       0.1% | ZAMAUSDT/15m/S3 |
| Uneconomic: fees/R=0.56 > max 0.3 |     1 |       0.1% | HAEDALUSDT/1h/S7 |
| Microcap short blocked: price=0.014383 < min=1.0 |     1 |       0.1% | BABYUSDT/15m/S3 |
| Microcap short blocked: price=0.098251 < min=1.0 |     1 |       0.1% | BELUSDT/15m/S1 |
| Microcap short blocked: price=0.025587 < min=1.0 |     1 |       0.1% | PHAUSDT/1h/S7 |
| Adverse gap: R degraded 21% (min 50%) |     1 |       0.1% | VIRTUALUSDT/15m/S6 |
| Stop too wide: 10.1% > max 10% |     1 |       0.1% | UMAUSDT/15m/S2 |
| Microcap short blocked: price=0.017071 < min=1.0 |     1 |       0.1% | SUNUSDT/15m/S2 |
| Microcap short blocked: price=0.141429 < min=1.0 |     1 |       0.1% | STGUSDT/1h/S7 |
| Microcap short blocked: price=0.203898 < min=1.0 |     1 |       0.1% | THETAUSDT/1h/S7 |
| Microcap short blocked: price=0.021289 < min=1.0 |     1 |       0.1% | AIXBTUSDT/15m/S3 |

## C. Gate Impact

Gate rejections are identified from `error_message` on trades where `data_available = False`.

### Gate rejection summary

**Adverse gap** — 362 trades (25.7%)

- Cannot estimate — payload missing: entry_price

**Microcap short blocked** — 183 trades (13.0%)

- Cannot estimate — payload missing: entry_price, stop_price, r_value

**Uneconomic (fees/R ratio)** — 140 trades (9.9%)

- Cannot estimate — payload missing: entry_price

**R-value invalid after slippage** — 80 trades (5.7%)

- Cannot estimate — payload missing: entry_price

**Stop too wide** — 5 trades (0.4%)

- Cannot estimate — payload missing: entry_price, stop_price, r_value

*To quantify actual gate impact, re-run the simulation with each gate disabled and compare outcomes.*

### TP Invalidation (post-entry gate)

- **TP invalidated:** 112 / 638 (17.6%)
- **Avg R of TP-invalidated trades:** -0.12
- **Win rate of TP-invalidated trades:** 31.2%

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |    91 |
| BELOW_COST |    21 |

## D. Segmentation

### Overall

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| ALL                      |    638 |   34.6% |     -2021.26 |      -3.17 |    0.48 |   -0.29 |     -0.35 |       -56.87 |       1.8 |

### D1. By Setup

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1                       |      8 |   37.5% |       -16.09 |      -2.01 |    0.56 |   -0.26 |     -0.18 |       -20.75 |       1.9 |
| S2                       |     13 |   46.2% |       -17.03 |      -1.31 |    0.83 |   -0.06 |     -0.11 |       -45.43 |       2.2 |
| S3                       |     16 |   18.8% |      -114.74 |      -7.17 |    0.11 |   -0.50 |     -0.61 |       -18.44 |       1.2 |
| S6                       |    483 |   34.2% |     -1566.60 |      -3.24 |    0.48 |   -0.25 |     -0.29 |       -56.87 |       1.9 |
| S7                       |    118 |   37.3% |      -306.81 |      -2.60 |    0.50 |   -0.46 |     -0.46 |       -40.81 |       1.4 |

### D2. By Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| long                     |    560 |   35.2% |     -1749.79 |      -3.12 |    0.50 |   -0.29 |     -0.36 |       -56.87 |       1.7 |
| short                    |     78 |   30.8% |      -271.47 |      -3.48 |    0.35 |   -0.30 |     -0.26 |       -43.36 |       2.1 |

### D3. By Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 15m                      |    352 |   32.4% |     -1085.38 |      -3.08 |    0.48 |   -0.27 |     -0.39 |       -45.43 |       1.7 |
| 1h                       |    271 |   37.6% |      -869.58 |      -3.21 |    0.48 |   -0.33 |     -0.33 |       -56.87 |       1.9 |
| 4h                       |     15 |   33.3% |       -66.30 |      -4.42 |    0.37 |   -0.10 |     -0.05 |       -40.81 |       1.7 |

### D4. By Exit Reason

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| FAIL_FAST                |    170 |    0.0% |     -1609.09 |      -9.47 |    0.00 |   -0.57 |     -0.56 |       -45.43 |       1.2 |
| STOP_LOSS                |    160 |    0.0% |     -2066.41 |     -12.92 |    0.00 |   -1.46 |     -1.20 |       -56.87 |       1.5 |
| TIME_STOP                |     10 |   50.0% |         7.00 |       0.70 |    1.39 |   -0.06 |     -0.02 |        -5.02 |       8.4 |
| TP1                      |    184 |  100.0% |      1413.24 |       7.68 |    0.00 |    0.65 |      0.74 |         0.05 |       1.7 |
| TP2                      |     10 |  100.0% |        25.02 |       2.50 |    0.00 |    0.15 |      0.11 |         0.31 |       2.4 |
| TRAIL_STOP               |    104 |   21.2% |       208.98 |       2.01 |    2.06 |    0.22 |     -0.13 |       -27.71 |       2.5 |

### D5. By Setup × Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / long                |      5 |   20.0% |       -22.29 |      -4.46 |    0.30 |   -0.57 |     -1.16 |       -20.75 |       1.4 |
| S1 / short               |      3 |   66.7% |         6.20 |       2.07 |    2.27 |    0.25 |      0.32 |        -4.87 |       2.7 |
| S2 / long                |     13 |   46.2% |       -17.03 |      -1.31 |    0.83 |   -0.06 |     -0.11 |       -45.43 |       2.2 |
| S3 / long                |     13 |   23.1% |       -88.89 |      -6.84 |    0.14 |   -0.46 |     -0.53 |       -18.44 |       1.0 |
| S3 / short               |      3 |    0.0% |       -25.85 |      -8.62 |    0.00 |   -0.66 |     -0.75 |       -10.94 |       2.0 |
| S6 / long                |    425 |   35.8% |     -1336.21 |      -3.14 |    0.50 |   -0.24 |     -0.28 |       -56.87 |       1.8 |
| S6 / short               |     58 |   22.4% |      -230.39 |      -3.97 |    0.34 |   -0.35 |     -0.37 |       -43.36 |       2.2 |
| S7 / long                |    104 |   33.7% |      -285.37 |      -2.74 |    0.51 |   -0.50 |     -0.51 |       -40.81 |       1.3 |
| S7 / short               |     14 |   64.3% |       -21.44 |      -1.53 |    0.43 |   -0.13 |      0.22 |       -13.79 |       1.6 |

### D6. By Setup × Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / 15m                 |      3 |   33.3% |        -0.24 |      -0.08 |    0.98 |   -0.52 |     -1.16 |        -5.74 |       1.7 |
| S1 / 1h                  |      5 |   40.0% |       -15.85 |      -3.17 |    0.41 |   -0.10 |     -0.07 |       -20.75 |       2.0 |
| S2 / 15m                 |      7 |   57.1% |        -1.90 |      -0.27 |    0.97 |    0.06 |      0.31 |       -45.43 |       1.7 |
| S2 / 1h                  |      5 |   40.0% |        -6.04 |      -1.21 |    0.76 |   -0.23 |     -0.71 |       -15.45 |       3.0 |
| S2 / 4h                  |      1 |    0.0% |        -9.09 |      -9.09 |    0.00 |   -0.11 |     -0.11 |        -9.09 |       1.0 |
| S3 / 15m                 |     10 |   30.0% |       -47.66 |      -4.77 |    0.24 |   -0.43 |     -0.62 |       -11.89 |       1.3 |
| S3 / 1h                  |      6 |    0.0% |       -67.08 |     -11.18 |    0.00 |   -0.61 |     -0.61 |       -18.44 |       1.0 |
| S6 / 15m                 |    332 |   31.9% |     -1035.58 |      -3.12 |    0.47 |   -0.27 |     -0.38 |       -44.66 |       1.7 |
| S6 / 1h                  |    151 |   39.1% |      -531.02 |      -3.52 |    0.49 |   -0.22 |     -0.19 |       -56.87 |       2.2 |
| S7 / 1h                  |    104 |   37.5% |      -249.59 |      -2.40 |    0.52 |   -0.50 |     -0.50 |       -28.45 |       1.3 |
| S7 / 4h                  |     14 |   35.7% |       -57.21 |      -4.09 |    0.41 |   -0.10 |     -0.05 |       -40.81 |       1.8 |

### D7. By Date (entry_ts)

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 2026-02-09               |     27 |   40.7% |       -71.90 |      -2.66 |    0.56 |   -0.06 |     -0.20 |       -34.35 |       1.9 |
| 2026-02-10               |     38 |   23.7% |      -245.81 |      -6.47 |    0.22 |   -0.36 |     -0.50 |       -28.45 |       1.7 |
| 2026-02-11               |     69 |   39.1% |      -263.16 |      -3.81 |    0.51 |   -0.26 |     -0.21 |       -56.87 |       1.5 |
| 2026-02-12               |     69 |   24.6% |      -371.01 |      -5.38 |    0.22 |   -0.51 |     -0.58 |       -36.33 |       1.5 |
| 2026-02-13               |    134 |   44.8% |       -78.98 |      -0.59 |    0.87 |   -0.10 |     -0.14 |       -44.66 |       2.1 |
| 2026-02-14               |    275 |   32.4% |      -820.73 |      -2.98 |    0.46 |   -0.29 |     -0.43 |       -42.45 |       1.7 |
| 2026-02-16               |     26 |   30.8% |      -169.67 |      -6.53 |    0.32 |   -1.02 |     -0.81 |       -29.19 |       1.6 |

### D8. By Hour of Day (entry_ts UTC)

| Hour  | Trades | Win Rate | Avg PnL    |
|:------|-------:|---------:|-----------:|
| 00:00 |     18 |    38.9% |      -0.66 |
| 01:00 |     32 |    21.9% |      -8.20 |
| 02:00 |      6 |    33.3% |      -0.33 |
| 03:00 |     19 |    31.6% |      -5.92 |
| 04:00 |     11 |    36.4% |      -0.16 |
| 05:00 |     14 |    57.1% |      -1.28 |
| 06:00 |     43 |    39.5% |      -4.50 |
| 07:00 |     21 |     9.5% |      -8.24 |
| 08:00 |     33 |    39.4% |       0.47 |
| 09:00 |     55 |    32.7% |      -1.54 |
| 10:00 |     38 |    26.3% |      -6.69 |
| 11:00 |     25 |    16.0% |      -7.95 |
| 12:00 |     28 |    28.6% |      -4.73 |
| 13:00 |     13 |    15.4% |     -10.99 |
| 14:00 |     21 |    52.4% |      -1.05 |
| 15:00 |     35 |    37.1% |      -0.96 |
| 16:00 |     66 |    40.9% |      -0.27 |
| 17:00 |     52 |    46.2% |      -1.46 |
| 18:00 |     11 |    45.5% |      -4.80 |
| 19:00 |     15 |    33.3% |      -0.61 |
| 20:00 |     23 |    30.4% |      -3.80 |
| 21:00 |     22 |    36.4% |      -5.29 |
| 22:00 |     29 |    41.4% |       1.05 |
| 23:00 |      8 |    12.5% |      -7.99 |

### D9. By Day of Week (entry_ts)

| Day | Trades | Win Rate | Avg PnL    |
|:----|-------:|---------:|-----------:|
| Mon |     53 |    35.8% |      -4.56 |
| Tue |     38 |    23.7% |      -6.47 |
| Wed |     69 |    39.1% |      -3.81 |
| Thu |     69 |    24.6% |      -5.38 |
| Fri |    134 |    44.8% |      -0.59 |
| Sat |    275 |    32.4% |      -2.98 |

## E. Exit Mechanics

### E1. Exit Reason Distribution

| Exit Reason  | Count | % of Total |
|:-------------|------:|-----------:|
| TP1          |   184 |      28.8% |
| FAIL_FAST    |   170 |      26.6% |
| STOP_LOSS    |   160 |      25.1% |
| TRAIL_STOP   |   104 |      16.3% |
| TIME_STOP    |    10 |       1.6% |
| TP2          |    10 |       1.6% |

### E2. Exit Reason × Setup

| Exit Reason  |    S1 |    S2 |    S3 |    S6 |    S7 | Total |
|:-------------|------:|------:|------:|------:|------:|------:|
| TP1          |     2 |     5 |     3 |   149 |    25 |   184 |
| FAIL_FAST    |     1 |     4 |     9 |   132 |    24 |   170 |
| STOP_LOSS    |     3 |     2 |     3 |   112 |    40 |   160 |
| TRAIL_STOP   |     1 |     2 |     1 |    76 |    24 |   104 |
| TIME_STOP    |     1 |     0 |     0 |     8 |     1 |    10 |
| TP2          |     0 |     0 |     0 |     6 |     4 |    10 |

### E3. Exit Reason × Timeframe

| Exit Reason  |  15m |   1h |   4h | Total |
|:-------------|-----:|-----:|-----:|------:|
| TP1          |  109 |   72 |    3 |   184 |
| FAIL_FAST    |   87 |   77 |    6 |   170 |
| STOP_LOSS    |   89 |   70 |    1 |   160 |
| TRAIL_STOP   |   64 |   36 |    4 |   104 |
| TIME_STOP    |    3 |    7 |    0 |    10 |
| TP2          |    0 |    9 |    1 |    10 |

### E4. Bars Held Distribution (all exits)

| Percentile | Bars |
|-----------:|-----:|
| 5th |    1 |
| 10th |    1 |
| 25th |    1 |
| 50th |    1 |
| 75th |    2 |
| 90th |    4 |
| 95th |    6 |
| Mean |  1.8 |

### E5. Bars Held by Exit Reason

| Exit Reason  | Count | Mean  | P25 | Median | P75 | Max |
|:-------------|------:|------:|----:|-------:|----:|----:|
| FAIL_FAST    |   170 |   1.2 |   1 |      1 |   1 |   2 |
| STOP_LOSS    |   160 |   1.5 |   1 |      1 |   1 |   9 |
| TIME_STOP    |    10 |   8.4 |   9 |      9 |   9 |   9 |
| TP1          |   184 |   1.7 |   1 |      1 |   1 |  12 |
| TP2          |    10 |   2.4 |   1 |      1 |   3 |   9 |
| TRAIL_STOP   |   104 |   2.5 |   1 |      1 |   3 |  15 |

### E6. STOP_LOSS Bars Held Detail

- Count: 160, P25: 1, Median: 1, P75: 1, Max: 9
- **Immediate stops (bars ≤ 1):** 133 (83.1%)

### E7. TP1 Bars Held Detail

- Count: 184, P25: 1, Median: 1, P75: 1, Max: 12

### E7. TP2 Bars Held Detail

- Count: 10, P25: 1, Median: 1, P75: 3, Max: 9

### E8. TP Invalidation

- **TP invalidated:** 112 / 638 (17.6%)

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |    91 |
| BELOW_COST |    21 |

## F. Outlier Flags

### F1. Top 10 Winners (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | MAGICUSDT  | 15m | S2    | long  |      1.09 |     46.20 | TRAIL_STOP   |    5 |
| 2 | BANANAS31USDT | 1h  | S7    | long  |     13.03 |     45.68 | TRAIL_STOP   |    1 |
| 3 | AWEUSDT    | 1h  | S7    | long  |      5.75 |     41.97 | TRAIL_STOP   |    1 |
| 4 | NEARUSDT   | 1h  | S6    | long  |      3.25 |     41.37 | TRAIL_STOP   |   15 |
| 5 | MEUSDT     | 1h  | S7    | long  |      1.22 |     39.55 | TRAIL_STOP   |    3 |
| 6 | EULUSDT    | 15m | S6    | long  |      0.97 |     38.81 | TP1          |    6 |
| 7 | VIRTUALUSDT | 1h  | S7    | long  |      3.50 |     36.29 | TRAIL_STOP   |    2 |
| 8 | SUSDT      | 15m | S6    | long  |      0.93 |     31.79 | TP1          |    2 |
| 9 | WIFUSDT    | 4h  | S7    | long  |      0.75 |     31.77 | TRAIL_STOP   |    3 |
| 10 | TNSRUSDT   | 15m | S6    | long  |      0.96 |     29.08 | TP1          |    1 |

### F2. Top 10 Losers (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | OPENUSDT   | 1h  | S6    | long  |     -1.66 |    -56.87 | STOP_LOSS    |    1 |
| 2 | ACEUSDT    | 15m | S2    | long  |     -0.46 |    -45.43 | FAIL_FAST    |    1 |
| 3 | ARKUSDT    | 15m | S6    | long  |     -3.05 |    -44.66 | STOP_LOSS    |    1 |
| 4 | ENSOUSDT   | 1h  | S6    | short |     -3.06 |    -43.36 | STOP_LOSS    |    1 |
| 5 | HBARUSDT   | 15m | S6    | long  |     -1.02 |    -42.45 | STOP_LOSS    |    1 |
| 6 | ARDRUSDT   | 15m | S6    | long  |     -1.87 |    -42.22 | STOP_LOSS    |    1 |
| 7 | WIFUSDT    | 4h  | S7    | long  |     -0.72 |    -40.81 | FAIL_FAST    |    1 |
| 8 | MUBARAKUSDT | 15m | S6    | long  |     -1.02 |    -36.54 | STOP_LOSS    |    1 |
| 9 | ZBTUSDT    | 15m | S6    | long  |     -1.02 |    -36.33 | STOP_LOSS    |    1 |
| 10 | FLOWUSDT   | 15m | S6    | long  |     -1.02 |    -34.35 | STOP_LOSS    |    1 |

### F3. Instant Exits (bars_held ≤ 1)

- **Count:** 473 / 638 (74.1%)

| Exit Reason  | Count | % of Instant |
|:-------------|------:|-------------:|
| FAIL_FAST    |   140 |        29.6% |
| TP1          |   139 |        29.4% |
| STOP_LOSS    |   133 |        28.1% |
| TRAIL_STOP   |    54 |        11.4% |
| TP2          |     7 |         1.5% |

| Setup | Count | % of Instant |
|:------|------:|-------------:|
| S6    |   348 |        73.6% |
| S7    |    98 |        20.7% |
| S3    |    14 |         3.0% |
| S2    |     9 |         1.9% |
| S1    |     4 |         0.8% |

### F4. PnL / Exit Reason Disagreement

No disagreements found (all STOP_LOSS/FAIL_FAST have negative PnL, all TP1/TP2 have positive PnL).

### F5. Top 10 Longest Held

| # | Symbol     | TF  | Setup | Dir   | Bars | R Multiple | Exit Reason  |
|--:|:-----------|:----|:------|:------|-----:|----------:|:-------------|
| 1 | NEARUSDT   | 1h  | S6    | long  |   15 |      3.25 | TRAIL_STOP   |
| 2 | MEMEUSDT   | 1h  | S6    | long  |   15 |     -0.19 | TRAIL_STOP   |
| 3 | RUNEUSDT   | 15m | S6    | long  |   12 |      0.71 | TP1          |
| 4 | WOOUSDT    | 1h  | S6    | long  |   11 |      1.04 | TRAIL_STOP   |
| 5 | ENSUSDT    | 15m | S6    | long  |    9 |     -1.05 | STOP_LOSS    |
| 6 | SOLUSDT    | 1h  | S6    | long  |    9 |      0.19 | TIME_STOP    |
| 7 | WUSDT      | 1h  | S7    | long  |    9 |      0.84 | TRAIL_STOP   |
| 8 | XPLUSDT    | 1h  | S6    | long  |    9 |      0.14 | TIME_STOP    |
| 9 | GNOUSDT    | 1h  | S6    | long  |    9 |     -0.09 | TIME_STOP    |
| 10 | PENGUUSDT  | 1h  | S6    | long  |    9 |      0.32 | TP2          |

### F6. Score Anomalies

Score Q25=0.778, Q75=0.904

**High-score losers (score ≥ Q75, R < 0): 97**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| INJUSDT    | S7    |  0.912 |     -3.18 | STOP_LOSS    |
| ZENUSDT    | S6    |  0.960 |     -3.11 | STOP_LOSS    |
| ENSOUSDT   | S6    |  0.915 |     -3.06 | STOP_LOSS    |
| GTCUSDT    | S7    |  0.958 |     -2.93 | STOP_LOSS    |
| OXTUSDT    | S6    |  0.911 |     -2.27 | STOP_LOSS    |
| MEMEUSDT   | S7    |  1.000 |     -1.90 | STOP_LOSS    |
| BANANAUSDT | S6    |  0.904 |     -1.63 | STOP_LOSS    |
| SKYUSDT    | S7    |  0.986 |     -1.46 | STOP_LOSS    |
| LAZIOUSDT  | S6    |  0.919 |     -1.39 | STOP_LOSS    |
| JSTUSDT    | S6    |  0.980 |     -1.37 | STOP_LOSS    |

**Low-score winners (score ≤ Q25, R > 0): 53**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| BANANAS31USDT | S7    |  0.543 |     13.03 | TRAIL_STOP   |
| NEARUSDT   | S6    |  0.601 |      3.25 | TRAIL_STOP   |
| ZENUSDT    | S7    |  0.682 |      1.77 | TRAIL_STOP   |
| MAGICUSDT  | S2    |  0.689 |      1.09 | TRAIL_STOP   |
| MOVEUSDT   | S7    |  0.676 |      0.94 | TRAIL_STOP   |
| ACMUSDT    | S6    |  0.760 |      0.94 | TP1          |
| BANKUSDT   | S6    |  0.752 |      0.93 | TP1          |
| POLYXUSDT  | S2    |  0.462 |      0.91 | TP1          |
| DYMUSDT    | S2    |  0.354 |      0.91 | TP1          |
| AAVEUSDT   | S6    |  0.771 |      0.89 | TP1          |

## G. Feature Coverage

Comparison of trace_json keys found vs what `FeatureExtractor` captures.

- **Traces with data:** 638 / 1408
- **Total feature columns:** 131

| trace_json key | In FeatureExtractor? | In features.csv? | Action taken |
|:---------------|:---------------------|:-----------------|:-------------|
| cond.why_0                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_1                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_2                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_3                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_4                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_5                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_6                               (79.8%) | No                   | Yes              | Extracted by profiler |
| ind.atr                                  (100.0%) | Yes                  | Yes              | Already captured |
| ind.bb_lb                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.bb_mb                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.bb_ub                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.bb_width                             (100.0%) | Yes                  | Yes              | Already captured |
| ind.clv                                  (100.0%) | Yes                  | Yes              | Already captured |
| ind.donchian_high_20                     (100.0%) | Yes                  | Yes              | Already captured |
| ind.donchian_low_20                      (100.0%) | Yes                  | Yes              | Already captured |
| ind.donchian_mid_20                      (100.0%) | Yes                  | Yes              | Already captured |
| ind.ema20_slope_atr                      (100.0%) | Yes                  | Yes              | Already captured |
| ind.ema50_slope_atr                      (100.0%) | Yes                  | Yes              | Already captured |
| ind.ema_20                               (100.0%) | Yes                  | Yes              | Already captured |
| ind.ema_21                               (100.0%) | Yes                  | Yes              | Already captured |
| ind.ema_50                               (100.0%) | Yes                  | Yes              | Already captured |
| ind.ema_9                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.htf_bb_mb                            (97.6%) | Yes                  | Yes              | Already captured |
| ind.htf_close                            (97.6%) | Yes                  | Yes              | Already captured |
| ind.htf_sma50                            (97.6%) | Yes                  | Yes              | Already captured |
| ind.kc_lb                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.kc_mb                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.kc_ub                                (100.0%) | Yes                  | Yes              | Already captured |
| ind.ma_gap_atr                           (100.0%) | Yes                  | Yes              | Already captured |
| ind.rsi                                  (100.0%) | Yes                  | Yes              | Already captured |
| ind.rsi_slope                            (100.0%) | Yes                  | Yes              | Already captured |
| ind.rsi_sma                              (100.0%) | Yes                  | Yes              | Already captured |
| ind.sma200_slope_atr                     (100.0%) | Yes                  | Yes              | Already captured |
| ind.sma50_slope_atr                      (100.0%) | Yes                  | Yes              | Already captured |
| ind.sma_200                              (100.0%) | Yes                  | Yes              | Already captured |
| ind.sma_50                               (100.0%) | Yes                  | Yes              | Already captured |
| ind.volume_z                             (100.0%) | Yes                  | Yes              | Already captured |
| ind.vwap                                 (98.3%) | Yes                  | Yes              | Already captured |
| ind.vwap_distance_atr                    (98.3%) | Yes                  | Yes              | Already captured |
| ind.vwap_lower_2sd                       (98.3%) | Yes                  | Yes              | Already captured |
| ind.vwap_upper_2sd                       (98.3%) | Yes                  | Yes              | Already captured |
| mae_mfe.bars_to_mae                      (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.bars_to_mfe                      (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mae_bar                          (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mae_pct                          (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mae_price                        (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mae_r                            (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mfe_bar                          (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mfe_pct                          (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mfe_price                        (100.0%) | No                   | Yes              | Extracted by profiler |
| mae_mfe.mfe_r                            (100.0%) | No                   | Yes              | Extracted by profiler |
| score.bias_strength                      (19.7%) | Yes                  | Yes              | Already captured |
| score.break_quality                      (3.3%) | Yes                  | Yes              | Already captured |
| score.break_strength                     (18.5%) | Yes                  | Yes              | Already captured |
| score.clean_close                        (100.0%) | Yes                  | Yes              | Already captured |
| score.conv_speed                         (2.0%) | Yes                  | Yes              | Already captured |
| score.conv_tight                         (2.0%) | Yes                  | Yes              | Already captured |
| score.div_magnitude                      (2.5%) | Yes                  | Yes              | Already captured |
| score.htf_confluence                     (18.5%) | Yes                  | Yes              | Already captured |
| score.participation                      (81.5%) | Yes                  | Yes              | Already captured |
| score.pullback_quality                   (75.7%) | Yes                  | Yes              | Already captured |
| score.retrace_depth                      (75.7%) | Yes                  | Yes              | Already captured |
| score.rsi_extremity                      (2.5%) | Yes                  | Yes              | Already captured |
| score.severity                           (1.3%) | Yes                  | Yes              | Already captured |
| score.span_quality                       (2.5%) | Yes                  | Yes              | Already captured |
| score.trend_strength                     (77.7%) | Yes                  | Yes              | Already captured |
| score.volume_quality                     (18.5%) | Yes                  | Yes              | Already captured |
| state.bars_out_of_squeeze                (1.3%) | Yes                  | Yes              | Already captured |
| state.bb_width_pctile                    (1.3%) | Yes                  | Yes              | Already captured |
| state.bearish_divergence                 (0.5%) | Yes                  | Yes              | Already captured |
| state.bias_score                         (1.3%) | Yes                  | Yes              | Already captured |
| state.box_high                           (1.3%) | Yes                  | Yes              | Already captured |
| state.box_low                            (1.3%) | Yes                  | Yes              | Already captured |
| state.btc_box_high                       (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_box_low                        (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_breakout_direction             (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_severe                         (1.3%) | Yes                  | Yes              | Already captured |
| state.btc_shock_down                     (2.0%) | Yes                  | Yes              | Already captured |
| state.btc_shock_up                       (2.0%) | Yes                  | Yes              | Already captured |
| state.bullish_divergence                 (2.0%) | Yes                  | Yes              | Already captured |
| state.converging                         (2.0%) | Yes                  | Yes              | Already captured |
| state.dec_count                          (2.0%) | Yes                  | Yes              | Already captured |
| state.donchian_high                      (18.5%) | Yes                  | Yes              | Already captured |
| state.donchian_low                       (18.5%) | Yes                  | Yes              | Already captured |
| state.donchian_mid                       (18.5%) | Yes                  | Yes              | Already captured |
| state.ema20_slope_negative               (75.7%) | Yes                  | Yes              | Already captured |
| state.ema20_slope_positive               (75.7%) | Yes                  | Yes              | Already captured |
| state.ema50_slope_negative               (75.7%) | Yes                  | Yes              | Already captured |
| state.ema50_slope_positive               (75.7%) | Yes                  | Yes              | Already captured |
| state.ema_stacked_long                   (75.7%) | Yes                  | Yes              | Already captured |
| state.ema_stacked_short                  (75.7%) | Yes                  | Yes              | Already captured |
| state.fail_fast_bars                     (1.3%) | Yes                  | Yes              | Already captured |
| state.gap_decreasing                     (2.0%) | Yes                  | Yes              | Already captured |
| state.has_divergence                     (2.5%) | Yes                  | Yes              | Already captured |
| state.held_ema50                         (75.7%) | Yes                  | Yes              | Already captured |
| state.htf_aligned                        (75.7%) | Yes                  | Yes              | Already captured |
| state.htf_bias                           (94.2%) | Yes                  | Yes              | Already captured |
| state.htf_bias_score                     (94.2%) | Yes                  | Yes              | Already captured |
| state.is_narrow                          (2.0%) | Yes                  | Yes              | Already captured |
| state.long_allowed                       (3.3%) | Yes                  | Yes              | Already captured |
| state.long_breakout                      (18.5%) | Yes                  | Yes              | Already captured |
| state.ma_gap_atr                         (2.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_long                     (2.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_long_t1                  (2.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_short                    (2.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_short_t1                 (2.0%) | Yes                  | Yes              | Already captured |
| state.prev_donchian_high                 (18.5%) | Yes                  | Yes              | Already captured |
| state.prev_donchian_low                  (18.5%) | Yes                  | Yes              | Already captured |
| state.pullback_detected                  (75.7%) | Yes                  | Yes              | Already captured |
| state.reclaimed_ema20                    (75.7%) | Yes                  | Yes              | Already captured |
| state.retrace_depth_atr                  (75.7%) | Yes                  | Yes              | Already captured |
| state.ripe                               (1.3%) | Yes                  | Yes              | Already captured |
| state.ripe_at_prev_bar                   (1.3%) | Yes                  | Yes              | Already captured |
| state.rsi_bias                           (18.5%) | Yes                  | Yes              | Already captured |
| state.rsi_extreme_high                   (2.5%) | Yes                  | Yes              | Already captured |
| state.rsi_extreme_low                    (2.5%) | Yes                  | Yes              | Already captured |
| state.rsi_reclaimed_30                   (2.5%) | Yes                  | Yes              | Already captured |
| state.rsi_reclaimed_70                   (2.5%) | Yes                  | Yes              | Already captured |
| state.short_allowed                      (3.3%) | Yes                  | Yes              | Already captured |
| state.short_breakout                     (18.5%) | Yes                  | Yes              | Already captured |
| state.sma200_slope_negative              (2.0%) | Yes                  | Yes              | Already captured |
| state.sma200_slope_positive              (2.0%) | Yes                  | Yes              | Already captured |
| state.sma50_slope_negative               (2.0%) | Yes                  | Yes              | Already captured |
| state.sma50_slope_positive               (2.0%) | Yes                  | Yes              | Already captured |
| state.squeeze_age                        (1.3%) | Yes                  | Yes              | Already captured |
| state.squeeze_on                         (1.3%) | Yes                  | Yes              | Already captured |
| state.time_stop_bars                     (2.0%) | Yes                  | Yes              | Already captured |
| state.touched_ema20                      (75.7%) | Yes                  | Yes              | Already captured |
| state.trend_direction                    (75.7%) | Yes                  | Yes              | Already captured |
| state.trend_strength                     (75.7%) | Yes                  | Yes              | Already captured |
| state.was_wide                           (2.0%) | Yes                  | Yes              | Already captured |
