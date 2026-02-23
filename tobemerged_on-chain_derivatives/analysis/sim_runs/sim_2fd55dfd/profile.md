# Simulation Profile: 2fd55dfd

Generated: 2026-02-16 20:27:48 UTC

## A. Run Metadata

- **Run ID:** `2fd55dfd-3bf6-4f62-85ee-1da09e06c286`
- **Type:** SIMULATION
- **Status:** SUCCESS
- **Name:** Phase5-trail-fix
- **Engine:** 1.0.0
- **Created:** 2026-02-16 20:05:43.894104+00:00
- **Started:** 2026-02-16 20:05:44.023821+00:00
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
  "winning_trades": 480,
  "losing_trades": 607,
  "win_rate": 0.4416,
  "total_r": -158.08,
  "avg_r": -0.1454,
  "max_r": 0.0,
  "min_r": 0.0,
  "gross_pnl": 0.0,
  "total_fees": 0.0,
  "total_slippage": 0.0,
  "net_pnl": -2480.83,
  "max_drawdown_pct": 0.0,
  "profit_factor": 0.618,
  "expectancy": -0.1454,
  "bars_processed": 0,
  "assets_processed": 0,
  "assets_skipped": 0,
  "signals_generated": 1408,
  "execution_time_seconds": 212.52590656280518,
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
      "wins": 392,
      "pnl_net": -2082.76,
      "total_r": -102.99,
      "win_rate": 0.43946188340807174,
      "avg_r": -0.11545876527541296
    },
    "S2": {
      "trades": 17,
      "wins": 13,
      "pnl_net": 72.62,
      "total_r": 2.03,
      "win_rate": 0.7647058823529411,
      "avg_r": 0.11965750665599911
    },
    "S3": {
      "trades": 31,
      "wins": 13,
      "pnl_net": -125.7,
      "total_r": -4.47,
      "win_rate": 0.41935483870967744,
      "avg_r": -0.14422667532351546
    },
    "S7": {
      "trades": 132,
      "wins": 52,
      "pnl_net": -358.92,
      "total_r": -53.78,
      "win_rate": 0.3939393939393939,
      "avg_r": -0.4073962067045544
    },
    "S1": {
      "trades": 15,
      "wins": 10,
      "pnl_net": 13.94,
      "total_r": 1.12,
      "win_rate": 0.6666666666666666,
      "avg_r": 0.07459162872099304
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
| Uneconomic: fees/R=0.87 > max 0.3 |     1 |       0.1% | EURUSDT/15m/S6 |
| Uneconomic: fees/R=1.82 > max 0.3 |     1 |       0.1% | EURUSDT/1h/S7 |
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
- **Avg R of TP-invalidated trades:** -0.11
- **Win rate of TP-invalidated trades:** 32.5%

| Reason | Count |
|:-------|------:|
| WRONG_SIDE |    91 |
| BELOW_COST |    23 |

## D. Segmentation

### Overall

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| ALL                      |   1087 |   44.2% |     -2480.83 |      -2.28 |    0.62 |   -0.15 |     -0.16 |       -71.80 |       2.4 |

### D1. By Setup

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1                       |     15 |   66.7% |        13.94 |       0.93 |    1.25 |    0.07 |      0.35 |       -35.66 |       2.6 |
| S2                       |     17 |   76.5% |        72.62 |       4.27 |    2.50 |    0.12 |      0.28 |       -16.61 |       2.5 |
| S3                       |     31 |   41.9% |      -125.70 |      -4.05 |    0.40 |   -0.14 |     -0.19 |       -37.71 |       2.2 |
| S6                       |    892 |   43.9% |     -2082.76 |      -2.33 |    0.62 |   -0.12 |     -0.16 |       -71.80 |       2.5 |
| S7                       |    132 |   39.4% |      -358.92 |      -2.72 |    0.48 |   -0.41 |     -0.42 |       -40.81 |       1.4 |

### D2. By Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| long                     |    949 |   43.9% |     -2116.88 |      -2.23 |    0.63 |   -0.15 |     -0.16 |       -56.87 |       2.3 |
| short                    |    138 |   45.7% |      -363.95 |      -2.64 |    0.53 |   -0.12 |     -0.09 |       -71.80 |       2.9 |

### D3. By Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 15m                      |    669 |   46.0% |      -916.22 |      -1.37 |    0.74 |   -0.09 |     -0.13 |       -48.99 |       2.5 |
| 1h                       |    403 |   41.2% |     -1508.93 |      -3.74 |    0.48 |   -0.23 |     -0.21 |       -71.80 |       2.2 |
| 4h                       |     15 |   40.0% |       -55.67 |      -3.71 |    0.43 |   -0.04 |     -0.04 |       -40.81 |       1.7 |

### D4. By Exit Reason

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| FAIL_FAST                |    361 |    0.0% |     -4008.44 |     -11.10 |    0.00 |   -0.43 |     -0.39 |       -49.67 |       1.2 |
| STOP_LOSS                |    144 |    0.0% |     -1955.26 |     -13.58 |    0.00 |   -1.36 |     -1.17 |       -71.80 |       2.0 |
| TIME_STOP                |     75 |   29.3% |      -327.67 |      -4.37 |    0.23 |   -0.21 |     -0.18 |       -32.06 |       8.8 |
| TP1                      |    421 |  100.0% |      3498.07 |       8.31 |    0.00 |    0.43 |      0.42 |         0.05 |       2.3 |
| TP2                      |     13 |  100.0% |        29.81 |       2.29 |    0.00 |    0.12 |      0.11 |         0.31 |       2.1 |
| TRAIL_STOP               |     73 |   32.9% |       282.67 |       3.87 |    3.72 |    0.37 |     -0.08 |       -16.75 |       3.4 |

### D5. By Setup × Direction

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / long                |     11 |   72.7% |         9.54 |       0.87 |    1.20 |    0.08 |      0.37 |       -35.66 |       2.4 |
| S1 / short               |      4 |   50.0% |         4.40 |       1.10 |    1.66 |    0.06 |      0.04 |        -4.87 |       3.2 |
| S2 / long                |     17 |   76.5% |        72.62 |       4.27 |    2.50 |    0.12 |      0.28 |       -16.61 |       2.5 |
| S3 / long                |     24 |   37.5% |      -114.59 |      -4.77 |    0.34 |   -0.22 |     -0.27 |       -37.71 |       2.1 |
| S3 / short               |      7 |   57.1% |       -11.11 |      -1.59 |    0.69 |    0.12 |      0.32 |       -21.09 |       2.4 |
| S6 / long                |    781 |   44.2% |     -1754.94 |      -2.25 |    0.63 |   -0.11 |     -0.15 |       -56.87 |       2.5 |
| S6 / short               |    111 |   42.3% |      -327.82 |      -2.95 |    0.53 |   -0.14 |     -0.18 |       -71.80 |       3.1 |
| S7 / long                |    116 |   36.2% |      -329.51 |      -2.84 |    0.49 |   -0.44 |     -0.45 |       -40.81 |       1.4 |
| S7 / short               |     16 |   62.5% |       -29.41 |      -1.84 |    0.33 |   -0.17 |      0.12 |       -13.79 |       1.6 |

### D6. By Setup × Timeframe

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| S1 / 15m                 |      7 |   57.1% |        10.02 |       1.43 |    1.70 |   -0.02 |      0.05 |       -10.67 |       3.3 |
| S1 / 1h                  |      8 |   75.0% |         3.92 |       0.49 |    1.10 |    0.16 |      0.37 |       -35.66 |       2.0 |
| S2 / 15m                 |      9 |   88.9% |        65.14 |       7.24 |    4.92 |    0.17 |      0.25 |       -16.61 |       2.1 |
| S2 / 1h                  |      8 |   62.5% |         7.48 |       0.94 |    1.24 |    0.06 |      0.35 |       -15.45 |       3.0 |
| S3 / 15m                 |     24 |   50.0% |       -79.60 |      -3.32 |    0.44 |   -0.13 |     -0.04 |       -37.71 |       2.4 |
| S3 / 1h                  |      7 |   14.3% |       -46.11 |      -6.59 |    0.30 |   -0.21 |     -0.30 |       -17.65 |       1.6 |
| S6 / 15m                 |    629 |   45.2% |      -911.79 |      -1.45 |    0.73 |   -0.10 |     -0.15 |       -48.99 |       2.5 |
| S6 / 1h                  |    263 |   41.1% |     -1170.98 |      -4.45 |    0.46 |   -0.16 |     -0.16 |       -71.80 |       2.6 |
| S7 / 1h                  |    117 |   39.3% |      -303.25 |      -2.59 |    0.49 |   -0.45 |     -0.45 |       -28.45 |       1.4 |
| S7 / 4h                  |     15 |   40.0% |       -55.67 |      -3.71 |    0.43 |   -0.04 |     -0.04 |       -40.81 |       1.7 |

### D7. By Date (entry_ts)

| Segment                  | Trades | WinRate | Total PnL    | Avg PnL    | PF      | Avg R   | Median R  | Max DD Trade | Avg Bars  |
|:-------------------------|-------:|--------:|-------------:|-----------:|--------:|--------:|----------:|-------------:|----------:|
| 2026-02-09               |     53 |   58.5% |        59.50 |       1.12 |    1.22 |    0.10 |      0.33 |       -31.51 |       2.5 |
| 2026-02-10               |     68 |   38.2% |      -227.89 |      -3.35 |    0.50 |   -0.13 |     -0.21 |       -30.46 |       2.4 |
| 2026-02-11               |    112 |   47.3% |      -371.03 |      -3.31 |    0.59 |   -0.14 |     -0.07 |       -71.80 |       2.0 |
| 2026-02-12               |    135 |   45.9% |      -427.30 |      -3.17 |    0.52 |   -0.10 |     -0.14 |       -45.91 |       2.5 |
| 2026-02-13               |    206 |   51.0% |        -9.32 |      -0.05 |    0.99 |   -0.06 |      0.03 |       -46.51 |       2.7 |
| 2026-02-14               |    466 |   39.9% |     -1197.26 |      -2.57 |    0.54 |   -0.18 |     -0.23 |       -48.99 |       2.4 |
| 2026-02-16               |     47 |   36.2% |      -307.53 |      -6.54 |    0.27 |   -0.59 |     -0.39 |       -49.67 |       1.8 |

### D8. By Hour of Day (entry_ts UTC)

| Hour  | Trades | Win Rate | Avg PnL    |
|:------|-------:|---------:|-----------:|
| 00:00 |     33 |    48.5% |      -1.46 |
| 01:00 |     56 |    37.5% |      -6.06 |
| 02:00 |     19 |    52.6% |       2.94 |
| 03:00 |     35 |    34.3% |      -3.94 |
| 04:00 |     32 |    46.9% |      -1.85 |
| 05:00 |     31 |    48.4% |      -2.45 |
| 06:00 |     58 |    50.0% |      -1.71 |
| 07:00 |     50 |    44.0% |      -4.49 |
| 08:00 |     49 |    69.4% |       2.24 |
| 09:00 |    112 |    42.9% |      -1.89 |
| 10:00 |     56 |    44.6% |      -3.49 |
| 11:00 |     52 |    19.2% |     -10.00 |
| 12:00 |     48 |    39.6% |      -3.62 |
| 13:00 |     20 |    35.0% |      -6.74 |
| 14:00 |     25 |    52.0% |      -1.81 |
| 15:00 |     51 |    43.1% |       0.18 |
| 16:00 |     89 |    55.1% |       1.52 |
| 17:00 |     73 |    56.2% |      -0.05 |
| 18:00 |     18 |    50.0% |      -1.46 |
| 19:00 |     27 |    37.0% |       0.70 |
| 20:00 |     43 |    34.9% |      -2.62 |
| 21:00 |     44 |    34.1% |      -3.74 |
| 22:00 |     50 |    40.0% |      -2.40 |
| 23:00 |     16 |    18.8% |      -7.31 |

### D9. By Day of Week (entry_ts)

| Day | Trades | Win Rate | Avg PnL    |
|:----|-------:|---------:|-----------:|
| Mon |    100 |    48.0% |      -2.48 |
| Tue |     68 |    38.2% |      -3.35 |
| Wed |    112 |    47.3% |      -3.31 |
| Thu |    135 |    45.9% |      -3.17 |
| Fri |    206 |    51.0% |      -0.05 |
| Sat |    466 |    39.9% |      -2.57 |

## E. Exit Mechanics

### E1. Exit Reason Distribution

| Exit Reason  | Count | % of Total |
|:-------------|------:|-----------:|
| TP1          |   421 |      38.7% |
| FAIL_FAST    |   361 |      33.2% |
| STOP_LOSS    |   144 |      13.2% |
| TIME_STOP    |    75 |       6.9% |
| TRAIL_STOP   |    73 |       6.7% |
| TP2          |    13 |       1.2% |

### E2. Exit Reason × Setup

| Exit Reason  |    S1 |    S2 |    S3 |    S6 |    S7 | Total |
|:-------------|------:|------:|------:|------:|------:|------:|
| TP1          |     8 |    12 |    11 |   358 |    32 |   421 |
| FAIL_FAST    |     1 |     2 |    11 |   322 |    25 |   361 |
| STOP_LOSS    |     2 |     2 |     5 |    90 |    45 |   144 |
| TIME_STOP    |     4 |     0 |     1 |    69 |     1 |    75 |
| TRAIL_STOP   |     0 |     1 |     3 |    45 |    24 |    73 |
| TP2          |     0 |     0 |     0 |     8 |     5 |    13 |

### E3. Exit Reason × Timeframe

| Exit Reason  |  15m |   1h |   4h | Total |
|:-------------|-----:|-----:|-----:|------:|
| TP1          |  289 |  128 |    4 |   421 |
| FAIL_FAST    |  219 |  136 |    6 |   361 |
| STOP_LOSS    |   70 |   74 |    0 |   144 |
| TIME_STOP    |   60 |   15 |    0 |    75 |
| TRAIL_STOP   |   30 |   39 |    4 |    73 |
| TP2          |    1 |   11 |    1 |    13 |

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
| Mean |  2.4 |

### E5. Bars Held by Exit Reason

| Exit Reason  | Count | Mean  | P25 | Median | P75 | Max |
|:-------------|------:|------:|----:|-------:|----:|----:|
| FAIL_FAST    |   361 |   1.2 |   1 |      1 |   1 |   2 |
| STOP_LOSS    |   144 |   2.0 |   1 |      1 |   2 |   9 |
| TIME_STOP    |    75 |   8.8 |   9 |      9 |   9 |   9 |
| TP1          |   421 |   2.3 |   1 |      1 |   3 |  16 |
| TP2          |    13 |   2.1 |   1 |      1 |   1 |   9 |
| TRAIL_STOP   |    73 |   3.4 |   1 |      2 |   4 |  15 |

### E6. STOP_LOSS Bars Held Detail

- Count: 144, P25: 1, Median: 1, P75: 2, Max: 9
- **Immediate stops (bars ≤ 1):** 101 (70.1%)

### E7. TP1 Bars Held Detail

- Count: 421, P25: 1, Median: 1, P75: 3, Max: 16

### E7. TP2 Bars Held Detail

- Count: 13, P25: 1, Median: 1, P75: 1, Max: 9

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
| 1 | BANANAS31USDT | 1h  | S7    | long  |     10.25 |     45.68 | TRAIL_STOP   |    1 |
| 2 | AXSUSDT    | 15m | S2    | long  |      0.50 |     44.18 | TP1          |    1 |
| 3 | AWEUSDT    | 1h  | S7    | long  |      4.80 |     41.97 | TRAIL_STOP   |    1 |
| 4 | NEARUSDT   | 1h  | S6    | long  |      2.61 |     41.37 | TRAIL_STOP   |   15 |
| 5 | MEUSDT     | 1h  | S7    | long  |      1.13 |     38.87 | TRAIL_STOP   |    3 |
| 6 | EULUSDT    | 15m | S6    | long  |      0.48 |     38.81 | TP1          |    6 |
| 7 | ZECUSDT    | 15m | S6    | long  |      0.48 |     36.63 | TP1          |    5 |
| 8 | ATMUSDT    | 15m | S6    | long  |      0.48 |     36.32 | TP1          |    2 |
| 9 | VIRTUALUSDT | 1h  | S7    | long  |      3.10 |     35.71 | TRAIL_STOP   |    2 |
| 10 | SUSDT      | 15m | S6    | long  |      0.47 |     31.79 | TP1          |    2 |

### F2. Top 10 Losers (by pnl_net)

| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |
|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|
| 1 | ENSOUSDT   | 1h  | S6    | short |     -2.77 |    -71.80 | STOP_LOSS    |    1 |
| 2 | ENSOUSDT   | 1h  | S6    | short |     -2.77 |    -71.80 | STOP_LOSS    |    1 |
| 3 | OPENUSDT   | 1h  | S6    | long  |     -1.23 |    -56.87 | STOP_LOSS    |    1 |
| 4 | HUMAUSDT   | 1h  | S6    | long  |     -0.77 |    -49.67 | FAIL_FAST    |    1 |
| 5 | TURBOUSDT  | 15m | S6    | long  |     -1.02 |    -48.99 | STOP_LOSS    |    9 |
| 6 | CFXUSDT    | 15m | S6    | long  |     -0.85 |    -46.51 | FAIL_FAST    |    1 |
| 7 | ASTERUSDT  | 1h  | S6    | long  |     -0.66 |    -45.91 | FAIL_FAST    |    1 |
| 8 | ARKUSDT    | 15m | S6    | long  |     -1.56 |    -45.63 | STOP_LOSS    |    1 |
| 9 | ARDRUSDT   | 15m | S6    | long  |     -1.02 |    -44.50 | STOP_LOSS    |    1 |
| 10 | BERAUSDT   | 1h  | S6    | long  |     -0.63 |    -43.94 | FAIL_FAST    |    1 |

### F3. Instant Exits (bars_held ≤ 1)

- **Count:** 683 / 1087 (62.8%)

| Exit Reason  | Count | % of Instant |
|:-------------|------:|-------------:|
| FAIL_FAST    |   298 |        43.6% |
| TP1          |   241 |        35.3% |
| STOP_LOSS    |   101 |        14.8% |
| TRAIL_STOP   |    33 |         4.8% |
| TP2          |    10 |         1.5% |

| Setup | Count | % of Instant |
|:------|------:|-------------:|
| S6    |   537 |        78.6% |
| S7    |   109 |        16.0% |
| S3    |    22 |         3.2% |
| S2    |    11 |         1.6% |
| S1    |     4 |         0.6% |

### F4. PnL / Exit Reason Disagreement

No disagreements found (all STOP_LOSS/FAIL_FAST have negative PnL, all TP1/TP2 have positive PnL).

### F5. Top 10 Longest Held

| # | Symbol     | TF  | Setup | Dir   | Bars | R Multiple | Exit Reason  |
|--:|:-----------|:----|:------|:------|-----:|----------:|:-------------|
| 1 | UTKUSDT    | 1h  | S6    | long  |   16 |      1.32 | TP1          |
| 2 | NEARUSDT   | 1h  | S6    | long  |   15 |      2.61 | TRAIL_STOP   |
| 3 | QNTUSDT    | 1h  | S6    | long  |   15 |      0.40 | TRAIL_STOP   |
| 4 | MEMEUSDT   | 1h  | S6    | long  |   15 |     -0.16 | TRAIL_STOP   |
| 5 | CUSDT      | 1h  | S6    | long  |   14 |     -0.03 | TRAIL_STOP   |
| 6 | WOOUSDT    | 1h  | S6    | long  |   13 |      0.64 | TRAIL_STOP   |
| 7 | WUSDT      | 1h  | S7    | long  |   12 |      1.61 | TRAIL_STOP   |
| 8 | WLDUSDT    | 1h  | S6    | long  |   11 |      1.01 | TP1          |
| 9 | DASHUSDT   | 15m | S6    | short |    9 |     -0.40 | TIME_STOP    |
| 10 | FFUSDT     | 15m | S3    | long  |    9 |      0.07 | TIME_STOP    |

### F6. Score Anomalies

Score Q25=0.785, Q75=0.907

**High-score losers (score ≥ Q75, R < 0): 140**

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

**Low-score winners (score ≤ Q25, R > 0): 115**

| Symbol     | Setup | Score  | R Multiple | Exit Reason  |
|:-----------|:------|-------:|----------:|:-------------|
| BANANAS31USDT | S7    |  0.543 |     10.25 | TRAIL_STOP   |
| NEARUSDT   | S6    |  0.601 |      2.61 | TRAIL_STOP   |
| ZENUSDT    | S7    |  0.682 |      1.71 | TRAIL_STOP   |
| JOEUSDT    | S6    |  0.779 |      1.35 | TP1          |
| PROMUSDT   | S3    |  0.554 |      1.25 | TP1          |
| REDUSDT    | S2    |  0.547 |      1.17 | TP1          |
| GASUSDT    | S2    |  0.750 |      1.12 | TP1          |
| JSTUSDT    | S7    |  0.474 |      0.91 | TP1          |
| SUNUSDT    | S6    |  0.780 |      0.89 | TP1          |
| TURBOUSDT  | S6    |  0.774 |      0.86 | TP1          |

## G. Feature Coverage

Comparison of trace_json keys found vs what `FeatureExtractor` captures.

- **Traces with data:** 1087 / 1408
- **Total feature columns:** 131

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
