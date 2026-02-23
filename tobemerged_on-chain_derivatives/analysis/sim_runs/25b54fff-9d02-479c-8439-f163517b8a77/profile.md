# Simulation Profile: 25b54fff

Generated: 2026-02-16 14:54:19 UTC

## A. Run Metadata

- **Run ID:** `25b54fff-9d02-479c-8439-f163517b8a77`
- **Type:** SIMULATION
- **Status:** SUCCESS
- **Name:** simulation_20260216_144924
- **Engine:** 1.0.0
- **Created:** 2026-02-16 14:49:24.541321+00:00
- **Started:** 2026-02-16 14:49:24.649203+00:00
- **Completed:** —
- **Duration:** —s
- **Setups:** S1, S2, S3, S6, S7
- **Timeframes:** 15m, 1h, 4h
- **Symbols:** 437
- **Date range:** 2026-02-08 00:45 UTC → 2026-02-16 00:00 UTC
- **Total opportunities (rows):** 2786
- **Simulated (data_available):** 1405
- **Skipped (no data / error):** 1381

**Run summary_json:**
```json
{
  "total_trades": 1405,
  "winning_trades": 582,
  "losing_trades": 823,
  "win_rate": 0.4142,
  "total_r": -283.47,
  "avg_r": -0.2018,
  "max_r": 0.0,
  "min_r": 0.0,
  "gross_pnl": 0.0,
  "total_fees": 0.0,
  "total_slippage": 0.0,
  "net_pnl": -2395.48,
  "max_drawdown_pct": 0.0,
  "profit_factor": 0.709,
  "expectancy": -0.2018,
  "bars_processed": 0,
  "assets_processed": 0,
  "assets_skipped": 0,
  "signals_generated": 2786,
  "execution_time_seconds": 153.42707514762878,
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
      "trades": 929,
      "wins": 417,
      "pnl_net": -1739.75,
      "total_r": -95.1,
      "win_rate": 0.4488697524219591,
      "avg_r": -0.1023683422672172
    },
    "S7": {
      "trades": 404,
      "wins": 131,
      "pnl_net": -493.17,
      "total_r": -178.79,
      "win_rate": 0.32425742574257427,
      "avg_r": -0.4425552966360206
    },
    "S1": {
      "trades": 21,
      "wins": 11,
      "pnl_net": -21.36,
      "total_r": -1.31,
      "win_rate": 0.5238095238095238,
      "avg_r": -0.062254884223834364
    },
    "S3": {
      "trades": 37,
      "wins": 13,
      "pnl_net": -199.17,
      "total_r": -9.72,
      "win_rate": 0.35135135135135137,
      "avg_r": -0.2626161895684146
    },
    "S2": {
      "trades": 14,
      "wins": 10,
      "pnl_net": 57.97,
      "total_r": 1.45,
      "win_rate": 0.7142857142857143,
      "avg_r": 0.103505111222654
    }
  },
  "per_symbol": {}
}
```

## B. Data Quality

- **Total rows:** 2786
- **Data available (valid):** 1405 (50.4%)
- **Data unavailable:** 1381 (49.6%)
- **With error messages:** 1381 (49.6%)

### Data availability by setup

| Setup | Available | Unavailable | % Available |
|:------|----------:|------------:|------------:|
| S1    |        21 |          18 |       53.8% |
| S2    |        14 |          20 |       41.2% |
| S3    |        37 |          60 |       38.1% |
| S6    |       929 |          61 |       93.8% |
| S7    |       404 |        1222 |       24.8% |

### Error taxonomy

| Error | Count | % of Total | Example |
|:------|------:|-----------:|:--------|
| R-value invalid after slippage |    82 |       2.9% | MLNUSDT/15m/S7 |
| Uneconomic: fees/R=0.32 > max 0.3 |    17 |       0.6% | PAXGUSDT/15m/S6 |
| Uneconomic: fees/R=1.60 > max 0.3 |    17 |       0.6% | EURUSDT/15m/S7 |
| Uneconomic: fees/R=0.31 > max 0.3 |    15 |       0.5% | FORTHUSDT/15m/S7 |
| Uneconomic: fees/R=0.33 > max 0.3 |    15 |       0.5% | GASUSDT/15m/S7 |
| Uneconomic: fees/R=0.37 > max 0.3 |    13 |       0.5% | MLNUSDT/15m/S7 |
| Uneconomic: fees/R=0.35 > max 0.3 |    13 |       0.5% | WBTCUSDT/15m/S7 |
| Uneconomic: fees/R=0.30 > max 0.3 |    12 |       0.4% | ICPUSDT/15m/S7 |
| Uneconomic: fees/R=0.41 > max 0.3 |    12 |       0.4% | GNSUSDT/15m/S7 |
| Uneconomic: fees/R=0.43 > max 0.3 |    10 |       0.4% | BNBUSDT/15m/S7 |
| Uneconomic: fees/R=0.44 > max 0.3 |     9 |       0.3% | FORTHUSDT/15m/S7 |
| Microcap short blocked: price=0.998600 < min=1.0 |     9 |       0.3% | TUSDUSDT/1h/S7 |
| Uneconomic: fees/R=0.34 > max 0.3 |     9 |       0.3% | BNBUSDT/15m/S7 |
| Uneconomic: fees/R=0.45 > max 0.3 |     8 |       0.3% | PAXGUSDT/15m/S6 |
| Uneconomic: fees/R=0.39 > max 0.3 |     8 |       0.3% | GMXUSDT/15m/S7 |
| Uneconomic: fees/R=0.42 > max 0.3 |     7 |       0.3% | BNBUSDT/15m/S7 |
| Uneconomic: fees/R=0.38 > max 0.3 |     7 |       0.3% | WBTCUSDT/15m/S7 |
| Uneconomic: fees/R=0.40 > max 0.3 |     7 |       0.3% | ARUSDT/1h/S7 |
| Uneconomic: fees/R=0.49 > max 0.3 |     6 |       0.2% | ETCUSDT/15m/S7 |
| Microcap short blocked: price=0.999700 < min=1.0 |     6 |       0.2% | RLUSDUSDT/1h/S7 |
| Uneconomic: fees/R=0.51 > max 0.3 |     6 |       0.2% | WBTCUSDT/15m/S7 |
| Microcap short blocked: price=0.025887 < min=1.0 |     5 |       0.2% | BICOUSDT/15m/S7 |
| Uneconomic: fees/R=0.50 > max 0.3 |     5 |       0.2% | SANTOSUSDT/15m/S7 |
| Microcap short blocked: price=0.999900 < min=1.0 |     5 |       0.2% | RLUSDUSDT/15m/S7 |
| Uneconomic: fees/R=0.48 > max 0.3 |     5 |       0.2% | CITYUSDT/15m/S7 |
| Microcap short blocked: price=0.999500 < min=1.0 |     5 |       0.2% | USD1USDT/1h/S6 |
| Uneconomic: fees/R=0.47 > max 0.3 |     5 |       0.2% | SUNUSDT/15m/S6 |
| Uneconomic: fees/R=0.96 > max 0.3 |     4 |       0.1% | PAXGUSDT/15m/S7 |
| Microcap short blocked: price=0.043878 < min=1.0 |     4 |       0.1% | POLYXUSDT/15m/S7 |
| Microcap short blocked: price=0.026087 < min=1.0 |     4 |       0.1% | HAEDALUSDT/1h/S7 |
| Microcap short blocked: price=0.013893 < min=1.0 |     4 |       0.1% | GLMRUSDT/15m/S7 |
| Microcap short blocked: price=0.131934 < min=1.0 |     4 |       0.1% | LSKUSDT/15m/S7 |
| Uneconomic: fees/R=1.46 > max 0.3 |     4 |       0.1% | BFUSDUSDT/15m/S7 |
| Uneconomic: fees/R=1.04 > max 0.3 |     4 |       0.1% | AEURUSDT/15m/S3 |
| Microcap short blocked: price=0.000022 < min=1.0 |     4 |       0.1% | WINUSDT/1h/S7 |
| Microcap short blocked: price=0.000011 < min=1.0 |     4 |       0.1% | 1000SATSUSDT/15m/S7 |
| Microcap short blocked: price=0.019190 < min=1.0 |     4 |       0.1% | COOKIEUSDT/15m/S7 |
| Uneconomic: fees/R=0.56 > max 0.3 |     4 |       0.1% | PAXGUSDT/15m/S7 |
| Uneconomic: fees/R=0.63 > max 0.3 |     4 |       0.1% | MINAUSDT/15m/S7 |
| Uneconomic: fees/R=0.52 > max 0.3 |     4 |       0.1% | ACEUSDT/15m/S7 |
| Microcap short blocked: price=0.076262 < min=1.0 |     4 |       0.1% | ADXUSDT/15m/S7 |
| Uneconomic: fees/R=0.36 > max 0.3 |     4 |       0.1% | NEOUSDT/15m/S7 |
| Uneconomic: fees/R=0.54 > max 0.3 |     4 |       0.1% | ILVUSDT/1h/S7 |
| Microcap short blocked: price=0.026487 < min=1.0 |     3 |       0.1% | SXPUSDT/1h/S7 |
| Uneconomic: fees/R=1.10 > max 0.3 |     3 |       0.1% | BANANAUSDT/15m/S7 |
| Uneconomic: fees/R=0.59 > max 0.3 |     3 |       0.1% | BIFIUSDT/15m/S7 |
| Uneconomic: fees/R=1.08 > max 0.3 |     3 |       0.1% | PORTOUSDT/15m/S7 |
| Microcap short blocked: price=0.045877 < min=1.0 |     3 |       0.1% | ONTUSDT/1h/S7 |
| Uneconomic: fees/R=1.16 > max 0.3 |     3 |       0.1% | AEURUSDT/15m/S7 |
| Microcap short blocked: price=0.005537 < min=1.0 |     3 |       0.1% | PIXELUSDT/1h/S1 |
| Microcap short blocked: price=0.208896 < min=1.0 |     3 |       0.1% | EIGENUSDT/15m/S7 |
| Microcap short blocked: price=0.045977 < min=1.0 |     3 |       0.1% | PYTHUSDT/15m/S7 |
| Microcap short blocked: price=0.013793 < min=1.0 |     3 |       0.1% | NTRNUSDT/15m/S7 |
| Microcap short blocked: price=0.064268 < min=1.0 |     3 |       0.1% | WANUSDT/1h/S7 |
| Microcap short blocked: price=0.000006 < min=1.0 |     3 |       0.1% | BONKUSDT/15m/S7 |
| Microcap short blocked: price=0.038381 < min=1.0 |     3 |       0.1% | JOEUSDT/15m/S7 |
| Microcap short blocked: price=0.097251 < min=1.0 |     3 |       0.1% | SCRTUSDT/15m/S7 |
| Microcap short blocked: price=0.060170 < min=1.0 |     3 |       0.1% | LUNAUSDT/1h/S7 |
| Uneconomic: fees/R=0.70 > max 0.3 |     3 |       0.1% | BANANAUSDT/15m/S7 |
| Microcap short blocked: price=0.025687 < min=1.0 |     3 |       0.1% | HOOKUSDT/15m/S7 |
| Microcap short blocked: price=1.000000 < min=1.0 |     3 |       0.1% | USDPUSDT/1h/S7 |
| Uneconomic: fees/R=0.80 > max 0.3 |     3 |       0.1% | AUCTIONUSDT/15m/S7 |
| Microcap short blocked: price=0.016392 < min=1.0 |     3 |       0.1% | BMTUSDT/15m/S7 |
| Microcap short blocked: price=0.022489 < min=1.0 |     3 |       0.1% | AIUSDT/15m/S7 |
| Microcap short blocked: price=0.000004 < min=1.0 |     3 |       0.1% | PEPEUSDT/15m/S7 |
| Microcap short blocked: price=0.999300 < min=1.0 |     3 |       0.1% | BFUSDUSDT/1h/S7 |
| Uneconomic: fees/R=1.14 > max 0.3 |     3 |       0.1% | EURIUSDT/15m/S7 |
| Uneconomic: fees/R=0.93 > max 0.3 |     3 |       0.1% | LPTUSDT/15m/S7 |
| Uneconomic: fees/R=0.77 > max 0.3 |     3 |       0.1% | BTCUSDT/1h/S7 |
| Microcap short blocked: price=0.157021 < min=1.0 |     3 |       0.1% | PUNDIXUSDT/15m/S7 |
| Uneconomic: fees/R=0.64 > max 0.3 |     3 |       0.1% | C98USDT/15m/S7 |
| Microcap short blocked: price=0.022389 < min=1.0 |     3 |       0.1% | AIUSDT/15m/S7 |
| Uneconomic: fees/R=0.75 > max 0.3 |     3 |       0.1% | SPKUSDT/15m/S7 |
| Uneconomic: fees/R=0.53 > max 0.3 |     3 |       0.1% | QTUMUSDT/15m/S6 |
| Microcap short blocked: price=0.019590 < min=1.0 |     2 |       0.1% | RAREUSDT/15m/S7 |
| Microcap short blocked: price=0.063068 < min=1.0 |     2 |       0.1% | TKOUSDT/15m/S7 |
| Microcap short blocked: price=0.163918 < min=1.0 |     2 |       0.1% | PHBUSDT/15m/S7 |
| Microcap short blocked: price=0.065867 < min=1.0 |     2 |       0.1% | TREEUSDT/15m/S7 |
| Microcap short blocked: price=0.020390 < min=1.0 |     2 |       0.1% | COOKIEUSDT/15m/S7 |
| Microcap short blocked: price=0.072864 < min=1.0 |     2 |       0.1% | FLUXUSDT/15m/S7 |
| Uneconomic: fees/R=1.15 > max 0.3 |     2 |       0.1% | PAXGUSDT/15m/S7 |
| Microcap short blocked: price=0.066967 < min=1.0 |     2 |       0.1% | LUMIAUSDT/15m/S7 |
| Microcap short blocked: price=0.020990 < min=1.0 |     2 |       0.1% | MBOXUSDT/15m/S7 |
| Microcap short blocked: price=0.145927 < min=1.0 |     2 |       0.1% | HIGHUSDT/15m/S7 |
| Microcap short blocked: price=0.289855 < min=1.0 |     2 |       0.1% | SNXUSDT/15m/S7 |
| Microcap short blocked: price=0.051974 < min=1.0 |     2 |       0.1% | IDUSDT/15m/S7 |
| Uneconomic: fees/R=1.41 > max 0.3 |     2 |       0.1% | AEURUSDT/15m/S7 |
| Microcap short blocked: price=0.009085 < min=1.0 |     2 |       0.1% | FIOUSDT/15m/S7 |
| Microcap short blocked: price=0.128536 < min=1.0 |     2 |       0.1% | MMTUSDT/15m/S7 |
| Microcap short blocked: price=0.005877 < min=1.0 |     2 |       0.1% | IOTXUSDT/1h/S7 |
| Microcap short blocked: price=0.159920 < min=1.0 |     2 |       0.1% | PHBUSDT/1h/S7 |
| Microcap short blocked: price=0.159420 < min=1.0 |     2 |       0.1% | XLMUSDT/1h/S1 |
| Microcap short blocked: price=0.080260 < min=1.0 |     2 |       0.1% | ADXUSDT/15m/S7 |
| Microcap short blocked: price=0.086457 < min=1.0 |     2 |       0.1% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.018091 < min=1.0 |     2 |       0.1% | HFTUSDT/15m/S7 |
| Microcap short blocked: price=0.051274 < min=1.0 |     2 |       0.1% | IDUSDT/15m/S7 |
| Microcap short blocked: price=0.064668 < min=1.0 |     2 |       0.1% | MINAUSDT/15m/S7 |
| Microcap short blocked: price=0.022689 < min=1.0 |     2 |       0.1% | MOVEUSDT/15m/S7 |
| Microcap short blocked: price=0.043578 < min=1.0 |     2 |       0.1% | POLYXUSDT/15m/S7 |
| Microcap short blocked: price=0.200900 < min=1.0 |     2 |       0.1% | THETAUSDT/15m/S7 |
| Microcap short blocked: price=0.040880 < min=1.0 |     2 |       0.1% | TURTLEUSDT/15m/S7 |
| Microcap short blocked: price=0.019690 < min=1.0 |     2 |       0.1% | WUSDT/15m/S7 |
| Microcap short blocked: price=0.041479 < min=1.0 |     2 |       0.1% | DYMUSDT/15m/S7 |
| Microcap short blocked: price=0.271864 < min=1.0 |     2 |       0.1% | MTLUSDT/15m/S7 |
| Uneconomic: fees/R=0.98 > max 0.3 |     2 |       0.1% | PAXGUSDT/15m/S7 |
| Microcap short blocked: price=0.998101 < min=1.0 |     2 |       0.1% | TUSDUSDT/15m/S7 |
| Uneconomic: fees/R=1.19 > max 0.3 |     2 |       0.1% | EURIUSDT/15m/S7 |
| Microcap short blocked: price=0.027286 < min=1.0 |     2 |       0.1% | GRTUSDT/15m/S7 |
| Microcap short blocked: price=0.460770 < min=1.0 |     2 |       0.1% | MASKUSDT/15m/S7 |
| Microcap short blocked: price=0.006897 < min=1.0 |     2 |       0.1% | TUSDT/15m/S7 |
| Microcap short blocked: price=0.026187 < min=1.0 |     2 |       0.1% | BIOUSDT/15m/S7 |
| Microcap short blocked: price=0.043678 < min=1.0 |     2 |       0.1% | CATIUSDT/15m/S7 |
| Microcap short blocked: price=0.514742 < min=1.0 |     2 |       0.1% | CYBERUSDT/15m/S7 |
| Microcap short blocked: price=0.000035 < min=1.0 |     2 |       0.1% | LUNCUSDT/15m/S7 |
| Microcap short blocked: price=0.067966 < min=1.0 |     2 |       0.1% | NEWTUSDT/15m/S7 |
| Microcap short blocked: price=0.043778 < min=1.0 |     2 |       0.1% | POLYXUSDT/15m/S7 |
| Microcap short blocked: price=0.000008 < min=1.0 |     2 |       0.1% | XECUSDT/15m/S7 |
| Microcap short blocked: price=0.030585 < min=1.0 |     2 |       0.1% | SAGAUSDT/15m/S7 |
| Uneconomic: fees/R=1.40 > max 0.3 |     2 |       0.1% | RLUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.006297 < min=1.0 |     2 |       0.1% | TRUUSDT/15m/S7 |
| Microcap short blocked: price=0.031284 < min=1.0 |     2 |       0.1% | BBUSDT/15m/S7 |
| Microcap short blocked: price=0.000387 < min=1.0 |     2 |       0.1% | BOMEUSDT/15m/S7 |
| Microcap short blocked: price=0.079060 < min=1.0 |     2 |       0.1% | LAYERUSDT/15m/S7 |
| Microcap short blocked: price=0.156922 < min=1.0 |     2 |       0.1% | PHBUSDT/15m/S7 |
| Microcap short blocked: price=0.065267 < min=1.0 |     2 |       0.1% | POWRUSDT/15m/S7 |
| Microcap short blocked: price=0.436782 < min=1.0 |     2 |       0.1% | RLCUSDT/15m/S7 |
| Microcap short blocked: price=0.039680 < min=1.0 |     2 |       0.1% | TURTLEUSDT/15m/S7 |
| Uneconomic: fees/R=1.44 > max 0.3 |     2 |       0.1% | USD1USDT/15m/S7 |
| Uneconomic: fees/R=1.74 > max 0.3 |     2 |       0.1% | FDUSDUSDT/15m/S7 |
| Microcap short blocked: price=0.320839 < min=1.0 |     2 |       0.1% | DEGOUSDT/15m/S7 |
| Microcap short blocked: price=0.999800 < min=1.0 |     2 |       0.1% | XUSDUSDT/15m/S6 |
| Microcap short blocked: price=0.025787 < min=1.0 |     2 |       0.1% | BICOUSDT/15m/S7 |
| Microcap short blocked: price=0.005277 < min=1.0 |     2 |       0.1% | ANIMEUSDT/15m/S7 |
| Microcap short blocked: price=0.039080 < min=1.0 |     2 |       0.1% | LRCUSDT/15m/S7 |
| Microcap short blocked: price=0.000702 < min=1.0 |     2 |       0.1% | MEMEUSDT/15m/S7 |
| Microcap short blocked: price=0.019890 < min=1.0 |     2 |       0.1% | RAREUSDT/15m/S7 |
| Microcap short blocked: price=0.040180 < min=1.0 |     2 |       0.1% | TURTLEUSDT/15m/S7 |
| Microcap short blocked: price=0.016692 < min=1.0 |     2 |       0.1% | WOOUSDT/15m/S7 |
| Microcap short blocked: price=0.025387 < min=1.0 |     2 |       0.1% | HOOKUSDT/15m/S7 |
| Uneconomic: fees/R=1.67 > max 0.3 |     2 |       0.1% | USDCUSDT/1h/S7 |
| Microcap short blocked: price=0.024888 < min=1.0 |     2 |       0.1% | BICOUSDT/15m/S7 |
| Microcap short blocked: price=0.040080 < min=1.0 |     2 |       0.1% | DYMUSDT/1h/S7 |
| Microcap short blocked: price=0.987506 < min=1.0 |     2 |       0.1% | NEARUSDT/1h/S7 |
| Microcap short blocked: price=0.023088 < min=1.0 |     2 |       0.1% | SXTUSDT/1h/S7 |
| Adverse gap: R degraded 43% (min 50%) |     2 |       0.1% | TAOUSDT/1h/S6 |
| Microcap short blocked: price=0.042079 < min=1.0 |     2 |       0.1% | RIFUSDT/15m/S3 |
| Uneconomic: fees/R=0.76 > max 0.3 |     2 |       0.1% | TRXUSDT/15m/S7 |
| Microcap short blocked: price=0.999400 < min=1.0 |     2 |       0.1% | USD1USDT/4h/S7 |
| Microcap short blocked: price=0.840580 < min=1.0 |     2 |       0.1% | GNSUSDT/15m/S7 |
| Microcap short blocked: price=0.041629 < min=1.0 |     2 |       0.1% | JSTUSDT/1h/S7 |
| Microcap short blocked: price=0.002919 < min=1.0 |     2 |       0.1% | REZUSDT/15m/S7 |
| Microcap short blocked: price=0.003908 < min=1.0 |     2 |       0.1% | GALAUSDT/1h/S7 |
| Microcap short blocked: price=0.025527 < min=1.0 |     2 |       0.1% | GRTUSDT/4h/S7 |
| Microcap short blocked: price=0.045377 < min=1.0 |     2 |       0.1% | PNUTUSDT/1h/S7 |
| Microcap short blocked: price=0.022889 < min=1.0 |     2 |       0.1% | SXTUSDT/15m/S7 |
| Microcap short blocked: price=0.000071 < min=1.0 |     2 |       0.1% | NEIROUSDT/1h/S7 |
| Microcap short blocked: price=0.004078 < min=1.0 |     2 |       0.1% | DGBUSDT/15m/S7 |
| Microcap short blocked: price=0.193903 < min=1.0 |     2 |       0.1% | EIGENUSDT/15m/S7 |
| Microcap short blocked: price=0.705647 < min=1.0 |     2 |       0.1% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.005437 < min=1.0 |     2 |       0.1% | IOTXUSDT/1h/S7 |
| Microcap short blocked: price=0.005407 < min=1.0 |     2 |       0.1% | IOTXUSDT/1h/S7 |
| Uneconomic: fees/R=0.71 > max 0.3 |     2 |       0.1% | PAXGUSDT/15m/S7 |
| Microcap short blocked: price=0.026287 < min=1.0 |     2 |       0.1% | CHRUSDT/1h/S7 |
| Uneconomic: fees/R=0.61 > max 0.3 |     2 |       0.1% | IDUSDT/15m/S7 |
| Microcap short blocked: price=0.019490 < min=1.0 |     2 |       0.1% | COOKIEUSDT/15m/S7 |
| Microcap short blocked: price=0.089255 < min=1.0 |     2 |       0.1% | ZKPUSDT/15m/S7 |
| Microcap short blocked: price=0.026587 < min=1.0 |     2 |       0.1% | HAEDALUSDT/15m/S7 |
| Microcap short blocked: price=0.998201 < min=1.0 |     2 |       0.1% | TUSDUSDT/15m/S7 |
| Microcap short blocked: price=0.025987 < min=1.0 |     2 |       0.1% | AEVOUSDT/15m/S7 |
| Microcap short blocked: price=0.021789 < min=1.0 |     2 |       0.1% | SXPUSDT/1h/S7 |
| Microcap short blocked: price=0.998401 < min=1.0 |     2 |       0.1% | FDUSDUSDT/1h/S7 |
| Uneconomic: fees/R=0.74 > max 0.3 |     2 |       0.1% | ALPINEUSDT/15m/S7 |
| Uneconomic: fees/R=0.58 > max 0.3 |     2 |       0.1% | MOVRUSDT/15m/S7 |
| Uneconomic: fees/R=0.73 > max 0.3 |     2 |       0.1% | EURUSDT/15m/S6 |
| Microcap short blocked: price=0.101449 < min=1.0 |     2 |       0.1% | WLFIUSDT/1h/S7 |
| Uneconomic: fees/R=1.45 > max 0.3 |     2 |       0.1% | FDUSDUSDT/15m/S7 |
| Adverse gap: R degraded 36% (min 50%) |     2 |       0.1% | RADUSDT/1h/S6 |
| Uneconomic: fees/R=0.55 > max 0.3 |     2 |       0.1% | 1INCHUSDT/15m/S7 |
| Adverse gap: R degraded 44% (min 50%) |     2 |       0.1% | VTHOUSDT/15m/S6 |
| Microcap short blocked: price=0.000005 < min=1.0 |     2 |       0.1% | PEPEUSDT/15m/S3 |
| Microcap short blocked: price=0.129935 < min=1.0 |     2 |       0.1% | BATUSDT/1h/S7 |
| Microcap short blocked: price=0.001674 < min=1.0 |     2 |       0.1% | CKBUSDT/1h/S7 |
| Microcap short blocked: price=0.034443 < min=1.0 |     2 |       0.1% | DOLOUSDT/1h/S7 |
| Microcap short blocked: price=0.021529 < min=1.0 |     2 |       0.1% | ENJUSDT/1h/S7 |
| Microcap short blocked: price=0.190505 < min=1.0 |     2 |       0.1% | GLMUSDT/1h/S7 |
| Microcap short blocked: price=0.102249 < min=1.0 |     2 |       0.1% | HYPERUSDT/1h/S7 |
| Microcap short blocked: price=0.090555 < min=1.0 |     1 |       0.0% | DUSKUSDT/15m/S3 |
| Microcap short blocked: price=0.886557 < min=1.0 |     1 |       0.0% | EULUSDT/15m/S1 |
| Microcap short blocked: price=0.094123 < min=1.0 |     1 |       0.0% | 2ZUSDT/15m/S7 |
| Microcap short blocked: price=0.001095 < min=1.0 |     1 |       0.0% | TURBOUSDT/15m/S7 |
| Microcap short blocked: price=0.007136 < min=1.0 |     1 |       0.0% | TUSDT/15m/S7 |
| Microcap short blocked: price=0.104148 < min=1.0 |     1 |       0.0% | MANAUSDT/15m/S7 |
| Microcap short blocked: price=0.029785 < min=1.0 |     1 |       0.0% | AEVOUSDT/15m/S7 |
| Microcap short blocked: price=0.228586 < min=1.0 |     1 |       0.0% | AVAUSDT/15m/S7 |
| Microcap short blocked: price=0.295152 < min=1.0 |     1 |       0.0% | NXPCUSDT/15m/S7 |
| Microcap short blocked: price=0.795602 < min=1.0 |     1 |       0.0% | ORCAUSDT/15m/S7 |
| Microcap short blocked: price=0.006777 < min=1.0 |     1 |       0.0% | PEOPLEUSDT/15m/S7 |
| Microcap short blocked: price=0.009765 < min=1.0 |     1 |       0.0% | SOPHUSDT/15m/S7 |
| Microcap short blocked: price=0.001072 < min=1.0 |     1 |       0.0% | COSUSDT/15m/S7 |
| Uneconomic: fees/R=1.30 > max 0.3 |     1 |       0.0% | DEXEUSDT/15m/S7 |
| Microcap short blocked: price=0.002609 < min=1.0 |     1 |       0.0% | ONEUSDT/15m/S7 |
| Microcap short blocked: price=0.030275 < min=1.0 |     1 |       0.0% | KMNOUSDT/15m/S7 |
| Microcap short blocked: price=0.121639 < min=1.0 |     1 |       0.0% | BATUSDT/15m/S7 |
| Microcap short blocked: price=0.000399 < min=1.0 |     1 |       0.0% | NOTUSDT/15m/S7 |
| Microcap short blocked: price=0.935532 < min=1.0 |     1 |       0.0% | QTUMUSDT/15m/S7 |
| Microcap short blocked: price=0.272764 < min=1.0 |     1 |       0.0% | SFPUSDT/15m/S7 |
| Microcap short blocked: price=0.000645 < min=1.0 |     1 |       0.0% | SLPUSDT/15m/S7 |
| Microcap short blocked: price=0.012694 < min=1.0 |     1 |       0.0% | ATAUSDT/15m/S7 |
| Microcap short blocked: price=0.082059 < min=1.0 |     1 |       0.0% | CELOUSDT/15m/S7 |
| Microcap short blocked: price=0.048176 < min=1.0 |     1 |       0.0% | CFXUSDT/15m/S7 |
| Microcap short blocked: price=0.088956 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.069965 < min=1.0 |     1 |       0.0% | IOTAUSDT/15m/S7 |
| Microcap short blocked: price=0.021189 < min=1.0 |     1 |       0.0% | MBOXUSDT/15m/S7 |
| Microcap short blocked: price=0.002589 < min=1.0 |     1 |       0.0% | ONEUSDT/15m/S7 |
| Microcap short blocked: price=0.027786 < min=1.0 |     1 |       0.0% | PHAUSDT/15m/S7 |
| Microcap short blocked: price=0.021549 < min=1.0 |     1 |       0.0% | CGPTUSDT/15m/S7 |
| Microcap short blocked: price=0.013343 < min=1.0 |     1 |       0.0% | SYSUSDT/15m/S7 |
| Uneconomic: fees/R=1.05 > max 0.3 |     1 |       0.0% | PAXGUSDT/15m/S7 |
| Microcap short blocked: price=0.017191 < min=1.0 |     1 |       0.0% | SUNUSDT/15m/S3 |
| Microcap short blocked: price=0.054073 < min=1.0 |     1 |       0.0% | CUSDT/15m/S7 |
| Microcap short blocked: price=0.009165 < min=1.0 |     1 |       0.0% | FIOUSDT/15m/S7 |
| Microcap short blocked: price=0.000012 < min=1.0 |     1 |       0.0% | 1000SATSUSDT/15m/S7 |
| Uneconomic: fees/R=0.69 > max 0.3 |     1 |       0.0% | PAXGUSDT/15m/S6 |
| Microcap short blocked: price=0.000148 < min=1.0 |     1 |       0.0% | DENTUSDT/15m/S7 |
| Microcap short blocked: price=0.001244 < min=1.0 |     1 |       0.0% | IOSTUSDT/15m/S7 |
| Microcap short blocked: price=0.017581 < min=1.0 |     1 |       0.0% | MAVUSDT/15m/S7 |
| Microcap short blocked: price=0.006597 < min=1.0 |     1 |       0.0% | TRUUSDT/15m/S7 |
| Microcap short blocked: price=0.000029 < min=1.0 |     1 |       0.0% | DOGSUSDT/15m/S7 |
| Microcap short blocked: price=0.017491 < min=1.0 |     1 |       0.0% | NFPUSDT/15m/S7 |
| Microcap short blocked: price=0.020090 < min=1.0 |     1 |       0.0% | WUSDT/15m/S7 |
| Microcap short blocked: price=0.179910 < min=1.0 |     1 |       0.0% | ACEUSDT/15m/S7 |
| Uneconomic: fees/R=2.96 > max 0.3 |     1 |       0.0% | BANANAUSDT/15m/S7 |
| Microcap short blocked: price=0.010125 < min=1.0 |     1 |       0.0% | DUSDT/15m/S7 |
| Microcap short blocked: price=0.204798 < min=1.0 |     1 |       0.0% | DIAUSDT/15m/S7 |
| Microcap short blocked: price=0.101349 < min=1.0 |     1 |       0.0% | MANAUSDT/15m/S7 |
| Microcap short blocked: price=0.020590 < min=1.0 |     1 |       0.0% | AIXBTUSDT/15m/S7 |
| Microcap short blocked: price=0.088256 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.722938 < min=1.0 |     1 |       0.0% | FRAXUSDT/15m/S7 |
| Microcap short blocked: price=0.008326 < min=1.0 |     1 |       0.0% | CHESSUSDT/15m/S1 |
| Microcap short blocked: price=0.028486 < min=1.0 |     1 |       0.0% | AEVOUSDT/1h/S7 |
| Microcap short blocked: price=0.464768 < min=1.0 |     1 |       0.0% | ETHFIUSDT/1h/S1 |
| Microcap short blocked: price=0.038981 < min=1.0 |     1 |       0.0% | JOEUSDT/1h/S7 |
| Microcap short blocked: price=0.004408 < min=1.0 |     1 |       0.0% | TOWNSUSDT/1h/S7 |
| Microcap short blocked: price=0.157921 < min=1.0 |     1 |       0.0% | YBUSDT/1h/S2 |
| Microcap short blocked: price=0.000412 < min=1.0 |     1 |       0.0% | BOMEUSDT/15m/S7 |
| Microcap short blocked: price=0.519740 < min=1.0 |     1 |       0.0% | CYBERUSDT/15m/S7 |
| Microcap short blocked: price=0.200999 < min=1.0 |     1 |       0.0% | DIAUSDT/15m/S7 |
| Microcap short blocked: price=0.005977 < min=1.0 |     1 |       0.0% | IOTXUSDT/15m/S7 |
| Microcap short blocked: price=0.162419 < min=1.0 |     1 |       0.0% | JUPUSDT/15m/S7 |
| Microcap short blocked: price=0.149625 < min=1.0 |     1 |       0.0% | KNCUSDT/15m/S7 |
| Microcap short blocked: price=0.465767 < min=1.0 |     1 |       0.0% | MASKUSDT/15m/S7 |
| Microcap short blocked: price=0.276862 < min=1.0 |     1 |       0.0% | MTLUSDT/15m/S7 |
| Microcap short blocked: price=0.165517 < min=1.0 |     1 |       0.0% | PUNDIXUSDT/15m/S7 |
| Microcap short blocked: price=0.006967 < min=1.0 |     1 |       0.0% | TUSDT/15m/S7 |
| Microcap short blocked: price=0.510745 < min=1.0 |     1 |       0.0% | UMAUSDT/15m/S7 |
| Microcap short blocked: price=0.092714 < min=1.0 |     1 |       0.0% | HBARUSDT/15m/S3 |
| Microcap short blocked: price=0.007606 < min=1.0 |     1 |       0.0% | IDEXUSDT/15m/S7 |
| Microcap short blocked: price=0.296052 < min=1.0 |     1 |       0.0% | BNTUSDT/1h/S1 |
| Microcap short blocked: price=0.017891 < min=1.0 |     1 |       0.0% | HFTUSDT/1h/S7 |
| Microcap short blocked: price=0.000396 < min=1.0 |     1 |       0.0% | HOTUSDT/1h/S1 |
| Microcap short blocked: price=0.045577 < min=1.0 |     1 |       0.0% | NILUSDT/1h/S7 |
| Microcap short blocked: price=0.019290 < min=1.0 |     1 |       0.0% | RAREUSDT/1h/S7 |
| Microcap short blocked: price=0.152924 < min=1.0 |     1 |       0.0% | YBUSDT/1h/S2 |
| Microcap short blocked: price=0.000982 < min=1.0 |     1 |       0.0% | A2ZUSDT/15m/S7 |
| Microcap short blocked: price=0.118841 < min=1.0 |     1 |       0.0% | BATUSDT/15m/S7 |
| Microcap short blocked: price=0.098951 < min=1.0 |     1 |       0.0% | BELUSDT/15m/S7 |
| Microcap short blocked: price=0.053773 < min=1.0 |     1 |       0.0% | CUSDT/15m/S7 |
| Microcap short blocked: price=0.933533 < min=1.0 |     1 |       0.0% | FILUSDT/15m/S7 |
| Microcap short blocked: price=0.012334 < min=1.0 |     1 |       0.0% | GMTUSDT/15m/S7 |
| Microcap short blocked: price=0.005927 < min=1.0 |     1 |       0.0% | IOTXUSDT/15m/S7 |
| Microcap short blocked: price=0.251674 < min=1.0 |     1 |       0.0% | JTOUSDT/15m/S7 |
| Microcap short blocked: price=0.054473 < min=1.0 |     1 |       0.0% | KAIAUSDT/15m/S7 |
| Microcap short blocked: price=0.095352 < min=1.0 |     1 |       0.0% | POLUSDT/15m/S7 |
| Microcap short blocked: price=0.067666 < min=1.0 |     1 |       0.0% | POWRUSDT/15m/S7 |
| Microcap short blocked: price=0.055472 < min=1.0 |     1 |       0.0% | SYNUSDT/15m/S7 |
| Microcap short blocked: price=0.004708 < min=1.0 |     1 |       0.0% | USTCUSDT/15m/S7 |
| Microcap short blocked: price=0.153923 < min=1.0 |     1 |       0.0% | YBUSDT/15m/S7 |
| Microcap short blocked: price=0.053173 < min=1.0 |     1 |       0.0% | CUSDT/15m/S7 |
| Microcap short blocked: price=0.000147 < min=1.0 |     1 |       0.0% | DENTUSDT/15m/S7 |
| Microcap short blocked: price=0.202899 < min=1.0 |     1 |       0.0% | EIGENUSDT/15m/S7 |
| Microcap short blocked: price=0.132934 < min=1.0 |     1 |       0.0% | LSKUSDT/15m/S7 |
| Microcap short blocked: price=0.017591 < min=1.0 |     1 |       0.0% | OXTUSDT/15m/S7 |
| Microcap short blocked: price=0.017091 < min=1.0 |     1 |       0.0% | WOOUSDT/1h/S7 |
| Microcap short blocked: price=0.000992 < min=1.0 |     1 |       0.0% | MBLUSDT/15m/S7 |
| Microcap short blocked: price=0.319540 < min=1.0 |     1 |       0.0% | PROVEUSDT/15m/S7 |
| Uneconomic: fees/R=3.39 > max 0.3 |     1 |       0.0% | XUSDUSDT/15m/S7 |
| Microcap short blocked: price=0.068866 < min=1.0 |     1 |       0.0% | IOTAUSDT/15m/S7 |
| Microcap short blocked: price=0.569715 < min=1.0 |     1 |       0.0% | JUVUSDT/15m/S7 |
| Uneconomic: fees/R=1.00 > max 0.3 |     1 |       0.0% | PAXGUSDT/15m/S7 |
| Microcap short blocked: price=0.014493 < min=1.0 |     1 |       0.0% | DODOUSDT/1h/S7 |
| Microcap short blocked: price=0.859570 < min=1.0 |     1 |       0.0% | EULUSDT/15m/S7 |
| Microcap short blocked: price=0.028466 < min=1.0 |     1 |       0.0% | HOMEUSDT/15m/S1 |
| Uneconomic: fees/R=1.54 > max 0.3 |     1 |       0.0% | AEURUSDT/15m/S7 |
| Microcap short blocked: price=0.009695 < min=1.0 |     1 |       0.0% | DUSDT/15m/S7 |
| Microcap short blocked: price=0.847576 < min=1.0 |     1 |       0.0% | LAZIOUSDT/15m/S7 |
| Microcap short blocked: price=0.058371 < min=1.0 |     1 |       0.0% | HOLOUSDT/15m/S7 |
| Microcap short blocked: price=0.263168 < min=1.0 |     1 |       0.0% | STXUSDT/15m/S7 |
| Microcap short blocked: price=0.254473 < min=1.0 |     1 |       0.0% | ONDOUSDT/15m/S7 |
| Microcap short blocked: price=0.058271 < min=1.0 |     1 |       0.0% | HOLOUSDT/15m/S7 |
| Stop too wide: 11.6% > max 10% |     1 |       0.0% | AXSUSDT/15m/S2 |
| Stop too wide: 15.2% > max 10% |     1 |       0.0% | AXSUSDT/15m/S2 |
| Uneconomic: fees/R=1.56 > max 0.3 |     1 |       0.0% | EURUSDT/15m/S7 |
| Microcap short blocked: price=0.408596 < min=1.0 |     1 |       0.0% | XTZUSDT/15m/S7 |
| Microcap short blocked: price=0.004228 < min=1.0 |     1 |       0.0% | DGBUSDT/15m/S7 |
| Microcap short blocked: price=0.094053 < min=1.0 |     1 |       0.0% | PIVXUSDT/15m/S7 |
| Microcap short blocked: price=0.055272 < min=1.0 |     1 |       0.0% | VICUSDT/15m/S7 |
| Microcap short blocked: price=0.031884 < min=1.0 |     1 |       0.0% | EDENUSDT/15m/S7 |
| Microcap short blocked: price=0.001223 < min=1.0 |     1 |       0.0% | IOSTUSDT/15m/S7 |
| Microcap short blocked: price=0.080860 < min=1.0 |     1 |       0.0% | SAPIENUSDT/15m/S7 |
| Microcap short blocked: price=0.010095 < min=1.0 |     1 |       0.0% | TSTUSDT/15m/S7 |
| Microcap short blocked: price=0.026677 < min=1.0 |     1 |       0.0% | SIGNUSDT/15m/S1 |
| Microcap short blocked: price=0.004218 < min=1.0 |     1 |       0.0% | DGBUSDT/15m/S7 |
| Microcap short blocked: price=0.137131 < min=1.0 |     1 |       0.0% | MEUSDT/15m/S7 |
| Microcap short blocked: price=0.065167 < min=1.0 |     1 |       0.0% | ZBTUSDT/15m/S7 |
| Microcap short blocked: price=0.000960 < min=1.0 |     1 |       0.0% | A2ZUSDT/15m/S7 |
| Microcap short blocked: price=0.014293 < min=1.0 |     1 |       0.0% | ACTUSDT/15m/S7 |
| Microcap short blocked: price=0.037481 < min=1.0 |     1 |       0.0% | ACXUSDT/15m/S7 |
| Microcap short blocked: price=0.008236 < min=1.0 |     1 |       0.0% | ALTUSDT/15m/S7 |
| Microcap short blocked: price=0.118941 < min=1.0 |     1 |       0.0% | ARKMUSDT/15m/S7 |
| Microcap short blocked: price=0.009915 < min=1.0 |     1 |       0.0% | ARPAUSDT/15m/S7 |
| Microcap short blocked: price=0.007576 < min=1.0 |     1 |       0.0% | ASTRUSDT/15m/S7 |
| Microcap short blocked: price=0.002407 < min=1.0 |     1 |       0.0% | BEAMXUSDT/15m/S7 |
| Microcap short blocked: price=0.014793 < min=1.0 |     1 |       0.0% | BIGTIMEUSDT/15m/S7 |
| Uneconomic: fees/R=1.32 > max 0.3 |     1 |       0.0% | COMPUSDT/15m/S7 |
| Microcap short blocked: price=0.031684 < min=1.0 |     1 |       0.0% | EDENUSDT/15m/S7 |
| Microcap short blocked: price=0.188606 < min=1.0 |     1 |       0.0% | GLMUSDT/15m/S7 |
| Microcap short blocked: price=0.090665 < min=1.0 |     1 |       0.0% | HBARUSDT/15m/S7 |
| Microcap short blocked: price=0.050775 < min=1.0 |     1 |       0.0% | IDUSDT/15m/S7 |
| Microcap short blocked: price=0.001210 < min=1.0 |     1 |       0.0% | IOSTUSDT/15m/S7 |
| Microcap short blocked: price=0.005787 < min=1.0 |     1 |       0.0% | IOTXUSDT/15m/S7 |
| Microcap short blocked: price=0.061769 < min=1.0 |     1 |       0.0% | LUNAUSDT/15m/S7 |
| Microcap short blocked: price=0.136032 < min=1.0 |     1 |       0.0% | MEUSDT/15m/S7 |
| Microcap short blocked: price=0.044258 < min=1.0 |     1 |       0.0% | MITOUSDT/15m/S7 |
| Microcap short blocked: price=0.022989 < min=1.0 |     1 |       0.0% | SXTUSDT/15m/S7 |
| Microcap short blocked: price=0.001037 < min=1.0 |     1 |       0.0% | TURBOUSDT/15m/S7 |
| Microcap short blocked: price=0.005725 < min=1.0 |     1 |       0.0% | XVGUSDT/15m/S7 |
| Microcap short blocked: price=0.000503 < min=1.0 |     1 |       0.0% | 1000CHEEMSUSDT/15m/S7 |
| Microcap short blocked: price=0.000502 < min=1.0 |     1 |       0.0% | 1000CHEEMSUSDT/15m/S7 |
| Microcap short blocked: price=0.028186 < min=1.0 |     1 |       0.0% | AEVOUSDT/15m/S7 |
| Microcap short blocked: price=0.053873 < min=1.0 |     1 |       0.0% | KAIAUSDT/15m/S7 |
| Microcap short blocked: price=0.907546 < min=1.0 |     1 |       0.0% | QTUMUSDT/15m/S7 |
| Microcap short blocked: price=0.078961 < min=1.0 |     1 |       0.0% | SAPIENUSDT/15m/S7 |
| Microcap short blocked: price=0.210895 < min=1.0 |     1 |       0.0% | AGLDUSDT/15m/S7 |
| Microcap short blocked: price=0.906547 < min=1.0 |     1 |       0.0% | QTUMUSDT/15m/S7 |
| Microcap short blocked: price=0.277261 < min=1.0 |     1 |       0.0% | TRXUSDT/1h/S7 |
| Microcap short blocked: price=0.018991 < min=1.0 |     1 |       0.0% | WUSDT/15m/S7 |
| Microcap short blocked: price=0.209895 < min=1.0 |     1 |       0.0% | AGLDUSDT/15m/S7 |
| Microcap short blocked: price=0.093253 < min=1.0 |     1 |       0.0% | ALGOUSDT/15m/S7 |
| Microcap short blocked: price=0.116942 < min=1.0 |     1 |       0.0% | ARKMUSDT/15m/S7 |
| Microcap short blocked: price=0.096952 < min=1.0 |     1 |       0.0% | BELUSDT/15m/S7 |
| Microcap short blocked: price=0.024988 < min=1.0 |     1 |       0.0% | BICOUSDT/15m/S7 |
| Microcap short blocked: price=0.016082 < min=1.0 |     1 |       0.0% | BMTUSDT/15m/S7 |
| Microcap short blocked: price=0.041079 < min=1.0 |     1 |       0.0% | DYMUSDT/15m/S7 |
| Microcap short blocked: price=0.077861 < min=1.0 |     1 |       0.0% | FFUSDT/15m/S7 |
| Microcap short blocked: price=0.011924 < min=1.0 |     1 |       0.0% | GMTUSDT/15m/S7 |
| Microcap short blocked: price=0.049675 < min=1.0 |     1 |       0.0% | IDUSDT/15m/S7 |
| Microcap short blocked: price=0.054773 < min=1.0 |     1 |       0.0% | KAVAUSDT/15m/S7 |
| Microcap short blocked: price=0.098651 < min=1.0 |     1 |       0.0% | MANAUSDT/15m/S7 |
| Microcap short blocked: price=0.043268 < min=1.0 |     1 |       0.0% | MITOUSDT/15m/S7 |
| Microcap short blocked: price=0.750625 < min=1.0 |     1 |       0.0% | ORCAUSDT/15m/S7 |
| Microcap short blocked: price=0.002015 < min=1.0 |     1 |       0.0% | PUMPUSDT/15m/S7 |
| Microcap short blocked: price=0.898551 < min=1.0 |     1 |       0.0% | QTUMUSDT/15m/S7 |
| Microcap short blocked: price=0.008976 < min=1.0 |     1 |       0.0% | SOPHUSDT/15m/S7 |
| Microcap short blocked: price=0.047676 < min=1.0 |     1 |       0.0% | STRKUSDT/15m/S7 |
| Microcap short blocked: price=0.012654 < min=1.0 |     1 |       0.0% | SYSUSDT/15m/S7 |
| Microcap short blocked: price=0.094353 < min=1.0 |     1 |       0.0% | 1INCHUSDT/15m/S7 |
| Microcap short blocked: price=0.031084 < min=1.0 |     1 |       0.0% | BBUSDT/15m/S7 |
| Microcap short blocked: price=0.432784 < min=1.0 |     1 |       0.0% | ETHFIUSDT/15m/S7 |
| Microcap short blocked: price=0.019090 < min=1.0 |     1 |       0.0% | FIDAUSDT/15m/S7 |
| Microcap short blocked: price=0.005267 < min=1.0 |     1 |       0.0% | JASMYUSDT/15m/S7 |
| Microcap short blocked: price=0.052774 < min=1.0 |     1 |       0.0% | KAIAUSDT/15m/S7 |
| Microcap short blocked: price=0.028886 < min=1.0 |     1 |       0.0% | KMNOUSDT/15m/S7 |
| Microcap short blocked: price=0.060370 < min=1.0 |     1 |       0.0% | LUNAUSDT/15m/S7 |
| Microcap short blocked: price=0.445777 < min=1.0 |     1 |       0.0% | MASKUSDT/15m/S7 |
| Microcap short blocked: price=0.010705 < min=1.0 |     1 |       0.0% | MDTUSDT/15m/S7 |
| Microcap short blocked: price=0.083558 < min=1.0 |     1 |       0.0% | MIRAUSDT/15m/S7 |
| Microcap short blocked: price=0.006347 < min=1.0 |     1 |       0.0% | PEOPLEUSDT/15m/S7 |
| Microcap short blocked: price=0.155922 < min=1.0 |     1 |       0.0% | PHBUSDT/15m/S7 |
| Microcap short blocked: price=0.026047 < min=1.0 |     1 |       0.0% | SENTUSDT/15m/S7 |
| Microcap short blocked: price=0.282859 < min=1.0 |     1 |       0.0% | SNXUSDT/15m/S7 |
| Microcap short blocked: price=0.277361 < min=1.0 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Microcap short blocked: price=0.155122 < min=1.0 |     1 |       0.0% | YBUSDT/15m/S7 |
| Microcap short blocked: price=0.457771 < min=1.0 |     1 |       0.0% | BARUSDT/15m/S7 |
| Microcap short blocked: price=0.276562 < min=1.0 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Uneconomic: fees/R=1.53 > max 0.3 |     1 |       0.0% | USDPUSDT/15m/S7 |
| Microcap short blocked: price=0.025487 < min=1.0 |     1 |       0.0% | C98USDT/15m/S7 |
| Stop too wide: 12.9% > max 10% |     1 |       0.0% | GUSDT/15m/S2 |
| Microcap short blocked: price=0.271064 < min=1.0 |     1 |       0.0% | SFPUSDT/15m/S7 |
| Microcap short blocked: price=0.215892 < min=1.0 |     1 |       0.0% | AGLDUSDT/15m/S7 |
| Microcap short blocked: price=0.039380 < min=1.0 |     1 |       0.0% | ICXUSDT/1h/S7 |
| Microcap short blocked: price=0.079660 < min=1.0 |     1 |       0.0% | REQUSDT/15m/S7 |
| Microcap short blocked: price=0.010045 < min=1.0 |     1 |       0.0% | TSTUSDT/15m/S7 |
| Microcap short blocked: price=0.006977 < min=1.0 |     1 |       0.0% | WAXPUSDT/15m/S7 |
| Microcap short blocked: price=0.058471 < min=1.0 |     1 |       0.0% | HOLOUSDT/15m/S7 |
| Microcap short blocked: price=0.211794 < min=1.0 |     1 |       0.0% | CTKUSDT/15m/S7 |
| Microcap short blocked: price=0.099150 < min=1.0 |     1 |       0.0% | BELUSDT/15m/S7 |
| Microcap short blocked: price=0.082859 < min=1.0 |     1 |       0.0% | CELOUSDT/15m/S7 |
| Microcap short blocked: price=0.210795 < min=1.0 |     1 |       0.0% | CTKUSDT/15m/S7 |
| Microcap short blocked: price=0.437781 < min=1.0 |     1 |       0.0% | ETHFIUSDT/15m/S7 |
| Microcap short blocked: price=0.084958 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.071464 < min=1.0 |     1 |       0.0% | HIVEUSDT/15m/S7 |
| Microcap short blocked: price=0.570715 < min=1.0 |     1 |       0.0% | JUVUSDT/15m/S7 |
| Microcap short blocked: price=0.002669 < min=1.0 |     1 |       0.0% | PONDUSDT/15m/S7 |
| Microcap short blocked: price=0.066867 < min=1.0 |     1 |       0.0% | POWRUSDT/15m/S7 |
| Microcap short blocked: price=0.004298 < min=1.0 |     1 |       0.0% | TOWNSUSDT/15m/S7 |
| Microcap short blocked: price=0.037581 < min=1.0 |     1 |       0.0% | ACXUSDT/15m/S7 |
| Microcap short blocked: price=0.007566 < min=1.0 |     1 |       0.0% | ASTRUSDT/15m/S7 |
| Microcap short blocked: price=0.013713 < min=1.0 |     1 |       0.0% | BABYUSDT/15m/S7 |
| Microcap short blocked: price=0.014523 < min=1.0 |     1 |       0.0% | BIGTIMEUSDT/15m/S7 |
| Microcap short blocked: price=0.020380 < min=1.0 |     1 |       0.0% | BLURUSDT/15m/S7 |
| Microcap short blocked: price=0.000395 < min=1.0 |     1 |       0.0% | BOMEUSDT/15m/S7 |
| Microcap short blocked: price=0.042978 < min=1.0 |     1 |       0.0% | CATIUSDT/15m/S7 |
| Microcap short blocked: price=0.082259 < min=1.0 |     1 |       0.0% | CELOUSDT/15m/S7 |
| Microcap short blocked: price=0.001051 < min=1.0 |     1 |       0.0% | COSUSDT/15m/S7 |
| Microcap short blocked: price=0.250575 < min=1.0 |     1 |       0.0% | CRVUSDT/15m/S7 |
| Microcap short blocked: price=0.520740 < min=1.0 |     1 |       0.0% | CYBERUSDT/15m/S7 |
| Microcap short blocked: price=0.014393 < min=1.0 |     1 |       0.0% | DODOUSDT/15m/S7 |
| Microcap short blocked: price=0.094833 < min=1.0 |     1 |       0.0% | DOGEUSDT/15m/S7 |
| Microcap short blocked: price=0.009515 < min=1.0 |     1 |       0.0% | DUSDT/15m/S7 |
| Microcap short blocked: price=0.117641 < min=1.0 |     1 |       0.0% | ENAUSDT/15m/S7 |
| Microcap short blocked: price=0.928536 < min=1.0 |     1 |       0.0% | FILUSDT/15m/S7 |
| Microcap short blocked: price=0.872564 < min=1.0 |     1 |       0.0% | GNSUSDT/15m/S7 |
| Microcap short blocked: price=0.084258 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.000391 < min=1.0 |     1 |       0.0% | HOTUSDT/15m/S7 |
| Microcap short blocked: price=0.001221 < min=1.0 |     1 |       0.0% | IOSTUSDT/15m/S7 |
| Microcap short blocked: price=0.005757 < min=1.0 |     1 |       0.0% | IOTXUSDT/15m/S7 |
| Microcap short blocked: price=0.020290 < min=1.0 |     1 |       0.0% | MBOXUSDT/15m/S7 |
| Microcap short blocked: price=0.273863 < min=1.0 |     1 |       0.0% | MTLUSDT/15m/S7 |
| Microcap short blocked: price=0.044778 < min=1.0 |     1 |       0.0% | OMUSDT/15m/S7 |
| Microcap short blocked: price=0.248176 < min=1.0 |     1 |       0.0% | ONDOUSDT/15m/S7 |
| Microcap short blocked: price=0.005327 < min=1.0 |     1 |       0.0% | PIXELUSDT/15m/S7 |
| Microcap short blocked: price=0.002999 < min=1.0 |     1 |       0.0% | REZUSDT/15m/S7 |
| Microcap short blocked: price=0.434783 < min=1.0 |     1 |       0.0% | RLCUSDT/15m/S7 |
| Microcap short blocked: price=0.404798 < min=1.0 |     1 |       0.0% | RUNEUSDT/15m/S7 |
| Microcap short blocked: price=0.005737 < min=1.0 |     1 |       0.0% | RVNUSDT/15m/S7 |
| Microcap short blocked: price=0.030785 < min=1.0 |     1 |       0.0% | SAGAUSDT/15m/S7 |
| Microcap short blocked: price=0.283858 < min=1.0 |     1 |       0.0% | SNXUSDT/15m/S7 |
| Microcap short blocked: price=0.120140 < min=1.0 |     1 |       0.0% | SUPERUSDT/15m/S7 |
| Microcap short blocked: price=0.004248 < min=1.0 |     1 |       0.0% | TOWNSUSDT/15m/S7 |
| Microcap short blocked: price=0.497751 < min=1.0 |     1 |       0.0% | UMAUSDT/15m/S7 |
| Microcap short blocked: price=0.004658 < min=1.0 |     1 |       0.0% | USTCUSDT/15m/S7 |
| Microcap short blocked: price=0.013833 < min=1.0 |     1 |       0.0% | VELODROMEUSDT/15m/S7 |
| Microcap short blocked: price=0.007766 < min=1.0 |     1 |       0.0% | VETUSDT/15m/S7 |
| Microcap short blocked: price=0.054973 < min=1.0 |     1 |       0.0% | VICUSDT/15m/S7 |
| Microcap short blocked: price=0.558721 < min=1.0 |     1 |       0.0% | XNOUSDT/15m/S7 |
| Microcap short blocked: price=0.027086 < min=1.0 |     1 |       0.0% | AEVOUSDT/15m/S7 |
| Microcap short blocked: price=0.027386 < min=1.0 |     1 |       0.0% | CHRUSDT/15m/S7 |
| Microcap short blocked: price=0.039090 < min=1.0 |     1 |       0.0% | CHZUSDT/15m/S7 |
| Microcap short blocked: price=0.061069 < min=1.0 |     1 |       0.0% | TKOUSDT/15m/S7 |
| Microcap short blocked: price=0.020690 < min=1.0 |     1 |       0.0% | AUDIOUSDT/15m/S7 |
| Uneconomic: fees/R=1.42 > max 0.3 |     1 |       0.0% | FDUSDUSDT/15m/S7 |
| Microcap short blocked: price=0.188706 < min=1.0 |     1 |       0.0% | AVNTUSDT/15m/S7 |
| Microcap short blocked: price=0.270865 < min=1.0 |     1 |       0.0% | MTLUSDT/15m/S7 |
| Microcap short blocked: price=0.085657 < min=1.0 |     1 |       0.0% | SANDUSDT/15m/S7 |
| Microcap short blocked: price=0.012194 < min=1.0 |     1 |       0.0% | ATAUSDT/15m/S7 |
| Microcap short blocked: price=0.757621 < min=1.0 |     1 |       0.0% | ORCAUSDT/15m/S7 |
| Microcap short blocked: price=0.026887 < min=1.0 |     1 |       0.0% | AEVOUSDT/15m/S7 |
| Microcap short blocked: price=0.016212 < min=1.0 |     1 |       0.0% | BMTUSDT/15m/S7 |
| Microcap short blocked: price=0.040480 < min=1.0 |     1 |       0.0% | DYMUSDT/15m/S7 |
| Microcap short blocked: price=0.197901 < min=1.0 |     1 |       0.0% | EIGENUSDT/15m/S7 |
| Microcap short blocked: price=0.002439 < min=1.0 |     1 |       0.0% | ONEUSDT/15m/S7 |
| Microcap short blocked: price=0.006397 < min=1.0 |     1 |       0.0% | PEOPLEUSDT/15m/S7 |
| Microcap short blocked: price=0.036882 < min=1.0 |     1 |       0.0% | ACXUSDT/15m/S7 |
| Microcap short blocked: price=0.014193 < min=1.0 |     1 |       0.0% | DODOUSDT/15m/S7 |
| Microcap short blocked: price=0.094953 < min=1.0 |     1 |       0.0% | GTCUSDT/15m/S7 |
| Microcap short blocked: price=0.070265 < min=1.0 |     1 |       0.0% | HIVEUSDT/15m/S7 |
| Microcap short blocked: price=0.000385 < min=1.0 |     1 |       0.0% | HOTUSDT/15m/S7 |
| Microcap short blocked: price=0.262869 < min=1.0 |     1 |       0.0% | SFPUSDT/15m/S7 |
| Microcap short blocked: price=0.004188 < min=1.0 |     1 |       0.0% | TOWNSUSDT/15m/S7 |
| Microcap short blocked: price=0.010205 < min=1.0 |     1 |       0.0% | TUTUSDT/15m/S7 |
| Uneconomic: fees/R=1.64 > max 0.3 |     1 |       0.0% | USDCUSDT/15m/S7 |
| Microcap short blocked: price=0.021009 < min=1.0 |     1 |       0.0% | ZKUSDT/15m/S7 |
| Microcap short blocked: price=0.326837 < min=1.0 |     1 |       0.0% | PYRUSDT/15m/S7 |
| Microcap short blocked: price=0.028586 < min=1.0 |     1 |       0.0% | BANKUSDT/15m/S7 |
| Microcap short blocked: price=0.037181 < min=1.0 |     1 |       0.0% | OSMOUSDT/15m/S7 |
| Microcap short blocked: price=0.009725 < min=1.0 |     1 |       0.0% | ARPAUSDT/1h/S7 |
| Microcap short blocked: price=0.007366 < min=1.0 |     1 |       0.0% | ASTRUSDT/15m/S7 |
| Microcap short blocked: price=0.013493 < min=1.0 |     1 |       0.0% | NTRNUSDT/1h/S7 |
| Microcap short blocked: price=0.003088 < min=1.0 |     1 |       0.0% | QKCUSDT/1h/S7 |
| Adverse gap: R degraded 41% (min 50%) |     1 |       0.0% | RENDERUSDT/1h/S6 |
| Microcap short blocked: price=0.000625 < min=1.0 |     1 |       0.0% | SLPUSDT/1h/S7 |
| Microcap short blocked: price=0.026427 < min=1.0 |     1 |       0.0% | ZAMAUSDT/15m/S7 |
| Microcap short blocked: price=0.004028 < min=1.0 |     1 |       0.0% | DGBUSDT/15m/S7 |
| Microcap short blocked: price=0.084558 < min=1.0 |     1 |       0.0% | SANDUSDT/15m/S7 |
| Adverse gap: R degraded 25% (min 50%) |     1 |       0.0% | ALPINEUSDT/1h/S6 |
| Adverse gap: R degraded 10% (min 50%) |     1 |       0.0% | OPENUSDT/1h/S6 |
| Microcap short blocked: price=0.026537 < min=1.0 |     1 |       0.0% | SIGNUSDT/15m/S1 |
| Uneconomic: fees/R=3.51 > max 0.3 |     1 |       0.0% | AEURUSDT/15m/S7 |
| Microcap short blocked: price=0.452774 < min=1.0 |     1 |       0.0% | ACMUSDT/1h/S3 |
| Microcap short blocked: price=0.012764 < min=1.0 |     1 |       0.0% | BROCCOLI714USDT/1h/S1 |
| Stop too wide: 19.1% > max 10% |     1 |       0.0% | FTTUSDT/1h/S2 |
| Microcap short blocked: price=0.092154 < min=1.0 |     1 |       0.0% | ALGOUSDT/15m/S7 |
| Microcap short blocked: price=0.000384 < min=1.0 |     1 |       0.0% | BOMEUSDT/15m/S7 |
| Microcap short blocked: price=0.006997 < min=1.0 |     1 |       0.0% | TUSDT/15m/S3 |
| Microcap short blocked: price=0.224088 < min=1.0 |     1 |       0.0% | LAUSDT/15m/S7 |
| Microcap short blocked: price=0.309845 < min=1.0 |     1 |       0.0% | DEGOUSDT/1h/S7 |
| Microcap short blocked: price=0.266966 < min=1.0 |     1 |       0.0% | NXPCUSDT/15m/S7 |
| Microcap short blocked: price=0.168916 < min=1.0 |     1 |       0.0% | ACEUSDT/15m/S7 |
| Microcap short blocked: price=0.029885 < min=1.0 |     1 |       0.0% | SAGAUSDT/15m/S7 |
| Microcap short blocked: price=0.053973 < min=1.0 |     1 |       0.0% | STOUSDT/15m/S7 |
| Microcap short blocked: price=0.000490 < min=1.0 |     1 |       0.0% | 1000CHEEMSUSDT/15m/S7 |
| Microcap short blocked: price=0.207696 < min=1.0 |     1 |       0.0% | CTKUSDT/1h/S7 |
| Microcap short blocked: price=0.209095 < min=1.0 |     1 |       0.0% | CTKUSDT/15m/S7 |
| Microcap short blocked: price=0.003130 < min=1.0 |     1 |       0.0% | QKCUSDT/15m/S7 |
| Microcap short blocked: price=0.044378 < min=1.0 |     1 |       0.0% | RIFUSDT/15m/S3 |
| Microcap short blocked: price=0.026967 < min=1.0 |     1 |       0.0% | SENTUSDT/1h/S1 |
| Microcap short blocked: price=0.208995 < min=1.0 |     1 |       0.0% | CTKUSDT/15m/S7 |
| Microcap short blocked: price=0.083758 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.242878 < min=1.0 |     1 |       0.0% | BANDUSDT/15m/S7 |
| Microcap short blocked: price=0.007156 < min=1.0 |     1 |       0.0% | IDEXUSDT/15m/S7 |
| Microcap short blocked: price=0.036682 < min=1.0 |     1 |       0.0% | ACXUSDT/15m/S7 |
| Microcap short blocked: price=0.135432 < min=1.0 |     1 |       0.0% | EDUUSDT/1h/S7 |
| Microcap short blocked: price=0.973513 < min=1.0 |     1 |       0.0% | PORTOUSDT/15m/S7 |
| Stop too wide: 10.2% > max 10% |     1 |       0.0% | DUSKUSDT/15m/S2 |
| Microcap short blocked: price=0.042009 < min=1.0 |     1 |       0.0% | JSTUSDT/15m/S7 |
| Microcap short blocked: price=0.001547 < min=1.0 |     1 |       0.0% | AMPUSDT/15m/S7 |
| Microcap short blocked: price=0.014093 < min=1.0 |     1 |       0.0% | DODOUSDT/15m/S7 |
| Microcap short blocked: price=0.433783 < min=1.0 |     1 |       0.0% | RLCUSDT/15m/S7 |
| Microcap short blocked: price=0.004178 < min=1.0 |     1 |       0.0% | TOWNSUSDT/15m/S7 |
| Microcap short blocked: price=0.009475 < min=1.0 |     1 |       0.0% | UTKUSDT/15m/S7 |
| Microcap short blocked: price=0.000376 < min=1.0 |     1 |       0.0% | 1MBABYDOGEUSDT/15m/S7 |
| Microcap short blocked: price=0.001230 < min=1.0 |     1 |       0.0% | IOSTUSDT/15m/S7 |
| Microcap short blocked: price=0.447776 < min=1.0 |     1 |       0.0% | MASKUSDT/15m/S7 |
| Microcap short blocked: price=0.395802 < min=1.0 |     1 |       0.0% | RUNEUSDT/15m/S7 |
| Microcap short blocked: price=0.004448 < min=1.0 |     1 |       0.0% | SOLVUSDT/15m/S7 |
| Microcap short blocked: price=0.077461 < min=1.0 |     1 |       0.0% | ADXUSDT/1h/S7 |
| Microcap short blocked: price=0.004348 < min=1.0 |     1 |       0.0% | ANKRUSDT/1h/S7 |
| Microcap short blocked: price=0.007316 < min=1.0 |     1 |       0.0% | ASTRUSDT/1h/S7 |
| Microcap short blocked: price=0.029585 < min=1.0 |     1 |       0.0% | BBUSDT/4h/S7 |
| Microcap short blocked: price=0.038641 < min=1.0 |     1 |       0.0% | CHZUSDT/1h/S7 |
| Microcap short blocked: price=0.204298 < min=1.0 |     1 |       0.0% | CTKUSDT/1h/S7 |
| Microcap short blocked: price=0.526737 < min=1.0 |     1 |       0.0% | CYBERUSDT/1h/S1 |
| Microcap short blocked: price=0.091694 < min=1.0 |     1 |       0.0% | DOGEUSDT/1h/S7 |
| Microcap short blocked: price=0.025187 < min=1.0 |     1 |       0.0% | HOOKUSDT/1h/S7 |
| Microcap short blocked: price=0.028736 < min=1.0 |     1 |       0.0% | KMNOUSDT/1h/S7 |
| Microcap short blocked: price=0.333033 < min=1.0 |     1 |       0.0% | LDOUSDT/1h/S7 |
| Microcap short blocked: price=0.000654 < min=1.0 |     1 |       0.0% | MEMEUSDT/4h/S7 |
| Microcap short blocked: price=0.134833 < min=1.0 |     1 |       0.0% | MEUSDT/1h/S7 |
| Microcap short blocked: price=0.123938 < min=1.0 |     1 |       0.0% | MMTUSDT/1h/S7 |
| Microcap short blocked: price=0.977511 < min=1.0 |     1 |       0.0% | NEARUSDT/1h/S7 |
| Microcap short blocked: price=0.006025 < min=1.0 |     1 |       0.0% | PENGUUSDT/1h/S7 |
| Microcap short blocked: price=0.428786 < min=1.0 |     1 |       0.0% | RLCUSDT/1h/S7 |
| Microcap short blocked: price=0.005587 < min=1.0 |     1 |       0.0% | RVNUSDT/1h/S7 |
| Microcap short blocked: price=0.078761 < min=1.0 |     1 |       0.0% | SAPIENUSDT/1h/S7 |
| Microcap short blocked: price=0.000618 < min=1.0 |     1 |       0.0% | SLPUSDT/1h/S7 |
| Microcap short blocked: price=0.911544 < min=1.0 |     1 |       0.0% | SUIUSDT/1h/S7 |
| Microcap short blocked: price=0.063868 < min=1.0 |     1 |       0.0% | TREEUSDT/1h/S7 |
| Microcap short blocked: price=0.449175 < min=1.0 |     1 |       0.0% | TWTUSDT/4h/S7 |
| Microcap short blocked: price=0.001626 < min=1.0 |     1 |       0.0% | CKBUSDT/15m/S7 |
| Microcap short blocked: price=0.000381 < min=1.0 |     1 |       0.0% | HOTUSDT/15m/S7 |
| Microcap short blocked: price=0.019990 < min=1.0 |     1 |       0.0% | MBOXUSDT/15m/S7 |
| Microcap short blocked: price=0.044078 < min=1.0 |     1 |       0.0% | OMUSDT/15m/S7 |
| Microcap short blocked: price=0.062969 < min=1.0 |     1 |       0.0% | ONGUSDT/15m/S7 |
| Microcap short blocked: price=0.000553 < min=1.0 |     1 |       0.0% | VTHOUSDT/15m/S7 |
| Microcap short blocked: price=0.078261 < min=1.0 |     1 |       0.0% | WALUSDT/15m/S7 |
| Microcap short blocked: price=0.000902 < min=1.0 |     1 |       0.0% | A2ZUSDT/15m/S7 |
| Microcap short blocked: price=0.024588 < min=1.0 |     1 |       0.0% | BIOUSDT/15m/S7 |
| Microcap short blocked: price=0.035882 < min=1.0 |     1 |       0.0% | JOEUSDT/15m/S7 |
| Microcap short blocked: price=0.883558 < min=1.0 |     1 |       0.0% | QTUMUSDT/15m/S7 |
| Microcap short blocked: price=0.002879 < min=1.0 |     1 |       0.0% | REZUSDT/15m/S7 |
| Microcap short blocked: price=0.077961 < min=1.0 |     1 |       0.0% | WALUSDT/15m/S7 |
| Microcap short blocked: price=0.305847 < min=1.0 |     1 |       0.0% | DEGOUSDT/15m/S7 |
| Microcap short blocked: price=0.275662 < min=1.0 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Microcap short blocked: price=0.392404 < min=1.0 |     1 |       0.0% | XTZUSDT/15m/S7 |
| Microcap short blocked: price=0.000667 < min=1.0 |     1 |       0.0% | MEMEUSDT/1h/S7 |
| Microcap short blocked: price=0.000668 < min=1.0 |     1 |       0.0% | MEMEUSDT/15m/S7 |
| Microcap short blocked: price=0.264868 < min=1.0 |     1 |       0.0% | MTLUSDT/1h/S7 |
| Microcap short blocked: price=0.006037 < min=1.0 |     1 |       0.0% | NOMUSDT/1h/S7 |
| Microcap short blocked: price=0.001641 < min=1.0 |     1 |       0.0% | TLMUSDT/1h/S7 |
| Microcap short blocked: price=0.007546 < min=1.0 |     1 |       0.0% | VETUSDT/1h/S7 |
| Microcap short blocked: price=0.000379 < min=1.0 |     1 |       0.0% | BOMEUSDT/15m/S7 |
| Microcap short blocked: price=0.263868 < min=1.0 |     1 |       0.0% | MTLUSDT/15m/S7 |
| Microcap short blocked: price=0.077561 < min=1.0 |     1 |       0.0% | WALUSDT/15m/S7 |
| Microcap short blocked: price=0.000375 < min=1.0 |     1 |       0.0% | HOTUSDT/15m/S7 |
| Microcap short blocked: price=0.000476 < min=1.0 |     1 |       0.0% | 1000CHEEMSUSDT/15m/S7 |
| Microcap short blocked: price=0.165917 < min=1.0 |     1 |       0.0% | ACEUSDT/15m/S7 |
| Microcap short blocked: price=0.025087 < min=1.0 |     1 |       0.0% | AEVOUSDT/15m/S7 |
| Microcap short blocked: price=0.018691 < min=1.0 |     1 |       0.0% | AIXBTUSDT/15m/S7 |
| Microcap short blocked: price=0.043868 < min=1.0 |     1 |       0.0% | ARDRUSDT/15m/S7 |
| Microcap short blocked: price=0.013863 < min=1.0 |     1 |       0.0% | BIGTIMEUSDT/15m/S7 |
| Microcap short blocked: price=0.079860 < min=1.0 |     1 |       0.0% | CELOUSDT/1h/S7 |
| Microcap short blocked: price=0.096752 < min=1.0 |     1 |       0.0% | DYDXUSDT/1h/S7 |
| Microcap short blocked: price=0.426787 < min=1.0 |     1 |       0.0% | ETHFIUSDT/15m/S7 |
| Microcap short blocked: price=0.088526 < min=1.0 |     1 |       0.0% | HBARUSDT/15m/S7 |
| Microcap short blocked: price=0.047876 < min=1.0 |     1 |       0.0% | IDUSDT/15m/S7 |
| Microcap short blocked: price=0.035482 < min=1.0 |     1 |       0.0% | JOEUSDT/15m/S7 |
| Microcap short blocked: price=0.010685 < min=1.0 |     1 |       0.0% | MDTUSDT/1h/S1 |
| Microcap short blocked: price=0.005987 < min=1.0 |     1 |       0.0% | NOMUSDT/1h/S7 |
| Microcap short blocked: price=0.000378 < min=1.0 |     1 |       0.0% | NOTUSDT/15m/S7 |
| Microcap short blocked: price=0.043278 < min=1.0 |     1 |       0.0% | OMUSDT/15m/S7 |
| Microcap short blocked: price=0.002399 < min=1.0 |     1 |       0.0% | ONEUSDT/15m/S7 |
| Microcap short blocked: price=0.005127 < min=1.0 |     1 |       0.0% | PIXELUSDT/15m/S7 |
| Microcap short blocked: price=0.274863 < min=1.0 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Microcap short blocked: price=0.476762 < min=1.0 |     1 |       0.0% | UMAUSDT/15m/S7 |
| Microcap short blocked: price=0.000543 < min=1.0 |     1 |       0.0% | VTHOUSDT/15m/S7 |
| Microcap short blocked: price=0.254173 < min=1.0 |     1 |       0.0% | SFPUSDT/15m/S7 |
| Microcap short blocked: price=0.001201 < min=1.0 |     1 |       0.0% | IOSTUSDT/15m/S7 |
| Microcap short blocked: price=0.001010 < min=1.0 |     1 |       0.0% | COSUSDT/15m/S7 |
| Microcap short blocked: price=0.051374 < min=1.0 |     1 |       0.0% | STOUSDT/15m/S7 |
| Microcap short blocked: price=0.004008 < min=1.0 |     1 |       0.0% | DGBUSDT/15m/S7 |
| Microcap short blocked: price=0.020490 < min=1.0 |     1 |       0.0% | MOVEUSDT/15m/S7 |
| Microcap short blocked: price=0.002819 < min=1.0 |     1 |       0.0% | REZUSDT/15m/S7 |
| Microcap short blocked: price=0.449675 < min=1.0 |     1 |       0.0% | TWTUSDT/15m/S7 |
| Microcap short blocked: price=0.013593 < min=1.0 |     1 |       0.0% | ACTUSDT/15m/S7 |
| Microcap short blocked: price=0.005507 < min=1.0 |     1 |       0.0% | RDNTUSDT/15m/S7 |
| Microcap short blocked: price=0.018191 < min=1.0 |     1 |       0.0% | WUSDT/1h/S7 |
| Microcap short blocked: price=0.014103 < min=1.0 |     1 |       0.0% | TFUELUSDT/15m/S7 |
| Microcap short blocked: price=0.010265 < min=1.0 |     1 |       0.0% | PLUMEUSDT/15m/S7 |
| Microcap short blocked: price=0.059770 < min=1.0 |     1 |       0.0% | TKOUSDT/15m/S7 |
| Microcap short blocked: price=0.000366 < min=1.0 |     1 |       0.0% | HOTUSDT/15m/S7 |
| Microcap short blocked: price=0.041499 < min=1.0 |     1 |       0.0% | MITOUSDT/15m/S7 |
| Microcap short blocked: price=0.664468 < min=1.0 |     1 |       0.0% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.000927 < min=1.0 |     1 |       0.0% | MBLUSDT/15m/S7 |
| Microcap short blocked: price=0.726637 < min=1.0 |     1 |       0.0% | ORCAUSDT/15m/S7 |
| Microcap short blocked: price=0.150925 < min=1.0 |     1 |       0.0% | XLMUSDT/1h/S7 |
| Microcap short blocked: price=0.181509 < min=1.0 |     1 |       0.0% | YBUSDT/15m/S3 |
| Stop too wide: 25.9% > max 10% |     1 |       0.0% | ACEUSDT/1h/S2 |
| Stop too wide: 19.0% > max 10% |     1 |       0.0% | ACEUSDT/15m/S2 |
| Adverse gap: R degraded 35% (min 50%) |     1 |       0.0% | ENJUSDT/1h/S7 |
| Stop too wide: 23.7% > max 10% |     1 |       0.0% | TNSRUSDT/1h/S2 |
| Microcap short blocked: price=0.003698 < min=1.0 |     1 |       0.0% | ACAUSDT/15m/S7 |
| Stop too wide: 40.4% > max 10% |     1 |       0.0% | DYMUSDT/1h/S2 |
| Stop too wide: 40.1% > max 10% |     1 |       0.0% | DYMUSDT/1h/S2 |
| Microcap short blocked: price=0.004048 < min=1.0 |     1 |       0.0% | DGBUSDT/15m/S7 |
| Uneconomic: fees/R=0.66 > max 0.3 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Microcap short blocked: price=0.188006 < min=1.0 |     1 |       0.0% | OPENUSDT/15m/S3 |
| Uneconomic: fees/R=0.97 > max 0.3 |     1 |       0.0% | DEGOUSDT/15m/S7 |
| Uneconomic: fees/R=0.60 > max 0.3 |     1 |       0.0% | QUICKUSDT/15m/S7 |
| Uneconomic: fees/R=0.67 > max 0.3 |     1 |       0.0% | SUNUSDT/15m/S7 |
| Uneconomic: fees/R=13.40 > max 0.3 |     1 |       0.0% | CTKUSDT/15m/S7 |
| Adverse gap: R degraded 49% (min 50%) |     1 |       0.0% | MAGICUSDT/1h/S6 |
| Microcap short blocked: price=0.088356 < min=1.0 |     1 |       0.0% | LAYERUSDT/15m/S3 |
| Microcap short blocked: price=0.224188 < min=1.0 |     1 |       0.0% | LAUSDT/15m/S7 |
| Microcap short blocked: price=0.094553 < min=1.0 |     1 |       0.0% | ZKCUSDT/15m/S3 |
| Microcap short blocked: price=0.002631 < min=1.0 |     1 |       0.0% | CELRUSDT/15m/S3 |
| Uneconomic: fees/R=2.54 > max 0.3 |     1 |       0.0% | ARUSDT/15m/S7 |
| Microcap short blocked: price=0.032644 < min=1.0 |     1 |       0.0% | CVCUSDT/15m/S7 |
| Microcap short blocked: price=0.005647 < min=1.0 |     1 |       0.0% | IOTXUSDT/15m/S7 |
| Microcap short blocked: price=0.001063 < min=1.0 |     1 |       0.0% | COSUSDT/15m/S7 |
| Microcap short blocked: price=0.017391 < min=1.0 |     1 |       0.0% | OXTUSDT/15m/S7 |
| Microcap short blocked: price=0.596701 < min=1.0 |     1 |       0.0% | RAYUSDT/1h/S3 |
| Microcap short blocked: price=0.184008 < min=1.0 |     1 |       0.0% | KITEUSDT/15m/S3 |
| Microcap short blocked: price=0.011014 < min=1.0 |     1 |       0.0% | MDTUSDT/15m/S7 |
| Microcap short blocked: price=0.021379 < min=1.0 |     1 |       0.0% | OGNUSDT/15m/S7 |
| Microcap short blocked: price=0.009755 < min=1.0 |     1 |       0.0% | UTKUSDT/15m/S7 |
| Microcap short blocked: price=0.431784 < min=1.0 |     1 |       0.0% | RLCUSDT/15m/S7 |
| Microcap short blocked: price=0.389805 < min=1.0 |     1 |       0.0% | RUNEUSDT/1h/S7 |
| Microcap short blocked: price=0.074363 < min=1.0 |     1 |       0.0% | REQUSDT/15m/S7 |
| Microcap short blocked: price=0.161319 < min=1.0 |     1 |       0.0% | XLMUSDT/15m/S3 |
| Uneconomic: fees/R=6.74 > max 0.3 |     1 |       0.0% | XVSUSDT/15m/S7 |
| Microcap short blocked: price=0.089355 < min=1.0 |     1 |       0.0% | ALGOUSDT/15m/S7 |
| Microcap short blocked: price=0.112944 < min=1.0 |     1 |       0.0% | ARKMUSDT/1h/S7 |
| Microcap short blocked: price=0.217191 < min=1.0 |     1 |       0.0% | AVAUSDT/15m/S7 |
| Microcap short blocked: price=0.231684 < min=1.0 |     1 |       0.0% | CRVUSDT/15m/S7 |
| Microcap short blocked: price=0.196902 < min=1.0 |     1 |       0.0% | EIGENUSDT/15m/S7 |
| Microcap short blocked: price=0.079650 < min=1.0 |     1 |       0.0% | FFUSDT/15m/S7 |
| Microcap short blocked: price=0.091834 < min=1.0 |     1 |       0.0% | HBARUSDT/15m/S7 |
| Microcap short blocked: price=0.137731 < min=1.0 |     1 |       0.0% | JUPUSDT/15m/S7 |
| Microcap short blocked: price=0.062869 < min=1.0 |     1 |       0.0% | LUMIAUSDT/15m/S7 |
| Microcap short blocked: price=0.000033 < min=1.0 |     1 |       0.0% | LUNCUSDT/15m/S7 |
| Microcap short blocked: price=0.042349 < min=1.0 |     1 |       0.0% | MITOUSDT/15m/S7 |
| Microcap short blocked: price=0.269865 < min=1.0 |     1 |       0.0% | MTLUSDT/15m/S7 |
| Microcap short blocked: price=0.943528 < min=1.0 |     1 |       0.0% | NEARUSDT/15m/S7 |
| Microcap short blocked: price=0.016792 < min=1.0 |     1 |       0.0% | NFPUSDT/15m/S7 |
| Microcap short blocked: price=0.245077 < min=1.0 |     1 |       0.0% | ONDOUSDT/15m/S7 |
| Adverse gap: R degraded 46% (min 50%) |     1 |       0.0% | PROMUSDT/1h/S6 |
| Microcap short blocked: price=0.002929 < min=1.0 |     1 |       0.0% | REZUSDT/1h/S7 |
| Microcap short blocked: price=0.386807 < min=1.0 |     1 |       0.0% | RUNEUSDT/1h/S7 |
| Microcap short blocked: price=0.048546 < min=1.0 |     1 |       0.0% | SCRUSDT/15m/S7 |
| Microcap short blocked: price=0.014463 < min=1.0 |     1 |       0.0% | TFUELUSDT/15m/S7 |
| Microcap short blocked: price=0.009845 < min=1.0 |     1 |       0.0% | TSTUSDT/1h/S7 |
| Microcap short blocked: price=0.001009 < min=1.0 |     1 |       0.0% | TURBOUSDT/1h/S7 |
| Microcap short blocked: price=0.007806 < min=1.0 |     1 |       0.0% | VETUSDT/15m/S7 |
| Microcap short blocked: price=0.079460 < min=1.0 |     1 |       0.0% | WALUSDT/15m/S7 |
| Microcap short blocked: price=0.207896 < min=1.0 |     1 |       0.0% | WIFUSDT/15m/S7 |
| Microcap short blocked: price=0.389205 < min=1.0 |     1 |       0.0% | XTZUSDT/15m/S7 |
| Microcap short blocked: price=0.423788 < min=1.0 |     1 |       0.0% | ALPINEUSDT/15m/S7 |
| Microcap short blocked: price=0.001601 < min=1.0 |     1 |       0.0% | CKBUSDT/15m/S7 |
| Microcap short blocked: price=0.179410 < min=1.0 |     1 |       0.0% | REDUSDT/15m/S7 |
| Microcap short blocked: price=0.250075 < min=1.0 |     1 |       0.0% | SFPUSDT/15m/S7 |
| Microcap short blocked: price=0.003688 < min=1.0 |     1 |       0.0% | TOWNSUSDT/15m/S7 |
| Microcap short blocked: price=0.482759 < min=1.0 |     1 |       0.0% | UMAUSDT/15m/S7 |
| Microcap short blocked: price=0.016292 < min=1.0 |     1 |       0.0% | WOOUSDT/15m/S7 |
| Microcap short blocked: price=0.276362 < min=1.0 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Microcap short blocked: price=0.000362 < min=1.0 |     1 |       0.0% | HOTUSDT/1h/S7 |
| Uneconomic: fees/R=1.92 > max 0.3 |     1 |       0.0% | USDEUSDT/1h/S7 |
| Microcap short blocked: price=0.784608 < min=1.0 |     1 |       0.0% | NEXOUSDT/15m/S7 |
| Microcap short blocked: price=0.002799 < min=1.0 |     1 |       0.0% | ACAUSDT/15m/S7 |
| Microcap short blocked: price=0.035282 < min=1.0 |     1 |       0.0% | ACXUSDT/15m/S7 |
| Microcap short blocked: price=0.001999 < min=1.0 |     1 |       0.0% | ACAUSDT/1h/S7 |
| Microcap short blocked: price=0.734633 < min=1.0 |     1 |       0.0% | ORCAUSDT/1h/S7 |
| Microcap short blocked: price=0.298651 < min=1.0 |     1 |       0.0% | API3USDT/15m/S7 |
| Microcap short blocked: price=0.001598 < min=1.0 |     1 |       0.0% | CKBUSDT/15m/S7 |
| Microcap short blocked: price=0.097451 < min=1.0 |     1 |       0.0% | MANAUSDT/15m/S7 |
| Microcap short blocked: price=0.999100 < min=1.0 |     1 |       0.0% | BFUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.005427 < min=1.0 |     1 |       0.0% | IOTXUSDT/15m/S7 |
| Microcap short blocked: price=0.090855 < min=1.0 |     1 |       0.0% | LISTAUSDT/1h/S7 |
| Microcap short blocked: price=0.051874 < min=1.0 |     1 |       0.0% | CUSDT/15m/S7 |
| Microcap short blocked: price=0.046377 < min=1.0 |     1 |       0.0% | SYNUSDT/15m/S7 |
| Microcap short blocked: price=0.101749 < min=1.0 |     1 |       0.0% | WLFIUSDT/15m/S7 |
| Microcap short blocked: price=0.024388 < min=1.0 |     1 |       0.0% | BICOUSDT/15m/S7 |
| Microcap short blocked: price=0.083158 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.000183 < min=1.0 |     1 |       0.0% | SPELLUSDT/15m/S7 |
| Microcap short blocked: price=0.008936 < min=1.0 |     1 |       0.0% | FIOUSDT/15m/S7 |
| Microcap short blocked: price=0.082759 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.082559 < min=1.0 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.025287 < min=1.0 |     1 |       0.0% | HOOKUSDT/15m/S7 |
| Microcap short blocked: price=0.020890 < min=1.0 |     1 |       0.0% | OGNUSDT/15m/S7 |
| Microcap short blocked: price=0.000182 < min=1.0 |     1 |       0.0% | SPELLUSDT/15m/S7 |
| Microcap short blocked: price=0.999000 < min=1.0 |     1 |       0.0% | BFUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.091904 < min=1.0 |     1 |       0.0% | DOGEUSDT/15m/S7 |
| Microcap short blocked: price=0.100950 < min=1.0 |     1 |       0.0% | DYDXUSDT/15m/S7 |
| Microcap short blocked: price=0.008836 < min=1.0 |     1 |       0.0% | FIOUSDT/15m/S7 |
| Uneconomic: fees/R=1.86 > max 0.3 |     1 |       0.0% | EURIUSDT/15m/S7 |
| Uneconomic: fees/R=13.20 > max 0.3 |     1 |       0.0% | ZENUSDT/15m/S7 |
| Microcap short blocked: price=0.462769 < min=1.0 |     1 |       0.0% | ACMUSDT/15m/S3 |
| Microcap short blocked: price=0.168716 < min=1.0 |     1 |       0.0% | PUNDIXUSDT/15m/S3 |
| Microcap short blocked: price=0.130535 < min=1.0 |     1 |       0.0% | EDUUSDT/15m/S7 |
| Adverse gap: R degraded 37% (min 50%) |     1 |       0.0% | XECUSDT/1h/S6 |
| Microcap short blocked: price=0.027936 < min=1.0 |     1 |       0.0% | HOMEUSDT/15m/S3 |
| Uneconomic: fees/R=0.95 > max 0.3 |     1 |       0.0% | ETCUSDT/15m/S7 |
| Microcap short blocked: price=0.169415 < min=1.0 |     1 |       0.0% | PUNDIXUSDT/1h/S3 |
| Microcap short blocked: price=0.002939 < min=1.0 |     1 |       0.0% | REZUSDT/15m/S7 |
| Microcap short blocked: price=0.025587 < min=1.0 |     1 |       0.0% | PHAUSDT/15m/S7 |
| Microcap short blocked: price=0.051174 < min=1.0 |     1 |       0.0% | CFXUSDT/15m/S3 |
| Microcap short blocked: price=0.211894 < min=1.0 |     1 |       0.0% | WIFUSDT/1h/S7 |
| Uneconomic: fees/R=3.14 > max 0.3 |     1 |       0.0% | ALTUSDT/1h/S7 |
| Uneconomic: fees/R=4.15 > max 0.3 |     1 |       0.0% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=3.82 > max 0.3 |     1 |       0.0% | QTUMUSDT/1h/S7 |
| Microcap short blocked: price=0.104548 < min=1.0 |     1 |       0.0% | RONINUSDT/15m/S3 |
| Microcap short blocked: price=0.015412 < min=1.0 |     1 |       0.0% | USUALUSDT/15m/S3 |
| Microcap short blocked: price=0.632684 < min=1.0 |     1 |       0.0% | 0GUSDT/15m/S3 |
| Microcap short blocked: price=0.103148 < min=1.0 |     1 |       0.0% | RONINUSDT/15m/S3 |
| Microcap short blocked: price=0.176812 < min=1.0 |     1 |       0.0% | OPENUSDT/15m/S3 |
| Microcap short blocked: price=0.001623 < min=1.0 |     1 |       0.0% | RSRUSDT/15m/S3 |
| Microcap short blocked: price=0.007516 < min=1.0 |     1 |       0.0% | WAXPUSDT/15m/S3 |
| Microcap short blocked: price=0.026787 < min=1.0 |     1 |       0.0% | CHRUSDT/15m/S7 |
| Microcap short blocked: price=0.016722 < min=1.0 |     1 |       0.0% | MAVUSDT/15m/S7 |
| Microcap short blocked: price=0.041979 < min=1.0 |     1 |       0.0% | CATIUSDT/15m/S7 |
| Adverse gap: R degraded 20% (min 50%) |     1 |       0.0% | TFUELUSDT/1h/S6 |
| Microcap short blocked: price=0.012484 < min=1.0 |     1 |       0.0% | ROSEUSDT/15m/S7 |
| Microcap short blocked: price=0.022089 < min=1.0 |     1 |       0.0% | SXTUSDT/15m/S7 |
| Uneconomic: fees/R=0.86 > max 0.3 |     1 |       0.0% | AVAUSDT/15m/S7 |
| Adverse gap: R degraded 21% (min 50%) |     1 |       0.0% | NEXOUSDT/1h/S6 |
| Microcap short blocked: price=0.073863 < min=1.0 |     1 |       0.0% | HIVEUSDT/15m/S3 |
| Microcap short blocked: price=0.027216 < min=1.0 |     1 |       0.0% | HOMEUSDT/15m/S7 |
| Microcap short blocked: price=0.003888 < min=1.0 |     1 |       0.0% | TOWNSUSDT/15m/S7 |
| Adverse gap: R degraded 32% (min 50%) |     1 |       0.0% | SKLUSDT/1h/S6 |
| Microcap short blocked: price=0.000504 < min=1.0 |     1 |       0.0% | 1000CHEEMSUSDT/15m/S7 |
| Microcap short blocked: price=0.033783 < min=1.0 |     1 |       0.0% | RIFUSDT/15m/S7 |
| Microcap short blocked: price=0.070665 < min=1.0 |     1 |       0.0% | FLUXUSDT/15m/S7 |
| Microcap short blocked: price=0.033183 < min=1.0 |     1 |       0.0% | RIFUSDT/15m/S7 |
| Microcap short blocked: price=0.021989 < min=1.0 |     1 |       0.0% | SXTUSDT/15m/S7 |
| Uneconomic: fees/R=1.72 > max 0.3 |     1 |       0.0% | FDUSDUSDT/15m/S7 |
| Uneconomic: fees/R=2.57 > max 0.3 |     1 |       0.0% | KAVAUSDT/15m/S7 |
| Uneconomic: fees/R=0.90 > max 0.3 |     1 |       0.0% | XTZUSDT/15m/S7 |
| Uneconomic: fees/R=0.87 > max 0.3 |     1 |       0.0% | EURUSDT/15m/S6 |
| Uneconomic: fees/R=1.81 > max 0.3 |     1 |       0.0% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=2.07 > max 0.3 |     1 |       0.0% | EURUSDT/15m/S7 |
| Uneconomic: fees/R=1.93 > max 0.3 |     1 |       0.0% | GALAUSDT/15m/S7 |
| Uneconomic: fees/R=7.07 > max 0.3 |     1 |       0.0% | CUSDT/15m/S7 |
| Uneconomic: fees/R=1.43 > max 0.3 |     1 |       0.0% | EURUSDT/15m/S7 |
| Uneconomic: fees/R=2.23 > max 0.3 |     1 |       0.0% | QTUMUSDT/1h/S7 |
| Microcap short blocked: price=0.636682 < min=1.0 |     1 |       0.0% | FRAXUSDT/15m/S7 |
| Uneconomic: fees/R=1.20 > max 0.3 |     1 |       0.0% | EURUSDT/1h/S7 |
| Microcap short blocked: price=0.042419 < min=1.0 |     1 |       0.0% | JSTUSDT/15m/S3 |
| Uneconomic: fees/R=0.65 > max 0.3 |     1 |       0.0% | CVXUSDT/15m/S7 |
| Uneconomic: fees/R=1.11 > max 0.3 |     1 |       0.0% | SHELLUSDT/15m/S7 |
| Uneconomic: fees/R=7.83 > max 0.3 |     1 |       0.0% | EURUSDT/4h/S7 |
| Microcap short blocked: price=0.281259 < min=1.0 |     1 |       0.0% | ONDOUSDT/15m/S3 |
| Microcap short blocked: price=0.993203 < min=1.0 |     1 |       0.0% | SUIUSDT/15m/S3 |
| Microcap short blocked: price=0.998800 < min=1.0 |     1 |       0.0% | FDUSDUSDT/15m/S7 |
| Microcap short blocked: price=0.980510 < min=1.0 |     1 |       0.0% | SUIUSDT/15m/S3 |
| Microcap short blocked: price=0.164918 < min=1.0 |     1 |       0.0% | ACEUSDT/15m/S7 |
| Microcap short blocked: price=0.022879 < min=1.0 |     1 |       0.0% | CGPTUSDT/15m/S3 |
| Uneconomic: fees/R=1.12 > max 0.3 |     1 |       0.0% | ALICEUSDT/15m/S7 |
| Microcap short blocked: price=0.226287 < min=1.0 |     1 |       0.0% | METUSDT/15m/S3 |
| Microcap short blocked: price=0.109845 < min=1.0 |     1 |       0.0% | DYDXUSDT/15m/S3 |
| Uneconomic: fees/R=3.28 > max 0.3 |     1 |       0.0% | GUSDT/15m/S7 |
| Uneconomic: fees/R=0.46 > max 0.3 |     1 |       0.0% | SCRTUSDT/15m/S7 |
| Uneconomic: fees/R=1.21 > max 0.3 |     1 |       0.0% | HEIUSDT/15m/S7 |
| Microcap short blocked: price=0.071364 < min=1.0 |     1 |       0.0% | INITUSDT/15m/S3 |
| Microcap short blocked: price=0.015302 < min=1.0 |     1 |       0.0% | PORTALUSDT/15m/S3 |
| Microcap short blocked: price=0.107546 < min=1.0 |     1 |       0.0% | WLFIUSDT/1h/S3 |
| Microcap short blocked: price=0.000454 < min=1.0 |     1 |       0.0% | BOMEUSDT/15m/S3 |
| Microcap short blocked: price=0.107846 < min=1.0 |     1 |       0.0% | MANAUSDT/15m/S3 |
| Microcap short blocked: price=0.033383 < min=1.0 |     1 |       0.0% | RIFUSDT/15m/S7 |
| Uneconomic: fees/R=4.64 > max 0.3 |     1 |       0.0% | CATIUSDT/15m/S7 |
| Microcap short blocked: price=0.001132 < min=1.0 |     1 |       0.0% | COSUSDT/15m/S3 |
| Microcap short blocked: price=0.222289 < min=1.0 |     1 |       0.0% | CTKUSDT/15m/S3 |
| Microcap short blocked: price=0.661669 < min=1.0 |     1 |       0.0% | RAYUSDT/15m/S3 |
| Microcap short blocked: price=0.070765 < min=1.0 |     1 |       0.0% | POWRUSDT/15m/S3 |
| Adverse gap: R degraded 16% (min 50%) |     1 |       0.0% | SHIBUSDT/1h/S6 |
| Microcap short blocked: price=0.016192 < min=1.0 |     1 |       0.0% | ACTUSDT/15m/S3 |
| Microcap short blocked: price=0.015352 < min=1.0 |     1 |       0.0% | PORTALUSDT/15m/S3 |
| Microcap short blocked: price=0.000031 < min=1.0 |     1 |       0.0% | DOGSUSDT/15m/S3 |
| Microcap short blocked: price=0.110635 < min=1.0 |     1 |       0.0% | DOGEUSDT/15m/S3 |
| Adverse gap: R degraded 48% (min 50%) |     1 |       0.0% | MBLUSDT/1h/S6 |
| Microcap short blocked: price=0.221489 < min=1.0 |     1 |       0.0% | LAUSDT/15m/S7 |
| Microcap short blocked: price=0.058811 < min=1.0 |     1 |       0.0% | ESPUSDT/15m/S7 |
| Microcap short blocked: price=0.001253 < min=1.0 |     1 |       0.0% | FUNUSDT/15m/S7 |
| Microcap short blocked: price=0.275862 < min=1.0 |     1 |       0.0% | LQTYUSDT/15m/S7 |
| Microcap short blocked: price=0.032284 < min=1.0 |     1 |       0.0% | RIFUSDT/1h/S7 |
| Microcap short blocked: price=0.272864 < min=1.0 |     1 |       0.0% | LQTYUSDT/15m/S7 |
| Microcap short blocked: price=0.033883 < min=1.0 |     1 |       0.0% | LRCUSDT/15m/S7 |
| Uneconomic: fees/R=1.33 > max 0.3 |     1 |       0.0% | PAXGUSDT/15m/S7 |
| Uneconomic: fees/R=16.72 > max 0.3 |     1 |       0.0% | PAXGUSDT/1h/S7 |
| Uneconomic: fees/R=2.09 > max 0.3 |     1 |       0.0% | MTLUSDT/15m/S7 |
| Uneconomic: fees/R=2.76 > max 0.3 |     1 |       0.0% | NXPCUSDT/15m/S7 |
| Adverse gap: R degraded 34% (min 50%) |     1 |       0.0% | LUNCUSDT/1h/S6 |
| Uneconomic: fees/R=10.70 > max 0.3 |     1 |       0.0% | WANUSDT/15m/S7 |
| Microcap short blocked: price=0.159120 < min=1.0 |     1 |       0.0% | ATUSDT/1h/S7 |
| Microcap short blocked: price=0.046477 < min=1.0 |     1 |       0.0% | DYMUSDT/15m/S7 |
| Microcap short blocked: price=0.054873 < min=1.0 |     1 |       0.0% | NILUSDT/15m/S7 |
| Microcap short blocked: price=0.041779 < min=1.0 |     1 |       0.0% | CATIUSDT/15m/S7 |
| Microcap short blocked: price=0.080560 < min=1.0 |     1 |       0.0% | 2ZUSDT/1h/S3 |
| Uneconomic: fees/R=0.92 > max 0.3 |     1 |       0.0% | LUNCUSDT/15m/S7 |
| Microcap short blocked: price=0.077661 < min=1.0 |     1 |       0.0% | 2ZUSDT/1h/S7 |
| Microcap short blocked: price=0.032184 < min=1.0 |     1 |       0.0% | BBUSDT/15m/S7 |
| Microcap short blocked: price=0.146926 < min=1.0 |     1 |       0.0% | MEUSDT/1h/S7 |
| Microcap short blocked: price=0.269965 < min=1.0 |     1 |       0.0% | STXUSDT/1h/S7 |
| Microcap short blocked: price=0.785607 < min=1.0 |     1 |       0.0% | BARDUSDT/15m/S7 |
| Microcap short blocked: price=0.024488 < min=1.0 |     1 |       0.0% | BICOUSDT/15m/S7 |
| Microcap short blocked: price=0.333833 < min=1.0 |     1 |       0.0% | DEGOUSDT/15m/S2 |
| Microcap short blocked: price=0.101949 < min=1.0 |     1 |       0.0% | DUSKUSDT/15m/S7 |
| Microcap short blocked: price=0.045077 < min=1.0 |     1 |       0.0% | DYMUSDT/15m/S7 |
| Microcap short blocked: price=0.006257 < min=1.0 |     1 |       0.0% | FUSDT/15m/S7 |
| Microcap short blocked: price=0.004058 < min=1.0 |     1 |       0.0% | GUSDT/15m/S7 |
| Microcap short blocked: price=0.017791 < min=1.0 |     1 |       0.0% | HFTUSDT/15m/S7 |
| Microcap short blocked: price=0.314643 < min=1.0 |     1 |       0.0% | KAITOUSDT/15m/S7 |
| Microcap short blocked: price=0.212094 < min=1.0 |     1 |       0.0% | LAUSDT/15m/S7 |
| Microcap short blocked: price=0.068266 < min=1.0 |     1 |       0.0% | MAGICUSDT/15m/S7 |
| Microcap short blocked: price=0.267966 < min=1.0 |     1 |       0.0% | NXPCUSDT/15m/S7 |
| Microcap short blocked: price=0.037081 < min=1.0 |     1 |       0.0% | OSMOUSDT/15m/S7 |
| Microcap short blocked: price=0.044478 < min=1.0 |     1 |       0.0% | POLYXUSDT/15m/S2 |
| Microcap short blocked: price=0.021980 < min=1.0 |     1 |       0.0% | SPKUSDT/15m/S7 |
| Microcap short blocked: price=0.126237 < min=1.0 |     1 |       0.0% | SUPERUSDT/15m/S7 |
| Microcap short blocked: price=0.266567 < min=1.0 |     1 |       0.0% | SYRUPUSDT/15m/S7 |
| Microcap short blocked: price=0.061369 < min=1.0 |     1 |       0.0% | TKOUSDT/15m/S2 |
| Microcap short blocked: price=0.041579 < min=1.0 |     1 |       0.0% | TURTLEUSDT/15m/S7 |
| Microcap short blocked: price=0.062469 < min=1.0 |     1 |       0.0% | WCTUSDT/15m/S7 |
| Microcap short blocked: price=0.040580 < min=1.0 |     1 |       0.0% | YGGUSDT/15m/S7 |
| Microcap short blocked: price=0.071264 < min=1.0 |     1 |       0.0% | ZBTUSDT/15m/S7 |
| Microcap short blocked: price=0.623088 < min=1.0 |     1 |       0.0% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.004848 < min=1.0 |     1 |       0.0% | SOLVUSDT/1h/S7 |
| Microcap short blocked: price=0.036482 < min=1.0 |     1 |       0.0% | ACXUSDT/15m/S2 |
| Microcap short blocked: price=0.067036 < min=1.0 |     1 |       0.0% | SKYUSDT/15m/S7 |
| Microcap short blocked: price=0.659670 < min=1.0 |     1 |       0.0% | BERAUSDT/15m/S7 |
| Microcap short blocked: price=0.291854 < min=1.0 |     1 |       0.0% | SNXUSDT/15m/S7 |
| Microcap short blocked: price=0.008286 < min=1.0 |     1 |       0.0% | DUSDT/4h/S7 |
| Microcap short blocked: price=0.040270 < min=1.0 |     1 |       0.0% | CHZUSDT/15m/S7 |
| Microcap short blocked: price=0.320740 < min=1.0 |     1 |       0.0% | PROVEUSDT/15m/S7 |
| Microcap short blocked: price=0.003988 < min=1.0 |     1 |       0.0% | DGBUSDT/15m/S7 |
| Microcap short blocked: price=0.002159 < min=1.0 |     1 |       0.0% | QIUSDT/15m/S7 |
| Microcap short blocked: price=0.285857 < min=1.0 |     1 |       0.0% | SNXUSDT/1h/S7 |
| Stop too wide: 20.9% > max 10% |     1 |       0.0% | VICUSDT/1h/S2 |
| Microcap short blocked: price=0.010995 < min=1.0 |     1 |       0.0% | PLUMEUSDT/15m/S7 |
| Microcap short blocked: price=0.001549 < min=1.0 |     1 |       0.0% | RSRUSDT/15m/S7 |
| Microcap short blocked: price=0.124038 < min=1.0 |     1 |       0.0% | SUPERUSDT/15m/S7 |
| Microcap short blocked: price=0.061469 < min=1.0 |     1 |       0.0% | WCTUSDT/15m/S7 |
| Microcap short blocked: price=0.999200 < min=1.0 |     1 |       0.0% | USD1USDT/15m/S7 |
| Microcap short blocked: price=0.033283 < min=1.0 |     1 |       0.0% | LRCUSDT/15m/S7 |
| Microcap short blocked: price=0.266067 < min=1.0 |     1 |       0.0% | THEUSDT/15m/S7 |
| Microcap short blocked: price=0.081259 < min=1.0 |     1 |       0.0% | WALUSDT/15m/S7 |
| Microcap short blocked: price=0.092854 < min=1.0 |     1 |       0.0% | PIVXUSDT/15m/S7 |
| Microcap short blocked: price=0.021389 < min=1.0 |     1 |       0.0% | SXTUSDT/15m/S7 |
| Uneconomic: fees/R=0.85 > max 0.3 |     1 |       0.0% | TRXUSDT/15m/S7 |
| Uneconomic: fees/R=4.99 > max 0.3 |     1 |       0.0% | STEEMUSDT/15m/S7 |

## C. Gate Impact

Gate rejections are identified from `error_message` on trades where `data_available = False`.

### Gate rejection summary

**Microcap short blocked** — 908 trades (32.6%)

- Cannot estimate — payload missing: entry_price, stop_price, r_value

**Uneconomic (fees/R ratio)** — 361 trades (13.0%)

- Cannot estimate — payload missing: entry_price

**R-value invalid after slippage** — 82 trades (2.9%)

- Cannot estimate — payload missing: entry_price

**Adverse gap** — 19 trades (0.7%)

- Cannot estimate — payload missing: entry_price

**Stop too wide** — 11 trades (0.4%)

- Cannot estimate — payload missing: entry_price, stop_price, r_value

*To quantify actual gate impact, re-run the simulation with each gate disabled and compare outcomes.*

### TP Invalidation (post-entry gate)

- **TP invalidated:** 183 / 1405 (13.0%)
- **Avg R of TP-invalidated trades:** -0.24
- **Win rate of TP-invalidated trades:** 33.3%

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |   125 |
| BELOW_COST |    58 |

## D. Segmentation

### Overall

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| ALL                      |   1405 |   41.4% |     -2395.48 |      -1.70 |    0.71 |   -0.20 |     -0.22 |       -71.73 |       2.5 |

### D1. By Setup

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1                       |     21 |   52.4% |       -21.36 |      -1.02 |    0.76 |   -0.06 |      0.05 |       -17.82 |       2.4 |
| S2                       |     14 |   71.4% |        57.97 |       4.14 |    2.52 |    0.10 |      0.28 |       -11.53 |       2.4 |
| S3                       |     37 |   35.1% |      -199.17 |      -5.38 |    0.27 |   -0.26 |     -0.19 |       -40.63 |       2.8 |
| S6                       |    929 |   44.9% |     -1739.75 |      -1.87 |    0.70 |   -0.10 |     -0.15 |       -71.73 |       2.9 |
| S7                       |    404 |   32.4% |      -493.17 |      -1.22 |    0.75 |   -0.44 |     -1.08 |       -41.29 |       1.5 |

### D2. By Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| long                     |   1200 |   41.7% |     -1844.09 |      -1.54 |    0.74 |   -0.19 |     -0.22 |       -71.73 |       2.4 |
| short                    |    205 |   40.0% |      -551.39 |      -2.69 |    0.49 |   -0.25 |     -0.23 |       -71.16 |       2.9 |

### D3. By Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 15m                      |    989 |   40.2% |     -1828.50 |      -1.85 |    0.66 |   -0.25 |     -0.24 |       -58.66 |       2.4 |
| 1h                       |    399 |   44.6% |      -402.63 |      -1.01 |    0.85 |   -0.09 |     -0.14 |       -71.73 |       2.7 |
| 4h                       |     17 |   35.3% |      -164.34 |      -9.67 |    0.24 |   -0.19 |     -0.26 |       -41.29 |       2.2 |

### D4. By Exit Reason

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| FAIL_FAST                |    386 |    0.0% |     -4268.25 |     -11.06 |    0.00 |   -0.44 |     -0.39 |       -58.66 |       1.2 |
| STOP_LOSS                |    314 |    0.0% |     -3431.03 |     -10.93 |    0.00 |   -1.53 |     -1.23 |       -71.73 |       2.0 |
| TIME_STOP                |     80 |   27.5% |      -311.62 |      -3.90 |    0.28 |   -0.21 |     -0.19 |       -28.54 |       9.9 |
| TP1                      |    373 |  100.0% |      3473.12 |       9.31 |    0.00 |    0.42 |      0.42 |         0.05 |       2.8 |
| TP2                      |     24 |  100.0% |        38.98 |       1.62 |    0.00 |    0.15 |      0.16 |         0.10 |       1.5 |
| TRAIL_STOP               |    228 |   71.5% |      2103.32 |       9.23 |   22.17 |    0.97 |      0.32 |        -4.13 |       2.5 |

### D5. By Setup × Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / long                |     17 |   64.7% |        24.38 |       1.43 |    1.54 |    0.01 |      0.35 |       -12.50 |       2.3 |
| S1 / short               |      4 |    0.0% |       -45.74 |     -11.43 |    0.00 |   -0.38 |     -0.40 |       -17.82 |       3.0 |
| S2 / long                |     14 |   71.4% |        57.97 |       4.14 |    2.52 |    0.10 |      0.28 |       -11.53 |       2.4 |
| S3 / long                |     29 |   34.5% |      -126.86 |      -4.37 |    0.33 |   -0.26 |     -0.19 |       -37.71 |       2.9 |
| S3 / short               |      8 |   37.5% |       -72.31 |      -9.04 |    0.13 |   -0.27 |     -0.23 |       -40.63 |       2.4 |
| S6 / long                |    806 |   45.2% |     -1456.30 |      -1.81 |    0.71 |   -0.10 |     -0.14 |       -71.73 |       2.8 |
| S6 / short               |    123 |   43.1% |      -283.45 |      -2.30 |    0.60 |   -0.12 |     -0.19 |       -71.16 |       3.8 |
| S7 / long                |    334 |   31.4% |      -343.28 |      -1.03 |    0.81 |   -0.44 |     -1.08 |       -41.29 |       1.6 |
| S7 / short               |     70 |   37.1% |      -149.89 |      -2.14 |    0.39 |   -0.47 |     -0.91 |       -13.79 |       1.4 |

### D6. By Setup × Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / 15m                 |     14 |   50.0% |       -12.28 |      -0.88 |    0.74 |   -0.14 |     -0.02 |       -12.50 |       2.7 |
| S1 / 1h                  |      7 |   57.1% |        -9.08 |      -1.30 |    0.79 |    0.09 |      0.38 |       -17.82 |       1.9 |
| S2 / 15m                 |      5 |  100.0% |        61.89 |      12.38 |    0.00 |    0.37 |      0.38 |         0.85 |       1.6 |
| S2 / 1h                  |      7 |   71.4% |        17.99 |       2.57 |    2.11 |    0.01 |      0.30 |        -9.73 |       3.3 |
| S2 / 4h                  |      2 |    0.0% |       -21.91 |     -10.95 |    0.00 |   -0.24 |     -0.24 |       -11.53 |       1.0 |
| S3 / 15m                 |     31 |   38.7% |      -134.01 |      -4.32 |    0.28 |   -0.27 |     -0.19 |       -37.71 |       3.0 |
| S3 / 1h                  |      5 |   20.0% |       -24.52 |      -4.90 |    0.45 |   -0.13 |     -0.19 |       -17.65 |       1.8 |
| S3 / 4h                  |      1 |    0.0% |       -40.63 |     -40.63 |    0.00 |   -0.68 |     -0.68 |       -40.63 |       1.0 |
| S6 / 15m                 |    662 |   44.4% |     -1018.81 |      -1.54 |    0.73 |   -0.10 |     -0.17 |       -58.66 |       2.9 |
| S6 / 1h                  |    267 |   46.1% |      -720.95 |      -2.70 |    0.65 |   -0.10 |     -0.13 |       -71.73 |       2.9 |
| S7 / 15m                 |    277 |   28.9% |      -725.29 |      -2.62 |    0.47 |   -0.60 |     -1.18 |       -35.10 |       1.2 |
| S7 / 1h                  |    113 |   39.8% |       333.92 |       2.96 |    1.69 |   -0.09 |     -0.43 |       -28.45 |       2.3 |
| S7 / 4h                  |     14 |   42.9% |      -101.80 |      -7.27 |    0.34 |   -0.15 |     -0.29 |       -41.29 |       2.4 |

### D7. By Date (entry_ts)

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 2026-02-08               |     55 |   34.5% |      -141.96 |      -2.58 |    0.63 |   -0.34 |     -0.39 |       -35.83 |       1.8 |
| 2026-02-09               |     78 |   47.4% |        18.37 |       0.24 |    1.05 |   -0.08 |     -0.12 |       -31.51 |       2.1 |
| 2026-02-10               |     92 |   37.0% |      -213.70 |      -2.32 |    0.55 |   -0.18 |     -0.21 |       -29.10 |       2.1 |
| 2026-02-11               |    133 |   52.6% |       479.42 |       3.60 |    1.57 |    0.34 |      0.08 |       -71.16 |       2.0 |
| 2026-02-12               |    198 |   39.9% |      -587.83 |      -2.97 |    0.49 |   -0.38 |     -0.25 |       -45.91 |       2.0 |
| 2026-02-13               |    236 |   48.3% |        61.37 |       0.26 |    1.05 |   -0.13 |     -0.12 |       -46.51 |       3.2 |
| 2026-02-14               |    528 |   40.2% |     -1258.89 |      -2.38 |    0.57 |   -0.23 |     -0.25 |       -56.30 |       2.6 |
| 2026-02-15               |     81 |   21.0% |      -704.98 |      -8.70 |    0.20 |   -0.65 |     -0.48 |       -71.73 |       3.0 |
| 2026-02-16               |      4 |    0.0% |       -47.29 |     -11.82 |    0.00 |   -1.71 |     -1.70 |       -18.58 |       2.5 |

### D8. By Hour of Day (entry_ts UTC)

| Hour  | Trades | Win Rate | Avg PnL    |
|:------|-------:|---------:|-----------:|
| 00:00 |     46 |    41.3% |      -1.37 |
| 01:00 |     69 |    39.1% |      -4.91 |
| 02:00 |     26 |    53.8% |       3.44 |
| 03:00 |     53 |    26.4% |      -3.22 |
| 04:00 |     47 |    40.4% |      -3.82 |
| 05:00 |     42 |    57.1% |       3.53 |
| 06:00 |     80 |    42.5% |      -1.89 |
| 07:00 |     64 |    37.5% |      -4.66 |
| 08:00 |     84 |    54.8% |      -1.13 |
| 09:00 |    117 |    42.7% |      -0.96 |
| 10:00 |     70 |    40.0% |      -3.08 |
| 11:00 |     76 |    21.1% |      -8.22 |
| 12:00 |     54 |    24.1% |      -7.07 |
| 13:00 |     24 |    50.0% |      -0.58 |
| 14:00 |     42 |    42.9% |      -1.12 |
| 15:00 |     63 |    44.4% |      -0.08 |
| 16:00 |    121 |    52.9% |       0.36 |
| 17:00 |     91 |    51.6% |      -0.26 |
| 18:00 |     20 |    65.0% |       2.53 |
| 19:00 |     35 |    31.4% |      -2.53 |
| 20:00 |     46 |    32.6% |      -2.90 |
| 21:00 |     56 |    39.3% |      -1.08 |
| 22:00 |     56 |    33.9% |       7.48 |
| 23:00 |     23 |    21.7% |      -6.17 |

### D9. By Day of Week (entry_ts)

| Day | Trades | Win Rate | Avg PnL    |
|:----|-------:|---------:|-----------:|
| Mon |     82 |    45.1% |      -0.35 |
| Tue |     92 |    37.0% |      -2.32 |
| Wed |    133 |    52.6% |       3.60 |
| Thu |    198 |    39.9% |      -2.97 |
| Fri |    236 |    48.3% |       0.26 |
| Sat |    528 |    40.2% |      -2.38 |
| Sun |    136 |    26.5% |      -6.23 |

## E. Exit Mechanics

### E1. Exit Reason Distribution

| Exit Reason  | Count | % of Total |
|:-------------|------:|-----------:|
| FAIL_FAST    |   386 |      27.5% |
| TP1          |   373 |      26.5% |
| STOP_LOSS    |   314 |      22.3% |
| TRAIL_STOP   |   228 |      16.2% |
| TIME_STOP    |    80 |       5.7% |
| TP2          |    24 |       1.7% |

### E2. Exit Reason × Setup

| Exit Reason  |    S1 |    S2 |    S3 |    S6 |    S7 | Total |
|:-------------|------:|------:|------:|------:|------:|------:|
| FAIL_FAST    |     5 |     3 |    13 |   332 |    33 |   386 |
| TP1          |     8 |     7 |    10 |   326 |    22 |   373 |
| STOP_LOSS    |     2 |     1 |     6 |   104 |   201 |   314 |
| TRAIL_STOP   |     2 |     3 |     5 |    85 |   133 |   228 |
| TIME_STOP    |     4 |     0 |     3 |    71 |     2 |    80 |
| TP2          |     0 |     0 |     0 |    11 |    13 |    24 |

### E3. Exit Reason × Timeframe

| Exit Reason  |  15m |   1h |   4h | Total |
|:-------------|-----:|-----:|-----:|------:|
| FAIL_FAST    |  251 |  127 |    8 |   386 |
| TP1          |  261 |  111 |    1 |   373 |
| STOP_LOSS    |  241 |   70 |    3 |   314 |
| TRAIL_STOP   |  163 |   61 |    4 |   228 |
| TIME_STOP    |   65 |   15 |    0 |    80 |
| TP2          |    8 |   15 |    1 |    24 |

### E4. Bars Held Distribution (all exits)

| Percentile | Bars |
|-----------:|-----:|
| 5th |    1 |
| 10th |    1 |
| 25th |    1 |
| 50th |    1 |
| 75th |    2 |
| 90th |    7 |
| 95th |    9 |
| Mean |  2.5 |

### E5. Bars Held by Exit Reason

| Exit Reason  | Count | Mean  | P25 | Median | P75 | Max |
|:-------------|------:|------:|----:|-------:|----:|----:|
| FAIL_FAST    |   386 |   1.2 |   1 |      1 |   1 |   2 |
| STOP_LOSS    |   314 |   2.0 |   1 |      1 |   1 |  58 |
| TIME_STOP    |    80 |   9.9 |   9 |      9 |   9 | 100 |
| TP1          |   373 |   2.8 |   1 |      1 |   4 |  26 |
| TP2          |    24 |   1.5 |   1 |      1 |   2 |   6 |
| TRAIL_STOP   |   228 |   2.5 |   1 |      1 |   2 |  49 |

### E6. STOP_LOSS Bars Held Detail

- Count: 314, P25: 1, Median: 1, P75: 1, Max: 58
- **Immediate stops (bars ≤ 1):** 250 (79.6%)

### E7. TP1 Bars Held Detail

- Count: 373, P25: 1, Median: 1, P75: 4, Max: 26

### E7. TP2 Bars Held Detail

- Count: 24, P25: 1, Median: 1, P75: 2, Max: 6

### E8. TP Invalidation

- **TP invalidated:** 183 / 1405 (13.0%)

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |   125 |
| BELOW_COST |    58 |

## F. Outlier Flags

### F1. Top 10 Winners (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | MEUSDT     | 1h  | S7    | long  |     12.80 |    441.35 | TRAIL_STOP   |   11 |
| 2 | ZKCUSDT    | 15m | S6    | long  |      2.45 |    132.17 | TRAIL_STOP   |    1 |
| 3 | TNSRUSDT   | 15m | S7    | long  |     24.00 |    121.40 | TRAIL_STOP   |    1 |
| 4 | DYMUSDT    | 15m | S7    | long  |      8.62 |    104.61 | TRAIL_STOP   |    1 |
| 5 | ZENUSDT    | 1h  | S7    | long  |      3.97 |     67.39 | TRAIL_STOP   |   19 |
| 6 | LINEAUSDT  | 15m | S6    | long  |      0.91 |     59.94 | TRAIL_STOP   |    1 |
| 7 | TNSRUSDT   | 15m | S6    | long  |      0.95 |     57.96 | TRAIL_STOP   |    1 |
| 8 | LSKUSDT    | 15m | S6    | long  |      0.49 |     46.18 | TP1          |    1 |
| 9 | BANANAS31USDT | 1h  | S7    | long  |     10.68 |     45.68 | TRAIL_STOP   |    1 |
| 10 | AXSUSDT    | 15m | S2    | long  |      0.50 |     44.18 | TP1          |    1 |

### F2. Top 10 Losers (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | HUMAUSDT   | 1h  | S6    | long  |     -1.23 |    -71.73 | STOP_LOSS    |    1 |
| 2 | ENSOUSDT   | 1h  | S6    | short |     -3.04 |    -71.16 | STOP_LOSS    |    1 |
| 3 | ENSOUSDT   | 1h  | S6    | short |     -3.04 |    -71.16 | STOP_LOSS    |    1 |
| 4 | 0GUSDT     | 15m | S6    | long  |     -0.75 |    -58.66 | FAIL_FAST    |    1 |
| 5 | OPENUSDT   | 1h  | S6    | long  |     -1.23 |    -56.87 | STOP_LOSS    |    1 |
| 6 | MEMEUSDT   | 1h  | S6    | long  |     -1.29 |    -56.30 | STOP_LOSS    |   19 |
| 7 | SKLUSDT    | 1h  | S6    | long  |     -0.49 |    -55.78 | FAIL_FAST    |    1 |
| 8 | TURBOUSDT  | 15m | S6    | long  |     -1.02 |    -48.99 | STOP_LOSS    |    9 |
| 9 | CFXUSDT    | 15m | S6    | long  |     -0.85 |    -46.51 | FAIL_FAST    |    1 |
| 10 | ASTERUSDT  | 1h  | S6    | long  |     -0.66 |    -45.91 | FAIL_FAST    |    1 |

### F3. Instant Exits (bars_held ≤ 1)

- **Count:** 952 / 1405 (67.8%)

| Exit Reason  | Count | % of Instant |
|:-------------|------:|-------------:|
| FAIL_FAST    |   322 |        33.8% |
| STOP_LOSS    |   250 |        26.3% |
| TP1          |   195 |        20.5% |
| TRAIL_STOP   |   167 |        17.5% |
| TP2          |    18 |         1.9% |

| Setup | Count | % of Instant |
|:------|------:|-------------:|
| S6    |   561 |        58.9% |
| S7    |   352 |        37.0% |
| S3    |    20 |         2.1% |
| S2    |    10 |         1.1% |
| S1    |     9 |         0.9% |

### F4. PnL / Exit Reason Disagreement

No disagreements found (all STOP_LOSS/FAIL_FAST have negative PnL, all TP1/TP2 have positive PnL).

### F5. Top 10 Longest Held

| # | Symbol     | TF  | Setup | Dir   | Bars | R Multiple | Exit Reason  |
|--:|:-----------|:----|:------|:------|-----:|----------:|:-------------|
| 1 | ORDIUSDT   | 15m | S6    | short |  100 |      0.25 | TIME_STOP    |
| 2 | NFPUSDT    | 15m | S6    | long  |   58 |     -1.05 | STOP_LOSS    |
| 3 | PUNDIXUSDT | 1h  | S7    | long  |   49 |      0.86 | TRAIL_STOP   |
| 4 | NEARUSDT   | 1h  | S6    | long  |   45 |      1.50 | TRAIL_STOP   |
| 5 | WBETHUSDT  | 1h  | S6    | long  |   27 |     -2.15 | STOP_LOSS    |
| 6 | CUSDT      | 1h  | S6    | long  |   26 |      0.68 | TP1          |
| 7 | REDUSDT    | 15m | S6    | long  |   24 |     -1.06 | STOP_LOSS    |
| 8 | PARTIUSDT  | 15m | S6    | long  |   23 |     -0.04 | TRAIL_STOP   |
| 9 | ALCXUSDT   | 1h  | S6    | long  |   20 |      0.65 | TP1          |
| 10 | ZENUSDT    | 1h  | S7    | long  |   19 |      3.97 | TRAIL_STOP   |

### F6. Score Anomalies

Score Q25=0.778, Q75=0.901

**High-score losers (score ≥ Q75, R < 0): 190**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| GTCUSDT    | S7    |  1.000 |     -3.28 | STOP_LOSS    |
| API3USDT   | S7    |  0.972 |     -3.23 | STOP_LOSS    |
| BARUSDT    | S7    |  0.971 |     -3.19 | STOP_LOSS    |
| PENGUUSDT  | S7    |  0.953 |     -3.13 | STOP_LOSS    |
| ENSOUSDT   | S6    |  0.916 |     -3.04 | STOP_LOSS    |
| EGLDUSDT   | S7    |  0.919 |     -2.76 | STOP_LOSS    |
| RENDERUSDT | S7    |  0.952 |     -2.51 | STOP_LOSS    |
| GTCUSDT    | S7    |  0.958 |     -2.20 | STOP_LOSS    |
| WBETHUSDT  | S6    |  0.918 |     -2.15 | STOP_LOSS    |
| CHZUSDT    | S7    |  0.910 |     -2.10 | STOP_LOSS    |

**Low-score winners (score ≤ Q25, R > 0): 137**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| BANANAS31USDT | S7    |  0.682 |     10.68 | TRAIL_STOP   |
| LAUSDT     | S7    |  0.741 |      4.97 | TRAIL_STOP   |
| YFIUSDT    | S7    |  0.659 |      3.97 | TRAIL_STOP   |
| ZENUSDT    | S7    |  0.678 |      3.97 | TRAIL_STOP   |
| KSMUSDT    | S7    |  0.686 |      3.87 | TRAIL_STOP   |
| PROMUSDT   | S7    |  0.690 |      3.57 | TRAIL_STOP   |
| DUSKUSDT   | S7    |  0.651 |      2.93 | TRAIL_STOP   |
| PUMPUSDT   | S7    |  0.596 |      2.72 | TRAIL_STOP   |
| NEARUSDT   | S6    |  0.601 |      2.12 | TRAIL_STOP   |
| HBARUSDT   | S7    |  0.711 |      1.71 | TRAIL_STOP   |

## G. Feature Coverage

Comparison of trace_json keys found vs what `FeatureExtractor` captures.

- **Traces with data:** 1405 / 2786
- **Total feature columns:** 125

| trace_json key | In FeatureExtractor? | In features.csv? | Action taken |
|:---------------|:---------------------|:-----------------|:-------------|
| cond.why_0                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_1                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_2                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_3                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_4                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_5                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_6                               (70.4%) | No                   | Yes              | Extracted by profiler |
| exit.bars_held                           (100.0%) | No                   | Yes              | Extracted by profiler |
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
| ind.htf_bb_mb                            (98.8%) | Yes                  | Yes              | Already captured |
| ind.htf_close                            (98.8%) | Yes                  | Yes              | Already captured |
| ind.htf_sma50                            (98.8%) | Yes                  | Yes              | Already captured |
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
| ind.vwap                                 (98.5%) | Yes                  | Yes              | Already captured |
| ind.vwap_distance_atr                    (98.5%) | Yes                  | Yes              | Already captured |
| ind.vwap_lower_2sd                       (98.5%) | Yes                  | Yes              | Already captured |
| ind.vwap_upper_2sd                       (98.5%) | Yes                  | Yes              | Already captured |
| meta.exit_reason                         (100.0%) | No                   | Yes              | Extracted by profiler |
| meta.tp_invalidated                      (13.0%) | No                   | Yes              | Extracted by profiler |
| meta.tp_invalidation_reason              (13.0%) | No                   | Yes              | Extracted by profiler |
| score.bias_strength                      (30.2%) | Yes                  | Yes              | Already captured |
| score.break_quality                      (2.5%) | Yes                  | Yes              | Already captured |
| score.break_strength                     (28.8%) | Yes                  | Yes              | Already captured |
| score.clean_close                        (100.0%) | Yes                  | Yes              | Already captured |
| score.conv_speed                         (1.0%) | Yes                  | Yes              | Already captured |
| score.conv_tight                         (1.0%) | Yes                  | Yes              | Already captured |
| score.div_magnitude                      (2.6%) | Yes                  | Yes              | Already captured |
| score.htf_confluence                     (28.8%) | Yes                  | Yes              | Already captured |
| score.participation                      (71.2%) | Yes                  | Yes              | Already captured |
| score.pullback_quality                   (66.1%) | Yes                  | Yes              | Already captured |
| score.retrace_depth                      (66.1%) | Yes                  | Yes              | Already captured |
| score.rsi_extremity                      (2.6%) | Yes                  | Yes              | Already captured |
| score.severity                           (1.5%) | Yes                  | Yes              | Already captured |
| score.span_quality                       (2.6%) | Yes                  | Yes              | Already captured |
| score.trend_strength                     (67.1%) | Yes                  | Yes              | Already captured |
| score.volume_quality                     (28.8%) | Yes                  | Yes              | Already captured |
| state.bars_out_of_squeeze                (1.5%) | Yes                  | Yes              | Already captured |
| state.bb_width_pctile                    (1.5%) | Yes                  | Yes              | Already captured |
| state.bearish_divergence                 (0.6%) | Yes                  | Yes              | Already captured |
| state.bias_score                         (1.5%) | Yes                  | Yes              | Already captured |
| state.box_high                           (1.5%) | Yes                  | Yes              | Already captured |
| state.box_low                            (1.5%) | Yes                  | Yes              | Already captured |
| state.btc_box_high                       (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_box_low                        (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_breakout_direction             (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_severe                         (1.5%) | Yes                  | Yes              | Already captured |
| state.btc_shock_down                     (1.0%) | Yes                  | Yes              | Already captured |
| state.btc_shock_up                       (1.0%) | Yes                  | Yes              | Already captured |
| state.bullish_divergence                 (2.1%) | Yes                  | Yes              | Already captured |
| state.converging                         (1.0%) | Yes                  | Yes              | Already captured |
| state.dec_count                          (1.0%) | Yes                  | Yes              | Already captured |
| state.donchian_high                      (28.8%) | Yes                  | Yes              | Already captured |
| state.donchian_low                       (28.8%) | Yes                  | Yes              | Already captured |
| state.donchian_mid                       (28.8%) | Yes                  | Yes              | Already captured |
| state.ema20_slope_negative               (66.1%) | Yes                  | Yes              | Already captured |
| state.ema20_slope_positive               (66.1%) | Yes                  | Yes              | Already captured |
| state.ema50_slope_negative               (66.1%) | Yes                  | Yes              | Already captured |
| state.ema50_slope_positive               (66.1%) | Yes                  | Yes              | Already captured |
| state.ema_stacked_long                   (66.1%) | Yes                  | Yes              | Already captured |
| state.ema_stacked_short                  (66.1%) | Yes                  | Yes              | Already captured |
| state.fail_fast_bars                     (1.5%) | Yes                  | Yes              | Already captured |
| state.gap_decreasing                     (1.0%) | Yes                  | Yes              | Already captured |
| state.has_divergence                     (2.6%) | Yes                  | Yes              | Already captured |
| state.held_ema50                         (66.1%) | Yes                  | Yes              | Already captured |
| state.htf_aligned                        (66.1%) | Yes                  | Yes              | Already captured |
| state.htf_bias                           (94.9%) | Yes                  | Yes              | Already captured |
| state.htf_bias_score                     (94.9%) | Yes                  | Yes              | Already captured |
| state.is_narrow                          (1.0%) | Yes                  | Yes              | Already captured |
| state.long_allowed                       (2.5%) | Yes                  | Yes              | Already captured |
| state.long_breakout                      (28.8%) | Yes                  | Yes              | Already captured |
| state.ma_gap_atr                         (1.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_long                     (1.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_long_t1                  (1.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_short                    (1.0%) | Yes                  | Yes              | Already captured |
| state.pre_cross_short_t1                 (1.0%) | Yes                  | Yes              | Already captured |
| state.prev_donchian_high                 (28.8%) | Yes                  | Yes              | Already captured |
| state.prev_donchian_low                  (28.8%) | Yes                  | Yes              | Already captured |
| state.pullback_detected                  (66.1%) | Yes                  | Yes              | Already captured |
| state.reclaimed_ema20                    (66.1%) | Yes                  | Yes              | Already captured |
| state.retrace_depth_atr                  (66.1%) | Yes                  | Yes              | Already captured |
| state.ripe                               (1.5%) | Yes                  | Yes              | Already captured |
| state.ripe_at_prev_bar                   (1.5%) | Yes                  | Yes              | Already captured |
| state.rsi_bias                           (28.8%) | Yes                  | Yes              | Already captured |
| state.rsi_extreme_high                   (2.6%) | Yes                  | Yes              | Already captured |
| state.rsi_extreme_low                    (2.6%) | Yes                  | Yes              | Already captured |
| state.rsi_reclaimed_30                   (2.6%) | Yes                  | Yes              | Already captured |
| state.rsi_reclaimed_70                   (2.6%) | Yes                  | Yes              | Already captured |
| state.short_allowed                      (2.5%) | Yes                  | Yes              | Already captured |
| state.short_breakout                     (28.8%) | Yes                  | Yes              | Already captured |
| state.sma200_slope_negative              (1.0%) | Yes                  | Yes              | Already captured |
| state.sma200_slope_positive              (1.0%) | Yes                  | Yes              | Already captured |
| state.sma50_slope_negative               (1.0%) | Yes                  | Yes              | Already captured |
| state.sma50_slope_positive               (1.0%) | Yes                  | Yes              | Already captured |
| state.squeeze_age                        (1.5%) | Yes                  | Yes              | Already captured |
| state.squeeze_on                         (1.5%) | Yes                  | Yes              | Already captured |
| state.time_stop_bars                     (1.0%) | Yes                  | Yes              | Already captured |
| state.touched_ema20                      (66.1%) | Yes                  | Yes              | Already captured |
| state.trend_direction                    (66.1%) | Yes                  | Yes              | Already captured |
| state.trend_strength                     (66.1%) | Yes                  | Yes              | Already captured |
| state.was_wide                           (1.0%) | Yes                  | Yes              | Already captured |
