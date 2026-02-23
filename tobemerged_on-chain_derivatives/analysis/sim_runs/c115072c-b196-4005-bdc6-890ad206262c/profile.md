# Simulation Profile: c115072c

Generated: 2026-02-16 17:50:03 UTC

## A. Run Metadata

- **Run ID:** `c115072c-b196-4005-bdc6-890ad206262c`
- **Type:** SIMULATION
- **Status:** SUCCESS
- **Name:** Phase3-gated-baseline
- **Engine:** 1.0.0
- **Created:** 2026-02-16 17:27:17.592168+00:00
- **Started:** 2026-02-16 17:27:17.723473+00:00
- **Completed:** —
- **Duration:** —s
- **Setups:** S1, S2, S3, S6, S7
- **Timeframes:** 15m, 1h, 4h
- **Symbols:** 392
- **Date range:** 2026-02-09 01:30 UTC → 2026-02-16 16:00 UTC
- **Total opportunities (rows):** 1408
- **Simulated (data_available):** 1087
- **Skipped (no data / error):** 321

**Run summary_json:**
```json
{
  "total_trades": 1087,
  "winning_trades": 482,
  "losing_trades": 605,
  "win_rate": 0.4434,
  "total_r": -126.52,
  "avg_r": -0.1164,
  "max_r": 0.0,
  "min_r": 0.0,
  "gross_pnl": 0.0,
  "total_fees": 0.0,
  "total_slippage": 0.0,
  "net_pnl": -1961.61,
  "max_drawdown_pct": 0.0,
  "profit_factor": 0.708,
  "expectancy": -0.1164,
  "bars_processed": 0,
  "assets_processed": 0,
  "assets_skipped": 0,
  "signals_generated": 1408,
  "execution_time_seconds": 110.81034398078918,
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
      "trades": 892,
      "wins": 398,
      "pnl_net": -1941.47,
      "total_r": -92.04,
      "win_rate": 0.4461883408071749,
      "avg_r": -0.1031830320216963
    },
    "S2": {
      "trades": 17,
      "wins": 13,
      "pnl_net": 76.15,
      "total_r": 1.44,
      "win_rate": 0.7647058823529411,
      "avg_r": 0.08471149394858975
    },
    "S3": {
      "trades": 31,
      "wins": 13,
      "pnl_net": -124.7,
      "total_r": -3.95,
      "win_rate": 0.41935483870967744,
      "avg_r": -0.1274243975391667
    },
    "S7": {
      "trades": 132,
      "wins": 48,
      "pnl_net": 21.25,
      "total_r": -32.86,
      "win_rate": 0.36363636363636365,
      "avg_r": -0.2489573833080549
    },
    "S1": {
      "trades": 15,
      "wins": 10,
      "pnl_net": 7.16,
      "total_r": 0.89,
      "win_rate": 0.6666666666666666,
      "avg_r": 0.05925581950832927
    }
  },
  "per_symbol": {}
}
```

## B. Data Quality

- **Total rows:** 1408
- **Data available (valid):** 1087 (77.2%)
- **Data unavailable:** 321 (22.8%)
- **With error messages:** 321 (22.8%)

### Data availability by setup

| Setup | Available | Unavailable | % Available |
|:------|----------:|------------:|------------:|
| S1    |        15 |          12 |       55.6% |
| S2    |        17 |          16 |       51.5% |
| S3    |        31 |          62 |       33.3% |
| S6    |       892 |          58 |       93.9% |
| S7    |       132 |         173 |       43.3% |

### Error taxonomy

| Error | Count | % of Total | Example |
|:------|------:|-----------:|:--------|
| R-value invalid after slippage |    39 |       2.8% | DEXEUSDT/1h/S7 |
| Uneconomic: fees/R=1.60 > max 0.3 |    10 |       0.7% | AEURUSDT/15m/S2 |
| Uneconomic: fees/R=0.32 > max 0.3 |     4 |       0.3% | WBTCUSDT/1h/S7 |
| Microcap short blocked: price=0.999700 < min=1.0 |     3 |       0.2% | RLUSDUSDT/1h/S7 |
| Uneconomic: fees/R=0.40 > max 0.3 |     3 |       0.2% | ARUSDT/1h/S7 |
| Uneconomic: fees/R=0.44 > max 0.3 |     3 |       0.2% | PYTHUSDT/1h/S7 |
| Uneconomic: fees/R=0.45 > max 0.3 |     2 |       0.1% | EURIUSDT/15m/S6 |
| Uneconomic: fees/R=1.04 > max 0.3 |     2 |       0.1% | AEURUSDT/15m/S3 |
| Microcap short blocked: price=0.999900 < min=1.0 |     2 |       0.1% | RLUSDUSDT/15m/S6 |
| Uneconomic: fees/R=0.96 > max 0.3 |     2 |       0.1% | EURIUSDT/15m/S6 |
| Microcap short blocked: price=0.999300 < min=1.0 |     2 |       0.1% | BFUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.038381 < min=1.0 |     2 |       0.1% | LRCUSDT/15m/S2 |
| Adverse gap: R degraded 25% (min 50%) |     2 |       0.1% | ALPINEUSDT/1h/S6 |
| Microcap short blocked: price=0.999500 < min=1.0 |     2 |       0.1% | USD1USDT/1h/S6 |
| Microcap short blocked: price=0.025527 < min=1.0 |     2 |       0.1% | GRTUSDT/4h/S7 |
| Microcap short blocked: price=0.045377 < min=1.0 |     2 |       0.1% | PNUTUSDT/1h/S7 |
| Microcap short blocked: price=0.208896 < min=1.0 |     2 |       0.1% | WIFUSDT/4h/S7 |
| Microcap short blocked: price=0.276862 < min=1.0 |     2 |       0.1% | SNXUSDT/1h/S7 |
| Adverse gap: R degraded 35% (min 50%) |     2 |       0.1% | ENJUSDT/1h/S7 |
| Adverse gap: R degraded 42% (min 50%) |     2 |       0.1% | MAGICUSDT/1h/S6 |
| Uneconomic: fees/R=0.34 > max 0.3 |     2 |       0.1% | BOMEUSDT/1h/S7 |
| Microcap short blocked: price=0.076262 < min=1.0 |     2 |       0.1% | ADXUSDT/1h/S7 |
| Uneconomic: fees/R=0.73 > max 0.3 |     2 |       0.1% | EURUSDT/15m/S6 |
| Uneconomic: fees/R=0.35 > max 0.3 |     2 |       0.1% | AEURUSDT/15m/S6 |
| Uneconomic: fees/R=0.39 > max 0.3 |     2 |       0.1% | NOTUSDT/1h/S7 |
| Uneconomic: fees/R=0.54 > max 0.3 |     2 |       0.1% | XTZUSDT/1h/S7 |
| Adverse gap: R degraded 44% (min 50%) |     2 |       0.1% | VTHOUSDT/15m/S6 |
| Adverse gap: R degraded 40% (min 50%) |     2 |       0.1% | ZAMAUSDT/1h/S6 |
| Stop too wide: 11.6% > max 10% |     1 |       0.1% | AXSUSDT/15m/S2 |
| Stop too wide: 15.2% > max 10% |     1 |       0.1% | AXSUSDT/15m/S2 |
| Uneconomic: fees/R=0.41 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Microcap short blocked: price=0.026677 < min=1.0 |     1 |       0.1% | SIGNUSDT/15m/S1 |
| Microcap short blocked: price=0.020390 < min=1.0 |     1 |       0.1% | RAREUSDT/15m/S3 |
| Microcap short blocked: price=0.060170 < min=1.0 |     1 |       0.1% | LUNAUSDT/1h/S7 |
| Uneconomic: fees/R=1.40 > max 0.3 |     1 |       0.1% | RLUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.006297 < min=1.0 |     1 |       0.1% | TRUUSDT/1h/S7 |
| Microcap short blocked: price=0.277261 < min=1.0 |     1 |       0.1% | TRXUSDT/1h/S7 |
| Microcap short blocked: price=0.000022 < min=1.0 |     1 |       0.1% | WINUSDT/1h/S7 |
| Microcap short blocked: price=1.000000 < min=1.0 |     1 |       0.1% | USDPUSDT/1h/S7 |
| Stop too wide: 12.9% > max 10% |     1 |       0.1% | GUSDT/15m/S2 |
| Microcap short blocked: price=0.039380 < min=1.0 |     1 |       0.1% | ICXUSDT/1h/S7 |
| Microcap short blocked: price=0.079060 < min=1.0 |     1 |       0.1% | REQUSDT/1h/S7 |
| Microcap short blocked: price=0.999800 < min=1.0 |     1 |       0.1% | XUSDUSDT/15m/S6 |
| Uneconomic: fees/R=1.66 > max 0.3 |     1 |       0.1% | USDCUSDT/1h/S7 |
| Microcap short blocked: price=0.009725 < min=1.0 |     1 |       0.1% | ARPAUSDT/1h/S7 |
| Microcap short blocked: price=0.040080 < min=1.0 |     1 |       0.1% | DYMUSDT/1h/S7 |
| Microcap short blocked: price=0.013493 < min=1.0 |     1 |       0.1% | NTRNUSDT/1h/S7 |
| Microcap short blocked: price=0.003088 < min=1.0 |     1 |       0.1% | QKCUSDT/1h/S7 |
| Microcap short blocked: price=0.023088 < min=1.0 |     1 |       0.1% | SXTUSDT/1h/S7 |
| Adverse gap: R degraded 10% (min 50%) |     1 |       0.1% | OPENUSDT/1h/S6 |
| Microcap short blocked: price=0.026537 < min=1.0 |     1 |       0.1% | SIGNUSDT/15m/S1 |
| Microcap short blocked: price=0.452774 < min=1.0 |     1 |       0.1% | ACMUSDT/1h/S3 |
| Microcap short blocked: price=0.026487 < min=1.0 |     1 |       0.1% | AEVOUSDT/1h/S7 |
| Microcap short blocked: price=0.012764 < min=1.0 |     1 |       0.1% | BROCCOLI714USDT/1h/S1 |
| Stop too wide: 16.9% > max 10% |     1 |       0.1% | FTTUSDT/1h/S2 |
| Microcap short blocked: price=0.042079 < min=1.0 |     1 |       0.1% | RIFUSDT/15m/S3 |
| Microcap short blocked: price=0.006997 < min=1.0 |     1 |       0.1% | TUSDT/15m/S3 |
| Microcap short blocked: price=0.309845 < min=1.0 |     1 |       0.1% | DEGOUSDT/1h/S7 |
| Microcap short blocked: price=0.207696 < min=1.0 |     1 |       0.1% | CTKUSDT/1h/S7 |
| Microcap short blocked: price=0.044378 < min=1.0 |     1 |       0.1% | RIFUSDT/15m/S3 |
| Microcap short blocked: price=0.026967 < min=1.0 |     1 |       0.1% | SENTUSDT/1h/S1 |
| Microcap short blocked: price=0.185607 < min=1.0 |     1 |       0.1% | ARKUSDT/1h/S1 |
| Microcap short blocked: price=0.135432 < min=1.0 |     1 |       0.1% | EDUUSDT/1h/S7 |
| Microcap short blocked: price=0.041629 < min=1.0 |     1 |       0.1% | JSTUSDT/1h/S7 |
| Stop too wide: 10.2% > max 10% |     1 |       0.1% | DUSKUSDT/15m/S2 |
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
| Uneconomic: fees/R=0.48 > max 0.3 |     1 |       0.1% | EURUSDT/15m/S6 |
| Microcap short blocked: price=0.019590 < min=1.0 |     1 |       0.1% | FIDAUSDT/1h/S1 |
| Microcap short blocked: price=0.131934 < min=1.0 |     1 |       0.1% | LSKUSDT/1h/S7 |
| Microcap short blocked: price=0.010685 < min=1.0 |     1 |       0.1% | MDTUSDT/1h/S1 |
| Microcap short blocked: price=0.005987 < min=1.0 |     1 |       0.1% | NOMUSDT/1h/S7 |
| Microcap short blocked: price=0.022489 < min=1.0 |     1 |       0.1% | SXTUSDT/1h/S7 |
| Microcap short blocked: price=0.063668 < min=1.0 |     1 |       0.1% | TREEUSDT/1h/S7 |
| Microcap short blocked: price=0.274563 < min=1.0 |     1 |       0.1% | TRXUSDT/1h/S7 |
| Microcap short blocked: price=0.025887 < min=1.0 |     1 |       0.1% | PHAUSDT/1h/S7 |
| Microcap short blocked: price=0.705647 < min=1.0 |     1 |       0.1% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.005437 < min=1.0 |     1 |       0.1% | IOTXUSDT/1h/S7 |
| Microcap short blocked: price=0.018191 < min=1.0 |     1 |       0.1% | WUSDT/1h/S7 |
| Microcap short blocked: price=0.005407 < min=1.0 |     1 |       0.1% | IOTXUSDT/1h/S7 |
| Adverse gap: R degraded 41% (min 50%) |     1 |       0.1% | WCTUSDT/1h/S6 |
| Microcap short blocked: price=0.026287 < min=1.0 |     1 |       0.1% | CHRUSDT/1h/S7 |
| Microcap short blocked: price=0.664468 < min=1.0 |     1 |       0.1% | FRAXUSDT/1h/S7 |
| Microcap short blocked: price=0.150925 < min=1.0 |     1 |       0.1% | XLMUSDT/1h/S7 |
| Microcap short blocked: price=0.181509 < min=1.0 |     1 |       0.1% | YBUSDT/15m/S3 |
| Microcap short blocked: price=0.000000 < min=1.0 |     1 |       0.1% | BTTCUSDT/1h/S7 |
| Microcap short blocked: price=0.072864 < min=1.0 |     1 |       0.1% | ZBTUSDT/15m/S3 |
| Stop too wide: 25.9% > max 10% |     1 |       0.1% | ACEUSDT/1h/S2 |
| Stop too wide: 19.0% > max 10% |     1 |       0.1% | ACEUSDT/15m/S2 |
| Microcap short blocked: price=0.011294 < min=1.0 |     1 |       0.1% | GPSUSDT/1h/S1 |
| Stop too wide: 23.5% > max 10% |     1 |       0.1% | TNSRUSDT/1h/S2 |
| Stop too wide: 40.4% > max 10% |     1 |       0.1% | DYMUSDT/1h/S2 |
| Stop too wide: 40.1% > max 10% |     1 |       0.1% | DYMUSDT/1h/S2 |
| Microcap short blocked: price=0.188006 < min=1.0 |     1 |       0.1% | OPENUSDT/15m/S3 |
| Uneconomic: fees/R=0.47 > max 0.3 |     1 |       0.1% | SUNUSDT/15m/S6 |
| Microcap short blocked: price=0.277961 < min=1.0 |     1 |       0.1% | TRXUSDT/1h/S3 |
| Uneconomic: fees/R=0.56 > max 0.3 |     1 |       0.1% | COSUSDT/15m/S6 |
| Microcap short blocked: price=0.088356 < min=1.0 |     1 |       0.1% | LAYERUSDT/15m/S3 |
| Microcap short blocked: price=0.094553 < min=1.0 |     1 |       0.1% | ZKCUSDT/15m/S3 |
| Microcap short blocked: price=0.002631 < min=1.0 |     1 |       0.1% | CELRUSDT/15m/S3 |
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
| Uneconomic: fees/R=1.92 > max 0.3 |     1 |       0.1% | USDEUSDT/1h/S7 |
| Microcap short blocked: price=0.998401 < min=1.0 |     1 |       0.1% | FDUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.022389 < min=1.0 |     1 |       0.1% | SXTUSDT/1h/S7 |
| Microcap short blocked: price=0.001999 < min=1.0 |     1 |       0.1% | ACAUSDT/1h/S7 |
| Microcap short blocked: price=0.734633 < min=1.0 |     1 |       0.1% | ORCAUSDT/1h/S7 |
| Microcap short blocked: price=0.162919 < min=1.0 |     1 |       0.1% | ACEUSDT/1h/S1 |
| Microcap short blocked: price=0.999100 < min=1.0 |     1 |       0.1% | BFUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.090855 < min=1.0 |     1 |       0.1% | LISTAUSDT/1h/S7 |
| Microcap short blocked: price=0.000011 < min=1.0 |     1 |       0.1% | 1000SATSUSDT/1h/S7 |
| Microcap short blocked: price=0.999000 < min=1.0 |     1 |       0.1% | BFUSDUSDT/1h/S7 |
| Uneconomic: fees/R=1.20 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Microcap short blocked: price=0.000006 < min=1.0 |     1 |       0.1% | SHIBUSDT/15m/S1 |
| Microcap short blocked: price=0.101449 < min=1.0 |     1 |       0.1% | WLFIUSDT/1h/S7 |
| Microcap short blocked: price=0.462769 < min=1.0 |     1 |       0.1% | ACMUSDT/15m/S3 |
| Microcap short blocked: price=0.168716 < min=1.0 |     1 |       0.1% | PUNDIXUSDT/15m/S3 |
| Microcap short blocked: price=0.261869 < min=1.0 |     1 |       0.1% | LQTYUSDT/1h/S7 |
| Microcap short blocked: price=0.016172 < min=1.0 |     1 |       0.1% | STRAXUSDT/1h/S3 |
| Adverse gap: R degraded 37% (min 50%) |     1 |       0.1% | XECUSDT/1h/S6 |
| Microcap short blocked: price=0.027936 < min=1.0 |     1 |       0.1% | HOMEUSDT/15m/S3 |
| Microcap short blocked: price=0.051174 < min=1.0 |     1 |       0.1% | CFXUSDT/15m/S3 |
| Microcap short blocked: price=0.211894 < min=1.0 |     1 |       0.1% | WIFUSDT/1h/S7 |
| Uneconomic: fees/R=4.91 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=1.14 > max 0.3 |     1 |       0.1% | FDUSDUSDT/15m/S6 |
| Uneconomic: fees/R=0.43 > max 0.3 |     1 |       0.1% | AVAUSDT/1h/S7 |
| Microcap short blocked: price=0.104548 < min=1.0 |     1 |       0.1% | RONINUSDT/15m/S3 |
| Microcap short blocked: price=0.015412 < min=1.0 |     1 |       0.1% | USUALUSDT/15m/S3 |
| Microcap short blocked: price=0.632684 < min=1.0 |     1 |       0.1% | 0GUSDT/15m/S3 |
| Microcap short blocked: price=0.103148 < min=1.0 |     1 |       0.1% | RONINUSDT/15m/S3 |
| Microcap short blocked: price=0.176812 < min=1.0 |     1 |       0.1% | OPENUSDT/15m/S3 |
| Microcap short blocked: price=0.001623 < min=1.0 |     1 |       0.1% | RSRUSDT/15m/S3 |
| Microcap short blocked: price=0.007516 < min=1.0 |     1 |       0.1% | WAXPUSDT/15m/S3 |
| Microcap short blocked: price=0.263568 < min=1.0 |     1 |       0.1% | ONDOUSDT/1h/S3 |
| Adverse gap: R degraded 15% (min 50%) |     1 |       0.1% | TFUELUSDT/1h/S6 |
| Adverse gap: R degraded 23% (min 50%) |     1 |       0.1% | ERAUSDT/1h/S6 |
| Uneconomic: fees/R=0.31 > max 0.3 |     1 |       0.1% | FARMUSDT/1h/S7 |
| Adverse gap: R degraded 21% (min 50%) |     1 |       0.1% | NEXOUSDT/1h/S6 |
| Microcap short blocked: price=0.156922 < min=1.0 |     1 |       0.1% | STGUSDT/1h/S7 |
| Uneconomic: fees/R=0.50 > max 0.3 |     1 |       0.1% | WBTCUSDT/15m/S6 |
| Microcap short blocked: price=0.073863 < min=1.0 |     1 |       0.1% | HIVEUSDT/15m/S3 |
| Uneconomic: fees/R=1.09 > max 0.3 |     1 |       0.1% | NEWTUSDT/1h/S7 |
| Adverse gap: R degraded 32% (min 50%) |     1 |       0.1% | SKLUSDT/1h/S6 |
| Uneconomic: fees/R=0.76 > max 0.3 |     1 |       0.1% | HMSTRUSDT/15m/S6 |
| Adverse gap: R degraded 45% (min 50%) |     1 |       0.1% | FOGOUSDT/1h/S6 |
| Uneconomic: fees/R=0.53 > max 0.3 |     1 |       0.1% | QTUMUSDT/15m/S6 |
| Uneconomic: fees/R=0.30 > max 0.3 |     1 |       0.1% | 1INCHUSDT/1h/S7 |
| Uneconomic: fees/R=0.33 > max 0.3 |     1 |       0.1% | ALPINEUSDT/1h/S7 |
| Uneconomic: fees/R=1.82 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Uneconomic: fees/R=0.87 > max 0.3 |     1 |       0.1% | EURUSDT/15m/S6 |
| Uneconomic: fees/R=0.52 > max 0.3 |     1 |       0.1% | GALAUSDT/1h/S7 |
| Uneconomic: fees/R=0.64 > max 0.3 |     1 |       0.1% | POLYXUSDT/1h/S7 |
| Uneconomic: fees/R=2.23 > max 0.3 |     1 |       0.1% | QTUMUSDT/1h/S7 |
| Adverse gap: R degraded 33% (min 50%) |     1 |       0.1% | WLDUSDT/1h/S6 |
| Uneconomic: fees/R=1.19 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
| Microcap short blocked: price=0.042419 < min=1.0 |     1 |       0.1% | JSTUSDT/15m/S3 |
| Microcap short blocked: price=0.009025 < min=1.0 |     1 |       0.1% | ALTUSDT/1h/S3 |
| Uneconomic: fees/R=7.60 > max 0.3 |     1 |       0.1% | EURUSDT/4h/S7 |
| Microcap short blocked: price=0.281259 < min=1.0 |     1 |       0.1% | ONDOUSDT/15m/S3 |
| Microcap short blocked: price=0.993203 < min=1.0 |     1 |       0.1% | SUIUSDT/15m/S3 |
| Microcap short blocked: price=0.514742 < min=1.0 |     1 |       0.1% | UMAUSDT/15m/S3 |
| Microcap short blocked: price=0.998600 < min=1.0 |     1 |       0.1% | FDUSDUSDT/1h/S7 |
| Microcap short blocked: price=0.980510 < min=1.0 |     1 |       0.1% | SUIUSDT/15m/S3 |
| Microcap short blocked: price=0.022879 < min=1.0 |     1 |       0.1% | CGPTUSDT/15m/S3 |
| Microcap short blocked: price=0.226287 < min=1.0 |     1 |       0.1% | METUSDT/15m/S3 |
| Microcap short blocked: price=0.109845 < min=1.0 |     1 |       0.1% | DYDXUSDT/15m/S3 |
| Uneconomic: fees/R=0.51 > max 0.3 |     1 |       0.1% | 1000CATUSDT/1h/S7 |
| Adverse gap: R degraded 46% (min 50%) |     1 |       0.1% | BATUSDT/1h/S6 |
| Microcap short blocked: price=0.071364 < min=1.0 |     1 |       0.1% | INITUSDT/15m/S3 |
| Microcap short blocked: price=0.015302 < min=1.0 |     1 |       0.1% | PORTALUSDT/15m/S3 |
| Microcap short blocked: price=0.000454 < min=1.0 |     1 |       0.1% | BOMEUSDT/15m/S3 |
| Microcap short blocked: price=0.107846 < min=1.0 |     1 |       0.1% | MANAUSDT/15m/S3 |
| Microcap short blocked: price=0.198001 < min=1.0 |     1 |       0.1% | OPUSDT/1h/S3 |
| Microcap short blocked: price=0.430485 < min=1.0 |     1 |       0.1% | WLDUSDT/1h/S3 |
| Microcap short blocked: price=0.001132 < min=1.0 |     1 |       0.1% | COSUSDT/15m/S3 |
| Microcap short blocked: price=0.222289 < min=1.0 |     1 |       0.1% | CTKUSDT/15m/S3 |
| Microcap short blocked: price=0.661669 < min=1.0 |     1 |       0.1% | RAYUSDT/15m/S3 |
| Microcap short blocked: price=0.070765 < min=1.0 |     1 |       0.1% | POWRUSDT/15m/S3 |
| Uneconomic: fees/R=0.36 > max 0.3 |     1 |       0.1% | PLUMEUSDT/1h/S6 |
| Adverse gap: R degraded 16% (min 50%) |     1 |       0.1% | SHIBUSDT/1h/S6 |
| Microcap short blocked: price=0.016192 < min=1.0 |     1 |       0.1% | ACTUSDT/15m/S3 |
| Microcap short blocked: price=0.015352 < min=1.0 |     1 |       0.1% | PORTALUSDT/15m/S3 |
| Microcap short blocked: price=0.000031 < min=1.0 |     1 |       0.1% | DOGSUSDT/15m/S3 |
| Microcap short blocked: price=0.000005 < min=1.0 |     1 |       0.1% | PEPEUSDT/15m/S3 |
| Uneconomic: fees/R=2.36 > max 0.3 |     1 |       0.1% | VETUSDT/1h/S7 |
| Microcap short blocked: price=0.423188 < min=1.0 |     1 |       0.1% | XTZUSDT/1h/S3 |
| Microcap short blocked: price=0.110635 < min=1.0 |     1 |       0.1% | DOGEUSDT/15m/S3 |
| Stop too wide: 13.5% > max 10% |     1 |       0.1% | INITUSDT/4h/S2 |
| Microcap short blocked: price=0.032684 < min=1.0 |     1 |       0.1% | LRCUSDT/1h/S7 |
| Microcap short blocked: price=0.021939 < min=1.0 |     1 |       0.1% | ZAMAUSDT/15m/S3 |
| Adverse gap: R degraded 47% (min 50%) |     1 |       0.1% | HAEDALUSDT/1h/S7 |
| Microcap short blocked: price=0.014383 < min=1.0 |     1 |       0.1% | BABYUSDT/15m/S3 |
| Microcap short blocked: price=0.098251 < min=1.0 |     1 |       0.1% | BELUSDT/15m/S1 |
| Microcap short blocked: price=0.025587 < min=1.0 |     1 |       0.1% | PHAUSDT/1h/S7 |
| Adverse gap: R degraded 19% (min 50%) |     1 |       0.1% | VANRYUSDT/1h/S3 |
| Stop too wide: 20.2% > max 10% |     1 |       0.1% | UMAUSDT/15m/S2 |
| Microcap short blocked: price=0.017071 < min=1.0 |     1 |       0.1% | SUNUSDT/15m/S2 |
| Microcap short blocked: price=0.141429 < min=1.0 |     1 |       0.1% | STGUSDT/1h/S7 |
| Microcap short blocked: price=0.203898 < min=1.0 |     1 |       0.1% | THETAUSDT/1h/S7 |
| Microcap short blocked: price=0.021289 < min=1.0 |     1 |       0.1% | AIXBTUSDT/15m/S3 |

## C. Gate Impact

Gate rejections are identified from `error_message` on trades where `data_available = False`.

### Gate rejection summary

**Microcap short blocked** — 183 trades (13.0%)

- Cannot estimate — payload missing: entry_price, stop_price, r_value

**Uneconomic (fees/R ratio)** — 64 trades (4.5%)

- Cannot estimate — payload missing: entry_price

**R-value invalid after slippage** — 39 trades (2.8%)

- Cannot estimate — payload missing: entry_price

**Adverse gap** — 23 trades (1.6%)

- Cannot estimate — payload missing: entry_price

**Stop too wide** — 12 trades (0.9%)

- Cannot estimate — payload missing: entry_price, stop_price, r_value

*To quantify actual gate impact, re-run the simulation with each gate disabled and compare outcomes.*

### TP Invalidation (post-entry gate)

- **TP invalidated:** 114 / 1087 (10.5%)
- **Avg R of TP-invalidated trades:** -0.05
- **Win rate of TP-invalidated trades:** 36.0%

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |    91 |
| BELOW_COST |    23 |

## D. Segmentation

### Overall

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| ALL                      |   1087 |   44.3% |     -1961.61 |      -1.80 |    0.71 |   -0.12 |     -0.17 |       -71.80 |       2.8 |

### D1. By Setup

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1                       |     15 |   66.7% |         7.16 |       0.48 |    1.13 |    0.06 |      0.17 |       -35.66 |       2.6 |
| S2                       |     17 |   76.5% |        76.15 |       4.48 |    2.58 |    0.08 |      0.25 |       -16.61 |       2.4 |
| S3                       |     31 |   41.9% |      -124.70 |      -4.02 |    0.40 |   -0.13 |     -0.15 |       -37.71 |       2.5 |
| S6                       |    892 |   44.6% |     -1941.47 |      -2.18 |    0.65 |   -0.10 |     -0.16 |       -71.80 |       2.8 |
| S7                       |    132 |   36.4% |        21.25 |       0.16 |    1.03 |   -0.25 |     -0.44 |       -41.29 |       2.2 |

### D2. By Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| long                     |    949 |   43.9% |     -1607.51 |      -1.69 |    0.73 |   -0.12 |     -0.18 |       -56.87 |       2.7 |
| short                    |    138 |   47.1% |      -354.10 |      -2.57 |    0.56 |   -0.11 |     -0.09 |       -71.80 |       3.1 |

### D3. By Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 15m                      |    669 |   46.5% |      -814.67 |      -1.22 |    0.77 |   -0.09 |     -0.14 |       -48.99 |       2.8 |
| 1h                       |    403 |   40.9% |      -984.96 |      -2.44 |    0.67 |   -0.16 |     -0.23 |       -71.80 |       2.7 |
| 4h                       |     15 |   40.0% |      -161.98 |     -10.80 |    0.16 |   -0.19 |     -0.39 |       -41.29 |       2.3 |

### D4. By Exit Reason

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| FAIL_FAST                |    361 |    0.0% |     -4007.82 |     -11.10 |    0.00 |   -0.43 |     -0.39 |       -49.67 |       1.2 |
| STOP_LOSS                |    156 |    0.0% |     -2223.82 |     -14.26 |    0.00 |   -1.34 |     -1.16 |       -71.80 |       2.9 |
| TIME_STOP                |     77 |   31.2% |      -314.38 |      -4.08 |    0.26 |   -0.20 |     -0.16 |       -32.06 |       8.6 |
| TP1                      |    345 |  100.0% |      3119.77 |       9.04 |    0.00 |    0.44 |      0.42 |         0.15 |       2.9 |
| TP2                      |     18 |  100.0% |        35.79 |       1.99 |    0.00 |    0.15 |      0.13 |         0.31 |       1.9 |
| TRAIL_STOP               |    130 |   73.1% |      1428.85 |      10.99 |   25.80 |    0.77 |      0.29 |        -4.13 |       3.3 |

### D5. By Setup × Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / long                |     11 |   72.7% |         2.76 |       0.25 |    1.06 |    0.06 |      0.35 |       -35.66 |       2.4 |
| S1 / short               |      4 |   50.0% |         4.40 |       1.10 |    1.66 |    0.06 |      0.04 |        -4.87 |       3.2 |
| S2 / long                |     17 |   76.5% |        76.15 |       4.48 |    2.58 |    0.08 |      0.25 |       -16.61 |       2.4 |
| S3 / long                |     24 |   37.5% |      -111.14 |      -4.63 |    0.36 |   -0.19 |     -0.22 |       -37.71 |       2.5 |
| S3 / short               |      7 |   57.1% |       -13.55 |      -1.94 |    0.62 |    0.09 |      0.32 |       -21.09 |       2.4 |
| S6 / long                |    781 |   44.6% |     -1626.09 |      -2.08 |    0.67 |   -0.10 |     -0.16 |       -56.87 |       2.8 |
| S6 / short               |    111 |   45.0% |      -315.38 |      -2.84 |    0.55 |   -0.13 |     -0.18 |       -71.80 |       3.3 |
| S7 / long                |    116 |   33.6% |        50.81 |       0.44 |    1.07 |   -0.27 |     -0.52 |       -41.29 |       2.3 |
| S7 / short               |     16 |   56.2% |       -29.56 |      -1.85 |    0.38 |   -0.12 |      0.06 |       -13.79 |       1.8 |

### D6. By Setup × Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / 15m                 |      7 |   57.1% |         2.27 |       0.32 |    1.16 |   -0.06 |      0.05 |       -10.67 |       3.3 |
| S1 / 1h                  |      8 |   75.0% |         4.88 |       0.61 |    1.12 |    0.17 |      0.37 |       -35.66 |       2.0 |
| S2 / 15m                 |      9 |   88.9% |        73.61 |       8.18 |    5.43 |    0.20 |      0.25 |       -16.61 |       1.8 |
| S2 / 1h                  |      8 |   62.5% |         2.55 |       0.32 |    1.08 |   -0.04 |      0.27 |       -15.45 |       3.0 |
| S3 / 15m                 |     24 |   50.0% |       -78.59 |      -3.27 |    0.45 |   -0.10 |     -0.04 |       -37.71 |       2.8 |
| S3 / 1h                  |      7 |   14.3% |       -46.11 |      -6.59 |    0.30 |   -0.21 |     -0.30 |       -17.65 |       1.6 |
| S6 / 15m                 |    629 |   45.6% |      -811.96 |      -1.29 |    0.76 |   -0.09 |     -0.16 |       -48.99 |       2.8 |
| S6 / 1h                  |    263 |   42.2% |     -1129.51 |      -4.29 |    0.49 |   -0.13 |     -0.16 |       -71.80 |       3.0 |
| S7 / 1h                  |    117 |   35.9% |       183.22 |       1.57 |    1.30 |   -0.26 |     -0.45 |       -28.45 |       2.2 |
| S7 / 4h                  |     15 |   40.0% |      -161.98 |     -10.80 |    0.16 |   -0.19 |     -0.39 |       -41.29 |       2.3 |

### D7. By Date (entry_ts)

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 2026-02-09               |     53 |   56.6% |        58.10 |       1.10 |    1.21 |    0.10 |      0.32 |       -31.51 |       2.5 |
| 2026-02-10               |     68 |   39.7% |      -227.20 |      -3.34 |    0.50 |   -0.13 |     -0.21 |       -30.46 |       2.5 |
| 2026-02-11               |    112 |   49.1% |        84.20 |       0.75 |    1.09 |    0.05 |     -0.07 |       -71.80 |       2.3 |
| 2026-02-12               |    135 |   45.9% |      -395.88 |      -2.93 |    0.56 |   -0.09 |     -0.14 |       -45.91 |       2.5 |
| 2026-02-13               |    206 |   50.5% |       181.16 |       0.88 |    1.19 |   -0.00 |      0.02 |       -46.51 |       3.5 |
| 2026-02-14               |    466 |   39.7% |     -1391.16 |      -2.99 |    0.50 |   -0.20 |     -0.26 |       -56.30 |       2.7 |
| 2026-02-16               |     47 |   40.4% |      -270.83 |      -5.76 |    0.35 |   -0.49 |     -0.43 |       -49.67 |       2.0 |

### D8. By Hour of Day (entry_ts UTC)

| Hour  | Trades | Win Rate | Avg PnL    |
|:------|-------:|---------:|-----------:|
| 00:00 |     33 |    42.4% |      -1.55 |
| 01:00 |     56 |    39.3% |      -6.11 |
| 02:00 |     19 |    57.9% |       3.38 |
| 03:00 |     35 |    34.3% |      -3.67 |
| 04:00 |     32 |    50.0% |      -2.01 |
| 05:00 |     31 |    51.6% |       2.84 |
| 06:00 |     58 |    46.6% |      -1.69 |
| 07:00 |     50 |    46.0% |      -3.64 |
| 08:00 |     49 |    71.4% |       1.88 |
| 09:00 |    112 |    41.1% |      -2.04 |
| 10:00 |     56 |    42.9% |      -3.75 |
| 11:00 |     52 |    17.3% |     -10.42 |
| 12:00 |     48 |    35.4% |      -3.68 |
| 13:00 |     20 |    35.0% |      -6.74 |
| 14:00 |     25 |    44.0% |       0.23 |
| 15:00 |     51 |    51.0% |       0.26 |
| 16:00 |     89 |    58.4% |       0.67 |
| 17:00 |     73 |    56.2% |      -0.46 |
| 18:00 |     18 |    55.6% |       0.22 |
| 19:00 |     27 |    40.7% |      -0.10 |
| 20:00 |     43 |    37.2% |      -2.59 |
| 21:00 |     44 |    38.6% |      -3.59 |
| 22:00 |     50 |    32.0% |       5.94 |
| 23:00 |     16 |    18.8% |      -7.70 |

### D9. By Day of Week (entry_ts)

| Day | Trades | Win Rate | Avg PnL    |
|:----|-------:|---------:|-----------:|
| Mon |    100 |    49.0% |      -2.13 |
| Tue |     68 |    39.7% |      -3.34 |
| Wed |    112 |    49.1% |       0.75 |
| Thu |    135 |    45.9% |      -2.93 |
| Fri |    206 |    50.5% |       0.88 |
| Sat |    466 |    39.7% |      -2.99 |

## E. Exit Mechanics

### E1. Exit Reason Distribution

| Exit Reason  | Count | % of Total |
|:-------------|------:|-----------:|
| FAIL_FAST    |   361 |      33.2% |
| TP1          |   345 |      31.7% |
| STOP_LOSS    |   156 |      14.4% |
| TRAIL_STOP   |   130 |      12.0% |
| TIME_STOP    |    77 |       7.1% |
| TP2          |    18 |       1.7% |

### E2. Exit Reason × Setup

| Exit Reason  |    S1 |    S2 |    S3 |    S6 |    S7 | Total |
|:-------------|------:|------:|------:|------:|------:|------:|
| FAIL_FAST    |     1 |     2 |    11 |   322 |    25 |   361 |
| TP1          |     6 |     8 |    10 |   309 |    12 |   345 |
| STOP_LOSS    |     2 |     2 |     5 |    98 |    49 |   156 |
| TRAIL_STOP   |     2 |     4 |     4 |    83 |    37 |   130 |
| TIME_STOP    |     4 |     1 |     1 |    70 |     1 |    77 |
| TP2          |     0 |     0 |     0 |    10 |     8 |    18 |

### E3. Exit Reason × Timeframe

| Exit Reason  |  15m |   1h |   4h | Total |
|:-------------|-----:|-----:|-----:|------:|
| FAIL_FAST    |  219 |  136 |    6 |   361 |
| TP1          |  244 |   99 |    2 |   345 |
| STOP_LOSS    |   76 |   77 |    3 |   156 |
| TRAIL_STOP   |   66 |   61 |    3 |   130 |
| TIME_STOP    |   61 |   16 |    0 |    77 |
| TP2          |    3 |   14 |    1 |    18 |

### E4. Bars Held Distribution (all exits)

| Percentile | Bars |
|-----------:|-----:|
| 5th |    1 |
| 10th |    1 |
| 25th |    1 |
| 50th |    1 |
| 75th |    3 |
| 90th |    8 |
| 95th |    9 |
| Mean |  2.8 |

### E5. Bars Held by Exit Reason

| Exit Reason  | Count | Mean  | P25 | Median | P75 | Max |
|:-------------|------:|------:|----:|-------:|----:|----:|
| FAIL_FAST    |   361 |   1.2 |   1 |      1 |   1 |   2 |
| STOP_LOSS    |   156 |   2.9 |   1 |      1 |   3 |  58 |
| TIME_STOP    |    77 |   8.6 |   9 |      9 |   9 |   9 |
| TP1          |   345 |   2.9 |   1 |      2 |   4 |  26 |
| TP2          |    18 |   1.9 |   1 |      1 |   2 |   9 |
| TRAIL_STOP   |   130 |   3.3 |   1 |      1 |   3 |  49 |

### E6. STOP_LOSS Bars Held Detail

- Count: 156, P25: 1, Median: 1, P75: 3, Max: 58
- **Immediate stops (bars ≤ 1):** 101 (64.7%)

### E7. TP1 Bars Held Detail

- Count: 345, P25: 1, Median: 2, P75: 4, Max: 26

### E7. TP2 Bars Held Detail

- Count: 18, P25: 1, Median: 1, P75: 2, Max: 9

### E8. TP Invalidation

- **TP invalidated:** 114 / 1087 (10.5%)

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |    91 |
| BELOW_COST |    23 |

## F. Outlier Flags

### F1. Top 10 Winners (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | MEUSDT     | 1h  | S7    | long  |     12.80 |    441.35 | TRAIL_STOP   |   11 |
| 2 | ZKCUSDT    | 15m | S6    | long  |      2.45 |    132.17 | TRAIL_STOP   |    1 |
| 3 | ZENUSDT    | 1h  | S7    | long  |      3.99 |     67.39 | TRAIL_STOP   |   19 |
| 4 | LINEAUSDT  | 15m | S6    | long  |      0.91 |     59.94 | TRAIL_STOP   |    1 |
| 5 | TNSRUSDT   | 15m | S6    | long  |      0.95 |     57.96 | TRAIL_STOP   |    1 |
| 6 | BANANAS31USDT | 1h  | S7    | long  |     10.25 |     45.68 | TRAIL_STOP   |    1 |
| 7 | AXSUSDT    | 15m | S2    | long  |      0.50 |     44.18 | TP1          |    1 |
| 8 | AWEUSDT    | 1h  | S7    | long  |      4.80 |     41.97 | TRAIL_STOP   |    1 |
| 9 | EULUSDT    | 15m | S6    | long  |      0.48 |     38.81 | TP1          |    6 |
| 10 | ZECUSDT    | 15m | S6    | long  |      0.48 |     36.63 | TP1          |    5 |

### F2. Top 10 Losers (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | ENSOUSDT   | 1h  | S6    | short |     -2.77 |    -71.80 | STOP_LOSS    |    1 |
| 2 | ENSOUSDT   | 1h  | S6    | short |     -2.77 |    -71.80 | STOP_LOSS    |    1 |
| 3 | OPENUSDT   | 1h  | S6    | long  |     -1.23 |    -56.87 | STOP_LOSS    |    1 |
| 4 | MEMEUSDT   | 1h  | S6    | long  |     -1.29 |    -56.30 | STOP_LOSS    |   19 |
| 5 | HUMAUSDT   | 1h  | S6    | long  |     -0.77 |    -49.67 | FAIL_FAST    |    1 |
| 6 | TURBOUSDT  | 15m | S6    | long  |     -1.02 |    -48.99 | STOP_LOSS    |    9 |
| 7 | CFXUSDT    | 15m | S6    | long  |     -0.85 |    -46.51 | FAIL_FAST    |    1 |
| 8 | ASTERUSDT  | 1h  | S6    | long  |     -0.66 |    -45.91 | FAIL_FAST    |    1 |
| 9 | ARKUSDT    | 15m | S6    | long  |     -1.56 |    -45.63 | STOP_LOSS    |    1 |
| 10 | ARDRUSDT   | 15m | S6    | long  |     -1.02 |    -44.50 | STOP_LOSS    |    1 |

### F3. Instant Exits (bars_held ≤ 1)

- **Count:** 662 / 1087 (60.9%)

| Exit Reason  | Count | % of Instant |
|:-------------|------:|-------------:|
| FAIL_FAST    |   298 |        45.0% |
| TP1          |   169 |        25.5% |
| STOP_LOSS    |   101 |        15.3% |
| TRAIL_STOP   |    80 |        12.1% |
| TP2          |    13 |         2.0% |
| TIME_STOP    |     1 |         0.2% |

| Setup | Count | % of Instant |
|:------|------:|-------------:|
| S6    |   523 |        79.0% |
| S7    |   104 |        15.7% |
| S3    |    20 |         3.0% |
| S2    |    11 |         1.7% |
| S1    |     4 |         0.6% |

### F4. PnL / Exit Reason Disagreement

No disagreements found (all STOP_LOSS/FAIL_FAST have negative PnL, all TP1/TP2 have positive PnL).

### F5. Top 10 Longest Held

| # | Symbol     | TF  | Setup | Dir   | Bars | R Multiple | Exit Reason  |
|--:|:-----------|:----|:------|:------|-----:|----------:|:-------------|
| 1 | NFPUSDT    | 15m | S6    | long  |   58 |     -1.05 | STOP_LOSS    |
| 2 | PUNDIXUSDT | 1h  | S7    | long  |   49 |      0.86 | TRAIL_STOP   |
| 3 | SUNUSDT    | 1h  | S6    | long  |   27 |      4.57 | TRAIL_STOP   |
| 4 | CUSDT      | 1h  | S6    | long  |   26 |      0.68 | TP1          |
| 5 | REDUSDT    | 15m | S6    | long  |   24 |     -1.06 | STOP_LOSS    |
| 6 | PARTIUSDT  | 15m | S6    | long  |   23 |     -0.04 | TRAIL_STOP   |
| 7 | ALCXUSDT   | 1h  | S6    | long  |   20 |      0.65 | TP1          |
| 8 | ZENUSDT    | 1h  | S7    | long  |   19 |      3.99 | TRAIL_STOP   |
| 9 | QNTUSDT    | 1h  | S6    | long  |   19 |      0.17 | TRAIL_STOP   |
| 10 | MEMEUSDT   | 1h  | S6    | long  |   19 |     -1.29 | STOP_LOSS    |

### F6. Score Anomalies

Score Q25=0.785, Q75=0.907

**High-score losers (score ≥ Q75, R < 0): 136**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| INJUSDT    | S7    |  0.912 |     -3.14 | STOP_LOSS    |
| ENSOUSDT   | S6    |  0.915 |     -2.77 | STOP_LOSS    |
| GTCUSDT    | S7    |  0.958 |     -2.20 | STOP_LOSS    |
| TWTUSDT    | S6    |  0.965 |     -1.74 | STOP_LOSS    |
| ZENUSDT    | S6    |  0.960 |     -1.71 | STOP_LOSS    |
| TLMUSDT    | S6    |  0.920 |     -1.49 | STOP_LOSS    |
| MEMEUSDT   | S7    |  1.000 |     -1.43 | STOP_LOSS    |
| SKYUSDT    | S7    |  0.986 |     -1.37 | STOP_LOSS    |
| XTZUSDT    | S6    |  0.914 |     -1.27 | STOP_LOSS    |
| ACHUSDT    | S6    |  0.938 |     -1.27 | STOP_LOSS    |

**Low-score winners (score ≤ Q25, R > 0): 117**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| BANANAS31USDT | S7    |  0.543 |     10.25 | TRAIL_STOP   |
| ZENUSDT    | S7    |  0.682 |      3.99 | TRAIL_STOP   |
| NEARUSDT   | S6    |  0.601 |      2.12 | TRAIL_STOP   |
| MLNUSDT    | S7    |  0.651 |      1.56 | TRAIL_STOP   |
| JSTUSDT    | S7    |  0.474 |      1.33 | TRAIL_STOP   |
| BBUSDT     | S7    |  0.535 |      1.31 | TRAIL_STOP   |
| GASUSDT    | S2    |  0.750 |      1.20 | TRAIL_STOP   |
| ARKMUSDT   | S7    |  0.759 |      1.06 | TRAIL_STOP   |
| PROMUSDT   | S3    |  0.554 |      1.03 | TRAIL_STOP   |
| LINEAUSDT  | S6    |  0.777 |      0.91 | TRAIL_STOP   |

## G. Feature Coverage

Comparison of trace_json keys found vs what `FeatureExtractor` captures.

- **Traces with data:** 1087 / 1408
- **Total feature columns:** 121

| trace_json key | In FeatureExtractor? | In features.csv? | Action taken |
|:---------------|:---------------------|:-----------------|:-------------|
| cond.why_0                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_1                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_2                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_3                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_4                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_5                               (100.0%) | No                   | Yes              | Extracted by profiler |
| cond.why_6                               (86.5%) | No                   | Yes              | Extracted by profiler |
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
| ind.htf_bb_mb                            (98.6%) | Yes                  | Yes              | Already captured |
| ind.htf_close                            (98.6%) | Yes                  | Yes              | Already captured |
| ind.htf_sma50                            (98.6%) | Yes                  | Yes              | Already captured |
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
| ind.vwap                                 (98.4%) | Yes                  | Yes              | Already captured |
| ind.vwap_distance_atr                    (98.4%) | Yes                  | Yes              | Already captured |
| ind.vwap_lower_2sd                       (98.4%) | Yes                  | Yes              | Already captured |
| ind.vwap_upper_2sd                       (98.4%) | Yes                  | Yes              | Already captured |
| score.bias_strength                      (13.5%) | Yes                  | Yes              | Already captured |
| score.break_quality                      (2.9%) | Yes                  | Yes              | Already captured |
| score.break_strength                     (12.1%) | Yes                  | Yes              | Already captured |
| score.clean_close                        (100.0%) | Yes                  | Yes              | Already captured |
| score.conv_speed                         (1.6%) | Yes                  | Yes              | Already captured |
| score.conv_tight                         (1.6%) | Yes                  | Yes              | Already captured |
| score.div_magnitude                      (2.9%) | Yes                  | Yes              | Already captured |
| score.htf_confluence                     (12.1%) | Yes                  | Yes              | Already captured |
| score.participation                      (87.9%) | Yes                  | Yes              | Already captured |
| score.pullback_quality                   (82.1%) | Yes                  | Yes              | Already captured |
| score.retrace_depth                      (82.1%) | Yes                  | Yes              | Already captured |
| score.rsi_extremity                      (2.9%) | Yes                  | Yes              | Already captured |
| score.severity                           (1.4%) | Yes                  | Yes              | Already captured |
| score.span_quality                       (2.9%) | Yes                  | Yes              | Already captured |
| score.trend_strength                     (83.6%) | Yes                  | Yes              | Already captured |
| score.volume_quality                     (12.1%) | Yes                  | Yes              | Already captured |
| state.bars_out_of_squeeze                (1.4%) | Yes                  | Yes              | Already captured |
| state.bb_width_pctile                    (1.4%) | Yes                  | Yes              | Already captured |
| state.bearish_divergence                 (0.6%) | Yes                  | Yes              | Already captured |
| state.bias_score                         (1.4%) | Yes                  | Yes              | Already captured |
| state.box_high                           (1.4%) | Yes                  | Yes              | Already captured |
| state.box_low                            (1.4%) | Yes                  | Yes              | Already captured |
| state.btc_box_high                       (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_box_low                        (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_breakout_direction             (0.0%) | Yes                  | Yes              | Already captured |
| state.btc_severe                         (1.4%) | Yes                  | Yes              | Already captured |
| state.btc_shock_down                     (1.6%) | Yes                  | Yes              | Already captured |
| state.btc_shock_up                       (1.6%) | Yes                  | Yes              | Already captured |
| state.bullish_divergence                 (2.2%) | Yes                  | Yes              | Already captured |
| state.converging                         (1.6%) | Yes                  | Yes              | Already captured |
| state.dec_count                          (1.6%) | Yes                  | Yes              | Already captured |
| state.donchian_high                      (12.1%) | Yes                  | Yes              | Already captured |
| state.donchian_low                       (12.1%) | Yes                  | Yes              | Already captured |
| state.donchian_mid                       (12.1%) | Yes                  | Yes              | Already captured |
| state.ema20_slope_negative               (82.1%) | Yes                  | Yes              | Already captured |
| state.ema20_slope_positive               (82.1%) | Yes                  | Yes              | Already captured |
| state.ema50_slope_negative               (82.1%) | Yes                  | Yes              | Already captured |
| state.ema50_slope_positive               (82.1%) | Yes                  | Yes              | Already captured |
| state.ema_stacked_long                   (82.1%) | Yes                  | Yes              | Already captured |
| state.ema_stacked_short                  (82.1%) | Yes                  | Yes              | Already captured |
| state.fail_fast_bars                     (1.4%) | Yes                  | Yes              | Already captured |
| state.gap_decreasing                     (1.6%) | Yes                  | Yes              | Already captured |
| state.has_divergence                     (2.9%) | Yes                  | Yes              | Already captured |
| state.held_ema50                         (82.1%) | Yes                  | Yes              | Already captured |
| state.htf_aligned                        (82.1%) | Yes                  | Yes              | Already captured |
| state.htf_bias                           (94.2%) | Yes                  | Yes              | Already captured |
| state.htf_bias_score                     (94.2%) | Yes                  | Yes              | Already captured |
| state.is_narrow                          (1.6%) | Yes                  | Yes              | Already captured |
| state.long_allowed                       (2.9%) | Yes                  | Yes              | Already captured |
| state.long_breakout                      (12.1%) | Yes                  | Yes              | Already captured |
| state.ma_gap_atr                         (1.6%) | Yes                  | Yes              | Already captured |
| state.pre_cross_long                     (1.6%) | Yes                  | Yes              | Already captured |
| state.pre_cross_long_t1                  (1.6%) | Yes                  | Yes              | Already captured |
| state.pre_cross_short                    (1.6%) | Yes                  | Yes              | Already captured |
| state.pre_cross_short_t1                 (1.6%) | Yes                  | Yes              | Already captured |
| state.prev_donchian_high                 (12.1%) | Yes                  | Yes              | Already captured |
| state.prev_donchian_low                  (12.1%) | Yes                  | Yes              | Already captured |
| state.pullback_detected                  (82.1%) | Yes                  | Yes              | Already captured |
| state.reclaimed_ema20                    (82.1%) | Yes                  | Yes              | Already captured |
| state.retrace_depth_atr                  (82.1%) | Yes                  | Yes              | Already captured |
| state.ripe                               (1.4%) | Yes                  | Yes              | Already captured |
| state.ripe_at_prev_bar                   (1.4%) | Yes                  | Yes              | Already captured |
| state.rsi_bias                           (12.1%) | Yes                  | Yes              | Already captured |
| state.rsi_extreme_high                   (2.9%) | Yes                  | Yes              | Already captured |
| state.rsi_extreme_low                    (2.9%) | Yes                  | Yes              | Already captured |
| state.rsi_reclaimed_30                   (2.9%) | Yes                  | Yes              | Already captured |
| state.rsi_reclaimed_70                   (2.9%) | Yes                  | Yes              | Already captured |
| state.short_allowed                      (2.9%) | Yes                  | Yes              | Already captured |
| state.short_breakout                     (12.1%) | Yes                  | Yes              | Already captured |
| state.sma200_slope_negative              (1.6%) | Yes                  | Yes              | Already captured |
| state.sma200_slope_positive              (1.6%) | Yes                  | Yes              | Already captured |
| state.sma50_slope_negative               (1.6%) | Yes                  | Yes              | Already captured |
| state.sma50_slope_positive               (1.6%) | Yes                  | Yes              | Already captured |
| state.squeeze_age                        (1.4%) | Yes                  | Yes              | Already captured |
| state.squeeze_on                         (1.4%) | Yes                  | Yes              | Already captured |
| state.time_stop_bars                     (1.6%) | Yes                  | Yes              | Already captured |
| state.touched_ema20                      (82.1%) | Yes                  | Yes              | Already captured |
| state.trend_direction                    (82.1%) | Yes                  | Yes              | Already captured |
| state.trend_strength                     (82.1%) | Yes                  | Yes              | Already captured |
| state.was_wide                           (1.6%) | Yes                  | Yes              | Already captured |
