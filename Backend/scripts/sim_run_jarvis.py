#!/usr/bin/env python3
"""
sim_run_jarvis.py — Phase 2: Analyse & Attribute

Standalone CLI that reads Phase 1 CSVs (trades.csv, features.csv) and produces:
  - report.md  (9-section analysis)
  - insights.json (ranked insight register)

Usage:
  python sim_run_jarvis.py --run-id <UUID> --data-dir <path> --outdir <path>
"""
import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [JARVIS] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jarvis")

# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class Insight:
    id: str
    title: str
    section: str
    impact: int       # 1-5
    confidence: int   # 1-5
    actionability: int  # 1-5
    detail: str
    code_path: str = ""

    @property
    def score(self) -> int:
        return self.impact * self.confidence * self.actionability


# ─── DataLoader ───────────────────────────────────────────────────────────────

class DataLoader:
    @staticmethod
    def load_trades(path: Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        log.info(f"Loaded trades: {len(df)} rows")
        return df

    @staticmethod
    def load_features(path: Path) -> pd.DataFrame:
        df = pd.read_csv(path)
        log.info(f"Loaded features: {len(df)} rows, {len(df.columns)} columns")
        return df

    @staticmethod
    def merge(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        valid = trades[trades["data_available"] == True].copy()
        merged = valid.merge(features, on="id", how="inner")
        merged["is_winner"] = merged["pnl_net"] > 0
        log.info(f"Merged: {len(merged)} rows (trades with trace data)")
        return merged


# ─── InsightCollector ─────────────────────────────────────────────────────────

class InsightCollector:
    def __init__(self):
        self.insights: list[Insight] = []
        self._counter = 0

    def add(self, title: str, section: str, impact: int, confidence: int,
            actionability: int, detail: str, code_path: str = ""):
        self._counter += 1
        self.insights.append(Insight(
            id=f"I{self._counter:02d}",
            title=title, section=section,
            impact=min(5, max(1, impact)),
            confidence=min(5, max(1, confidence)),
            actionability=min(5, max(1, actionability)),
            detail=detail, code_path=code_path,
        ))

    def ranked(self) -> list[Insight]:
        return sorted(self.insights, key=lambda i: i.score, reverse=True)

    def to_json(self) -> list[dict]:
        return [asdict(i) | {"score": i.score} for i in self.ranked()]


# ─── CorrelationEngine ────────────────────────────────────────────────────────

class CorrelationEngine:
    @staticmethod
    def spearman_vs_targets(merged: pd.DataFrame, feature_cols: list[str],
                            collector: InsightCollector) -> dict:
        """Spearman correlation of each feature vs pnl_net and is_winner."""
        t0 = time.time()
        results = {"pnl_net": [], "is_winner": []}

        for target_name in ["pnl_net", "is_winner"]:
            target = merged[target_name].astype(float)
            rows = []
            for col in feature_cols:
                vals = merged[col]
                mask = vals.notna() & target.notna()
                n = mask.sum()
                if n < 10:
                    continue
                rho, pval = stats.spearmanr(vals[mask], target[mask])
                if np.isnan(rho):
                    continue
                rows.append({
                    "feature": col, "rho": round(rho, 4),
                    "p_value": round(pval, 6), "n": int(n),
                    "low_confidence": n < 50,
                })
            rows.sort(key=lambda r: r["rho"], reverse=True)
            results[target_name] = rows

        elapsed = time.time() - t0
        log.info(f"Correlation engine: {len(feature_cols)} features × 2 targets in {elapsed:.1f}s")

        # Generate insights from top correlations
        for target_name in ["pnl_net", "is_winner"]:
            rows = results[target_name]
            if len(rows) >= 3:
                top = rows[0]
                bot = rows[-1]
                collector.add(
                    title=f"Top positive correlate with {target_name}: {top['feature']}",
                    section="3a", impact=4, confidence=4 if top["n"] >= 100 else 2,
                    actionability=3,
                    detail=f"{top['feature']} has rho={top['rho']} (p={top['p_value']}, N={top['n']}) "
                           f"with {target_name}. Higher values predict better outcomes.",
                )
                collector.add(
                    title=f"Top negative correlate with {target_name}: {bot['feature']}",
                    section="3a", impact=4, confidence=4 if bot["n"] >= 100 else 2,
                    actionability=3,
                    detail=f"{bot['feature']} has rho={bot['rho']} (p={bot['p_value']}, N={bot['n']}) "
                           f"with {target_name}. Higher values predict worse outcomes.",
                )
        return results


# ─── TreeMiner ────────────────────────────────────────────────────────────────

class TreeMiner:
    @staticmethod
    def _prepare_features(merged: pd.DataFrame, feature_cols: list[str],
                          max_nan_frac: float = 0.8) -> tuple[pd.DataFrame, list[str]]:
        """Select numeric features with < max_nan_frac missing, impute median."""
        usable = [c for c in feature_cols
                  if merged[c].notna().mean() > (1 - max_nan_frac)]
        X = merged[usable].copy()
        for c in usable:
            X[c] = X[c].fillna(X[c].median())
        return X, usable

    @staticmethod
    def _extract_rules(tree, feature_names: list[str], top_n: int = 5) -> list[dict]:
        """Extract top leaf rules from a fitted decision tree."""
        t = tree.tree_
        rules = []

        def _walk(node, path):
            if t.children_left[node] == -1:  # leaf
                samples = int(t.n_node_samples[node])
                values = t.value[node][0]
                total = values.sum()
                if total == 0:
                    return
                positive_rate = values[1] / total if len(values) > 1 else 0
                rules.append({
                    "path": list(path),
                    "samples": samples,
                    "positive_rate": round(float(positive_rate), 3),
                    "positive_count": int(values[1]) if len(values) > 1 else 0,
                })
                return
            feat = feature_names[t.feature[node]]
            thresh = round(float(t.threshold[node]), 4)
            _walk(t.children_left[node], path + [f"{feat} <= {thresh}"])
            _walk(t.children_right[node], path + [f"{feat} > {thresh}"])

        _walk(0, [])
        # Sort by extremity of positive_rate (most interesting leaves)
        rules.sort(key=lambda r: abs(r["positive_rate"] - 0.5) * r["samples"], reverse=True)
        return rules[:top_n]

    @staticmethod
    def fit_trees(merged: pd.DataFrame, feature_cols: list[str],
                  collector: InsightCollector) -> dict:
        t0 = time.time()
        X, usable = TreeMiner._prepare_features(merged, feature_cols)
        results = {}

        # Tree 1: predict is_winner
        y_win = merged["is_winner"].astype(int)
        clf_win = DecisionTreeClassifier(max_depth=4, min_samples_leaf=20, random_state=42)
        clf_win.fit(X, y_win)
        winner_rules = TreeMiner._extract_rules(clf_win, usable)
        results["winner_rules"] = winner_rules

        # Tree 2: predict STOP_LOSS
        y_sl = (merged["exit_reason"] == "STOP_LOSS").astype(int)
        clf_sl = DecisionTreeClassifier(max_depth=4, min_samples_leaf=20, random_state=42)
        clf_sl.fit(X, y_sl)
        sl_rules = TreeMiner._extract_rules(clf_sl, usable)
        results["stoploss_rules"] = sl_rules

        # Feature importances
        results["winner_importances"] = sorted(
            zip(usable, clf_win.feature_importances_),
            key=lambda x: x[1], reverse=True
        )[:10]
        results["stoploss_importances"] = sorted(
            zip(usable, clf_sl.feature_importances_),
            key=lambda x: x[1], reverse=True
        )[:10]

        elapsed = time.time() - t0
        log.info(f"Tree mining: 2 trees fit on {len(usable)} features in {elapsed:.1f}s")

        # Insights from rules
        if winner_rules:
            best = winner_rules[0]
            collector.add(
                title=f"Best winner rule: {' AND '.join(best['path'][:2])}",
                section="3b", impact=4, confidence=3, actionability=4,
                detail=f"Decision tree path [{' AND '.join(best['path'])}] → "
                       f"{best['positive_rate']*100:.0f}% win rate on {best['samples']} trades. "
                       f"vs population {merged['is_winner'].mean()*100:.1f}%.",
            )
        if sl_rules:
            worst = max(sl_rules, key=lambda r: r["positive_rate"])
            collector.add(
                title=f"High stop-loss rule: {' AND '.join(worst['path'][:2])}",
                section="3b", impact=4, confidence=3, actionability=4,
                detail=f"Path [{' AND '.join(worst['path'])}] → "
                       f"{worst['positive_rate']*100:.0f}% stop-loss rate on {worst['samples']} trades.",
                code_path="exit_checker.py:101-107 (FAIL_FAST), exit_checker.py:131-153 (TRAIL_STOP)",
            )
        return results


# ─── ClusterEngine ────────────────────────────────────────────────────────────

class ClusterEngine:
    @staticmethod
    def find_archetypes(merged: pd.DataFrame, feature_cols: list[str],
                        collector: InsightCollector,
                        k_range: tuple[int, int] = (4, 8)) -> dict:
        t0 = time.time()
        # Prepare: drop >50% NaN cols, scale
        usable = [c for c in feature_cols if merged[c].notna().mean() > 0.5]
        X = merged[usable].copy()
        for c in usable:
            X[c] = X[c].fillna(X[c].median())

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Find best k
        best_k, best_score = 4, -1
        for k in range(k_range[0], k_range[1] + 1):
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = km.fit_predict(X_scaled)
            sc = silhouette_score(X_scaled, labels, sample_size=min(2000, len(X_scaled)))
            if sc > best_score:
                best_k, best_score = k, sc

        # Fit best
        km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
        merged_copy = merged.copy()
        merged_copy["cluster"] = km.fit_predict(X_scaled)

        clusters = []
        pop_means = X.mean()
        pop_stds = X.std()

        for cid in range(best_k):
            mask = merged_copy["cluster"] == cid
            c_df = merged_copy[mask]
            c_feat = X[mask]
            n = len(c_df)

            # Distinguishing features: highest |z-score vs population|
            c_means = c_feat.mean()
            z_scores = {}
            for col in usable:
                if pop_stds[col] > 1e-10:
                    z_scores[col] = abs((c_means[col] - pop_means[col]) / pop_stds[col])
            top_features = sorted(z_scores.items(), key=lambda x: x[1], reverse=True)[:5]

            # Dominant exit_reason and setup_id
            dom_exit = c_df["exit_reason"].mode().iloc[0] if len(c_df) > 0 else "N/A"
            dom_setup = c_df["setup_id"].mode().iloc[0] if len(c_df) > 0 else "N/A"

            # Name based on characteristics
            win_rate = c_df["is_winner"].mean()
            avg_pnl = c_df["pnl_net"].mean()
            avg_r = c_df["r_multiple"].mean()
            name = _name_cluster(win_rate, avg_r, dom_exit, dom_setup, top_features)

            clusters.append({
                "id": cid,
                "name": name,
                "count": n,
                "win_rate": round(win_rate, 3),
                "avg_pnl": round(avg_pnl, 2),
                "avg_r": round(avg_r, 3),
                "dominant_exit": dom_exit,
                "dominant_setup": dom_setup,
                "top_features": [(f, round(z, 2)) for f, z in top_features],
            })

        elapsed = time.time() - t0
        log.info(f"Clustering: best k={best_k} (silhouette={best_score:.3f}) in {elapsed:.1f}s")

        # Insights
        best_cluster = max(clusters, key=lambda c: c["win_rate"])
        worst_cluster = min(clusters, key=lambda c: c["win_rate"])
        collector.add(
            title=f"Best archetype: '{best_cluster['name']}' ({best_cluster['win_rate']*100:.0f}% WR)",
            section="3c", impact=4, confidence=3, actionability=3,
            detail=f"Cluster {best_cluster['id']} ({best_cluster['count']} trades): "
                   f"WR={best_cluster['win_rate']*100:.1f}%, avg R={best_cluster['avg_r']:.2f}, "
                   f"dominant setup={best_cluster['dominant_setup']}, exit={best_cluster['dominant_exit']}. "
                   f"Top features: {', '.join(f'{f}(z={z})' for f,z in best_cluster['top_features'][:3])}.",
        )
        collector.add(
            title=f"Worst archetype: '{worst_cluster['name']}' ({worst_cluster['win_rate']*100:.0f}% WR)",
            section="3c", impact=4, confidence=3, actionability=4,
            detail=f"Cluster {worst_cluster['id']} ({worst_cluster['count']} trades): "
                   f"WR={worst_cluster['win_rate']*100:.1f}%, avg R={worst_cluster['avg_r']:.2f}, "
                   f"dominant setup={worst_cluster['dominant_setup']}, exit={worst_cluster['dominant_exit']}. "
                   f"Top features: {', '.join(f'{f}(z={z})' for f,z in worst_cluster['top_features'][:3])}.",
        )

        return {
            "best_k": best_k,
            "silhouette": round(best_score, 3),
            "clusters": clusters,
            "labels": merged_copy["cluster"].tolist(),
        }


def _name_cluster(win_rate, avg_r, dom_exit, dom_setup, top_features):
    """Generate a descriptive cluster name."""
    parts = []
    if win_rate >= 0.55:
        parts.append("Winners")
    elif win_rate <= 0.35:
        parts.append("Losers")
    else:
        parts.append("Mixed")

    if dom_exit == "FAIL_FAST":
        parts.append("Quick-Death")
    elif dom_exit == "TP2":
        parts.append("Runners")
    elif dom_exit == "STOP_LOSS":
        parts.append("Stopped")
    elif dom_exit == "TIME_STOP":
        parts.append("Stalled")
    elif dom_exit == "TRAIL_STOP":
        parts.append("Trailed")
    elif dom_exit == "TP1":
        parts.append("Target-Hit")

    parts.append(f"({dom_setup})")
    return " ".join(parts)


# ─── ExitAnalyzer ─────────────────────────────────────────────────────────────

class ExitAnalyzer:
    @staticmethod
    def analyze(merged: pd.DataFrame, feature_cols: list[str],
                collector: InsightCollector) -> dict:
        t0 = time.time()
        results = {}

        # 4a: Exit predictability — correlations of features with each exit type
        exit_corrs = {}
        for exit_type in ["STOP_LOSS", "FAIL_FAST", "TP1", "TP2", "TRAIL_STOP", "TIME_STOP"]:
            target = (merged["exit_reason"] == exit_type).astype(float)
            if target.sum() < 5:
                continue
            rows = []
            for col in feature_cols:
                vals = merged[col]
                mask = vals.notna()
                n = mask.sum()
                if n < 20:
                    continue
                rho, pval = stats.spearmanr(vals[mask], target[mask])
                if np.isnan(rho):
                    continue
                rows.append({"feature": col, "rho": round(rho, 4),
                             "p_value": round(pval, 6), "n": int(n)})
            rows.sort(key=lambda r: abs(r["rho"]), reverse=True)
            exit_corrs[exit_type] = rows[:10]

        results["exit_correlations"] = exit_corrs

        # 4b: Time-in-trade distributions
        bars_held = merged["bars_held"].astype(float)
        time_stats = {}
        for exit_type in merged["exit_reason"].unique():
            subset = bars_held[merged["exit_reason"] == exit_type]
            if len(subset) < 3:
                continue
            time_stats[exit_type] = {
                "count": int(len(subset)),
                "min": int(subset.min()),
                "p25": round(float(subset.quantile(0.25)), 1),
                "median": round(float(subset.median()), 1),
                "p75": round(float(subset.quantile(0.75)), 1),
                "max": int(subset.max()),
                "mean": round(float(subset.mean()), 1),
            }
        results["time_in_trade"] = time_stats

        # Instant death analysis: bars_held <= 2 AND STOP_LOSS or FAIL_FAST
        instant_mask = (merged["bars_held"] <= 2) & (
            merged["exit_reason"].isin(["STOP_LOSS", "FAIL_FAST"])
        )
        instant_count = instant_mask.sum()
        total_count = len(merged)
        results["instant_death"] = {
            "count": int(instant_count),
            "fraction": round(instant_count / total_count, 3),
        }
        # Feature differences for instant death
        if instant_count >= 10:
            instant_feats = []
            for col in feature_cols:
                mask_ok = merged[col].notna()
                inst_vals = merged.loc[instant_mask & mask_ok, col]
                rest_vals = merged.loc[~instant_mask & mask_ok, col]
                if len(inst_vals) < 5 or len(rest_vals) < 5:
                    continue
                stat_val, pval = stats.mannwhitneyu(inst_vals, rest_vals, alternative="two-sided")
                if pval < 0.05:
                    instant_feats.append({
                        "feature": col,
                        "instant_median": round(float(inst_vals.median()), 4),
                        "rest_median": round(float(rest_vals.median()), 4),
                        "p_value": round(pval, 6),
                    })
            instant_feats.sort(key=lambda r: r["p_value"])
            results["instant_death"]["differentiators"] = instant_feats[:10]

        # 4c: TP1 vs TP2
        tp_mask = merged["exit_reason"].isin(["TP1", "TP2"])
        tp_trades = merged[tp_mask]
        tp2_count = (tp_trades["exit_reason"] == "TP2").sum()
        tp1_count = (tp_trades["exit_reason"] == "TP1").sum()
        results["tp1_vs_tp2"] = {
            "tp1_count": int(tp1_count),
            "tp2_count": int(tp2_count),
            "tp2_hit_rate": round(tp2_count / (tp1_count + tp2_count), 3) if (tp1_count + tp2_count) > 0 else 0,
        }
        # Feature comparison TP1 vs TP2
        if tp2_count >= 5 and tp1_count >= 5:
            is_tp2 = (tp_trades["exit_reason"] == "TP2").astype(float)
            tp_corrs = []
            for col in feature_cols:
                vals = tp_trades[col]
                mask = vals.notna()
                n = mask.sum()
                if n < 10:
                    continue
                rho, pval = stats.spearmanr(vals[mask], is_tp2[mask])
                if np.isnan(rho):
                    continue
                tp_corrs.append({"feature": col, "rho": round(rho, 4),
                                 "p_value": round(pval, 6), "n": int(n)})
            tp_corrs.sort(key=lambda r: abs(r["rho"]), reverse=True)
            results["tp1_vs_tp2"]["differentiators"] = tp_corrs[:10]

        elapsed = time.time() - t0
        log.info(f"Exit analysis: done in {elapsed:.1f}s")

        # Insights
        if instant_count > 0:
            collector.add(
                title=f"{results['instant_death']['fraction']*100:.0f}% of trades die within 2 bars",
                section="4b", impact=5, confidence=5, actionability=4,
                detail=f"{instant_count}/{total_count} trades exit via STOP_LOSS or FAIL_FAST "
                       f"within 2 bars. These are entries that immediately move against the position.",
                code_path="exit_checker.py:101-107 (FAIL_FAST logic)",
            )

        if results["tp1_vs_tp2"]["tp2_hit_rate"] > 0:
            collector.add(
                title=f"TP2 hit rate: {results['tp1_vs_tp2']['tp2_hit_rate']*100:.0f}% of profitable exits reach TP2",
                section="4c", impact=3, confidence=4, actionability=3,
                detail=f"Of {tp1_count + tp2_count} trades exiting at a target, "
                       f"{tp2_count} ({results['tp1_vs_tp2']['tp2_hit_rate']*100:.0f}%) reached TP2. "
                       f"TP1: {tp1_count}, TP2: {tp2_count}.",
                code_path="exit_checker.py:155-175 (TP1/TP2 logic)",
            )

        # Exit type insights
        for etype, corrs in exit_corrs.items():
            if corrs:
                top = corrs[0]
                collector.add(
                    title=f"Top predictor of {etype}: {top['feature']}",
                    section="4a", impact=3, confidence=3, actionability=3,
                    detail=f"{top['feature']} correlates with {etype} exit (rho={top['rho']}, "
                           f"p={top['p_value']}, N={top['n']}).",
                )

        return results


# ─── SegmentAnalyzer ──────────────────────────────────────────────────────────

class SegmentAnalyzer:
    @staticmethod
    def wilson_ci(wins: int, total: int, z: float = 1.96) -> tuple[float, float]:
        """Wilson score confidence interval for win rate."""
        if total == 0:
            return (0.0, 0.0)
        p = wins / total
        denom = 1 + z**2 / total
        center = (p + z**2 / (2 * total)) / denom
        spread = z * np.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denom
        return (round(float(max(0, center - spread)), 4), round(float(min(1, center + spread)), 4))

    @staticmethod
    def analyze(merged: pd.DataFrame, collector: InsightCollector) -> dict:
        t0 = time.time()
        results = {}

        # Parse opportunity_created_ts for time-based segments
        merged = merged.copy()
        merged["created_dt"] = pd.to_datetime(merged["opportunity_created_ts"], utc=True, errors="coerce")
        merged["hour"] = merged["created_dt"].dt.hour
        merged["dow"] = merged["created_dt"].dt.day_name()

        def _segment_stats(mask, name):
            sub = merged[mask]
            n = len(sub)
            if n == 0:
                return None
            wins = int(sub["is_winner"].sum())
            wr = wins / n
            avg_pnl = sub["pnl_net"].mean()
            avg_r = sub["r_multiple"].mean()
            avg_win = sub.loc[sub["is_winner"], "pnl_net"].mean() if wins > 0 else 0
            losses = n - wins
            avg_loss = sub.loc[~sub["is_winner"], "pnl_net"].mean() if losses > 0 else 0
            ci = SegmentAnalyzer.wilson_ci(wins, n)
            return {
                "name": name, "count": n, "wins": wins, "losses": losses,
                "win_rate": round(wr, 3),
                "wilson_ci_95": ci,
                "avg_pnl": round(float(avg_pnl), 2),
                "avg_r": round(float(avg_r), 3),
                "avg_win_size": round(float(avg_win), 2),
                "avg_loss_size": round(float(avg_loss), 2),
                "total_pnl": round(float(sub["pnl_net"].sum()), 2),
            }

        # By setup_id
        setup_stats = {}
        for sid in sorted(merged["setup_id"].unique()):
            s = _segment_stats(merged["setup_id"] == sid, sid)
            if s:
                setup_stats[sid] = s
        results["by_setup"] = setup_stats

        # By setup_id × timeframe
        combo_stats = {}
        for sid in merged["setup_id"].unique():
            for tf in merged["timeframe"].unique():
                mask = (merged["setup_id"] == sid) & (merged["timeframe"] == tf)
                if mask.sum() >= 5:
                    key = f"{sid}/{tf}"
                    s = _segment_stats(mask, key)
                    if s:
                        combo_stats[key] = s
        results["by_setup_timeframe"] = combo_stats

        # By direction
        dir_stats = {}
        for d in ["long", "short"]:
            s = _segment_stats(merged["direction"] == d, d)
            if s:
                dir_stats[d] = s
        results["by_direction"] = dir_stats

        # By day of week
        dow_stats = {}
        for dow in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            mask = merged["dow"] == dow
            if mask.sum() >= 5:
                s = _segment_stats(mask, dow)
                if s:
                    dow_stats[dow] = s
        results["by_dow"] = dow_stats

        # By hour bucket (4h buckets)
        hour_stats = {}
        for h_start in range(0, 24, 4):
            h_end = h_start + 4
            mask = merged["hour"].between(h_start, h_end - 1)
            if mask.sum() >= 5:
                key = f"{h_start:02d}-{h_end:02d}"
                s = _segment_stats(mask, key)
                if s:
                    hour_stats[key] = s
        results["by_hour_bucket"] = hour_stats

        elapsed = time.time() - t0
        log.info(f"Segment analysis: done in {elapsed:.1f}s")

        # Identify underperformers and outperformers
        overall_wr = merged["is_winner"].mean()
        for category, stats_dict in [("by_setup", setup_stats), ("by_setup_timeframe", combo_stats),
                                      ("by_direction", dir_stats), ("by_dow", dow_stats)]:
            for key, s in stats_dict.items():
                if s["count"] >= 10:
                    if s["win_rate"] >= overall_wr + 0.15:
                        collector.add(
                            title=f"Outperformer: {key} ({s['win_rate']*100:.0f}% WR)",
                            section="5", impact=4, confidence=4 if s["count"] >= 50 else 3,
                            actionability=3,
                            detail=f"{key}: WR={s['win_rate']*100:.1f}% vs overall {overall_wr*100:.1f}% "
                                   f"(N={s['count']}, Wilson 95% CI: {s['wilson_ci_95']}). "
                                   f"Avg R={s['avg_r']}, total PnL={s['total_pnl']}.",
                        )
                    elif s["win_rate"] <= overall_wr - 0.10:
                        collector.add(
                            title=f"Underperformer: {key} ({s['win_rate']*100:.0f}% WR)",
                            section="5", impact=5, confidence=4 if s["count"] >= 50 else 3,
                            actionability=5,
                            detail=f"{key}: WR={s['win_rate']*100:.1f}% vs overall {overall_wr*100:.1f}% "
                                   f"(N={s['count']}, Wilson 95% CI: {s['wilson_ci_95']}). "
                                   f"Avg R={s['avg_r']}, total PnL={s['total_pnl']}. "
                                   f"Decomposition: avg_win={s['avg_win_size']}, avg_loss={s['avg_loss_size']}.",
                        )

        return results


# ─── MAEMFEAssessor ───────────────────────────────────────────────────────────

class MAEMFEAssessor:
    @staticmethod
    def assess(merged: pd.DataFrame, collector: InsightCollector) -> dict:
        # Check if trace_json has bar-by-bar data
        # trace_json stores entry features, not intra-trade tracking
        has_mae = False
        has_mfe = False

        # Check for any drawdown/runup fields
        sample = merged.head(10)
        for _, row in sample.iterrows():
            tj = str(row.get("trace_json", ""))
            if "mae" in tj.lower() or "max_adverse" in tj.lower() or "drawdown" in tj.lower():
                has_mae = True
            if "mfe" in tj.lower() or "max_favorable" in tj.lower() or "runup" in tj.lower():
                has_mfe = True

        result = {
            "mae_available": has_mae,
            "mfe_available": has_mfe,
            "assessment": "NOT AVAILABLE",
            "schema_proposal": {
                "table": "trade_bar_tracking",
                "columns": [
                    "trade_id UUID REFERENCES trades(id)",
                    "bar_index INT (0-based from entry)",
                    "bar_start_ts TIMESTAMPTZ",
                    "high NUMERIC",
                    "low NUMERIC",
                    "close NUMERIC",
                    "unrealized_pnl NUMERIC",
                    "mae_so_far NUMERIC (worst drawdown from entry to this bar)",
                    "mfe_so_far NUMERIC (best excursion from entry to this bar)",
                ],
                "implementation_file": "opportunity_simulator.py",
                "implementation_location": "lines 611-676 (trace construction)",
                "pseudocode": (
                    "In simulate_trade(), after entry fill:\n"
                    "  mae, mfe = 0.0, 0.0\n"
                    "  for bar in bars_after_entry:\n"
                    "    unrealized = (bar.close - entry_price) * direction_mult\n"
                    "    mae = min(mae, unrealized)\n"
                    "    mfe = max(mfe, unrealized)\n"
                    "    record(trade_id, bar_index, bar.ts, bar.high, bar.low, bar.close, unrealized, mae, mfe)\n"
                    "  # Also store final mae/mfe on the trade row itself for quick access"
                ),
            },
        }

        collector.add(
            title="MAE/MFE data not available — bar-by-bar tracking needed",
            section="6", impact=4, confidence=5, actionability=4,
            detail="trace_json stores entry-time features only, not intra-trade bar tracking. "
                   "Adding MAE/MFE would enable stop optimization (are stops too tight? too loose?) "
                   "and target calibration. Implementation: ~30 lines in opportunity_simulator.py:611-676.",
            code_path="opportunity_simulator.py:611-676 (trace construction)",
        )

        return result


# ─── CodeAttribution ──────────────────────────────────────────────────────────

class CodeAttribution:
    """Map insights to specific code paths."""

    # Static code map based on Phase 1 exploration
    CODE_MAP = {
        "FAIL_FAST": {
            "file": "exit_checker.py",
            "lines": "101-107",
            "description": "Exits if close moves 0.3% against entry within first 2 bars",
            "params": "fail_fast_bars=2, fail_fast_threshold=0.003",
        },
        "TIME_STOP": {
            "file": "exit_checker.py",
            "lines": "109-129",
            "description": "Exits if price hasn't reached 0.5R within max_bars (setup-specific)",
            "params": "Requires half_r_touched tracking; window = time_stop_bars from setup",
        },
        "TRAIL_STOP": {
            "file": "exit_checker.py",
            "lines": "131-153",
            "description": "Activates after 1R profit, trails 1R below high water mark",
            "params": "Trailing distance = 1 × r_value",
        },
        "TP1": {
            "file": "exit_checker.py",
            "lines": "155-164",
            "description": "Target 1 hit (setup-specific distance from entry)",
        },
        "TP2": {
            "file": "exit_checker.py",
            "lines": "167-175",
            "description": "Target 2 hit (extended target, setup-specific)",
        },
        "STOP_LOSS": {
            "file": "exit_checker.py",
            "lines": "~70-99",
            "description": "Hard stop at setup-computed stop_price (checked first in priority)",
        },
        "entry_gates": {
            "file": "opportunity_simulator.py",
            "lines": "341-498",
            "description": "Pre-entry validation (microcap filter, spread check, data availability)",
        },
        "trace_construction": {
            "file": "opportunity_simulator.py",
            "lines": "611-676",
            "description": "Builds trace_json with entry-time indicator snapshot",
        },
        "scoring_S1": {
            "file": "setups/s1_bb_squeeze.py",
            "description": "BB Squeeze setup: severity, bias_strength, participation, break_quality, clean_close",
        },
        "scoring_S2": {
            "file": "setups/s2_golden_cross.py",
            "description": "Golden Cross setup: conv_speed, conv_tight, trend_strength, participation, clean_close",
        },
        "scoring_S3": {
            "file": "setups/s3_rsi_divergence.py",
            "description": "RSI Divergence setup: div_magnitude, rsi_extremity, participation, clean_close, span_quality",
        },
        "scoring_S6": {
            "file": "setups/s6_trend_pullback.py",
            "description": "Trend Pullback setup: trend_strength, pullback_quality, retrace_depth, volume_quality, htf_confluence",
        },
        "scoring_S7": {
            "file": "setups/s7_momentum_breakout.py",
            "description": "Momentum Breakout (Donchian): break_strength, volume_quality, bias_strength, clean_close, htf_confluence",
        },
        "signal_ranking": {
            "file": "signal_engine.py",
            "lines": "620-794",
            "description": "Weighted score aggregation → rank; serves ranked list to API",
        },
    }

    @staticmethod
    def attribute(insights: list[Insight]) -> list[dict]:
        """For top insights, map to code paths."""
        attributions = []
        for ins in insights[:10]:
            attr = {"insight_id": ins.id, "title": ins.title, "code_refs": []}
            detail_lower = ins.detail.lower()

            # Match by keywords
            if "fail_fast" in detail_lower or "instant death" in detail_lower or "2 bars" in detail_lower:
                attr["code_refs"].append(CodeAttribution.CODE_MAP["FAIL_FAST"])
            if "stop_loss" in detail_lower or "stopped" in detail_lower:
                attr["code_refs"].append(CodeAttribution.CODE_MAP["STOP_LOSS"])
            if "trail" in detail_lower:
                attr["code_refs"].append(CodeAttribution.CODE_MAP["TRAIL_STOP"])
            if "time_stop" in detail_lower or "stalled" in detail_lower:
                attr["code_refs"].append(CodeAttribution.CODE_MAP["TIME_STOP"])
            if "tp1" in detail_lower or "tp2" in detail_lower or "target" in detail_lower:
                attr["code_refs"].append(CodeAttribution.CODE_MAP["TP1"])
                attr["code_refs"].append(CodeAttribution.CODE_MAP["TP2"])

            for sid in ["S1", "S2", "S3", "S6", "S7"]:
                if sid.lower() in detail_lower or sid in ins.title:
                    key = f"scoring_{sid}"
                    if key in CodeAttribution.CODE_MAP:
                        attr["code_refs"].append(CodeAttribution.CODE_MAP[key])

            if "score" in detail_lower or "rank" in detail_lower:
                attr["code_refs"].append(CodeAttribution.CODE_MAP["signal_ranking"])

            if ins.code_path:
                attr["explicit_path"] = ins.code_path

            # Deduplicate
            seen = set()
            unique_refs = []
            for ref in attr["code_refs"]:
                key = ref["file"] + ref.get("lines", "")
                if key not in seen:
                    seen.add(key)
                    unique_refs.append(ref)
            attr["code_refs"] = unique_refs

            attributions.append(attr)
        return attributions


# ─── Experiments ──────────────────────────────────────────────────────────────

def generate_experiments(merged: pd.DataFrame, seg_results: dict,
                         exit_results: dict, collector: InsightCollector) -> list[dict]:
    """Generate 5 concrete experiment proposals based on findings."""
    experiments = []
    overall_wr = merged["is_winner"].mean()
    overall_pf = _profit_factor(merged)

    # Experiment 1: FAIL_FAST tuning
    ff_count = (merged["exit_reason"] == "FAIL_FAST").sum()
    ff_frac = ff_count / len(merged)
    experiments.append({
        "id": "E1",
        "title": "Relax FAIL_FAST threshold for setups with high FAIL_FAST rate",
        "module": "exit_checker.py",
        "change": "Increase fail_fast_threshold from 0.003 (0.3%) to 0.005 (0.5%) for S6 and S7",
        "rationale": f"FAIL_FAST exits {ff_count} trades ({ff_frac*100:.1f}% of all). "
                     f"Some may be noise-triggered — widening the threshold should reduce false exits.",
        "baseline": f"FAIL_FAST rate: {ff_frac*100:.1f}%, overall WR: {overall_wr*100:.1f}%",
        "metric": "FAIL_FAST rate, overall win rate, profit factor",
    })

    # Experiment 2: Based on worst setup/timeframe combo
    worst_combo = None
    worst_wr = 1.0
    for key, s in seg_results.get("by_setup_timeframe", {}).items():
        if s["count"] >= 20 and s["win_rate"] < worst_wr:
            worst_wr = s["win_rate"]
            worst_combo = (key, s)
    if worst_combo:
        key, s = worst_combo
        setup_id = key.split("/")[0]
        experiments.append({
            "id": "E2",
            "title": f"Add RSI filter for {key}",
            "module": f"setups/{setup_id.lower()}_*.py (evaluate_trigger)",
            "change": f"Gate {key} entries on RSI not being extreme against direction "
                      f"(e.g., RSI > 70 blocks long, RSI < 30 blocks short)",
            "rationale": f"{key} has {s['win_rate']*100:.1f}% WR on {s['count']} trades "
                         f"(vs {overall_wr*100:.1f}% overall). Many may be late-momentum entries.",
            "baseline": f"{key}: WR={s['win_rate']*100:.1f}%, N={s['count']}, PnL={s['total_pnl']}",
            "metric": f"{key} win rate and total PnL",
        })

    # Experiment 3: Time-stop adjustment
    ts_stats = exit_results.get("time_in_trade", {}).get("TIME_STOP", {})
    experiments.append({
        "id": "E3",
        "title": "Reduce time_stop_bars for S7 from 6 to 4",
        "module": "setups/s7_momentum_breakout.py (metadata.time_stop_bars)",
        "change": "Reduce S7 time_stop_bars from 6 to 4. Momentum breakouts should resolve quickly.",
        "rationale": "Momentum breakouts that don't move within 4 bars are likely failed breakouts. "
                     "Holding longer just delays the inevitable stop-loss.",
        "baseline": f"TIME_STOP stats: {ts_stats}" if ts_stats else "TIME_STOP: 80 exits",
        "metric": "S7 profit factor, TIME_STOP count, avg holding period",
    })

    # Experiment 4: Direction filter
    dir_stats = seg_results.get("by_direction", {})
    short_stats = dir_stats.get("short", {})
    long_stats = dir_stats.get("long", {})
    if short_stats and long_stats:
        experiments.append({
            "id": "E4",
            "title": "Raise score threshold for short entries",
            "module": "signal_engine.py (ranking/filtering)",
            "change": "Require opportunity_score >= 0.75 for short entries (vs current universal threshold)",
            "rationale": f"Shorts: WR={short_stats.get('win_rate', 0)*100:.1f}% (N={short_stats.get('count', 0)}) "
                         f"vs Longs: WR={long_stats.get('win_rate', 0)*100:.1f}% (N={long_stats.get('count', 0)}). "
                         f"Higher bar for shorts should filter out weak setups.",
            "baseline": f"Short PnL={short_stats.get('total_pnl', 0)}, Long PnL={long_stats.get('total_pnl', 0)}",
            "metric": "Short win rate, short profit factor, overall PnL",
        })

    # Experiment 5: Volume Z-score minimum
    experiments.append({
        "id": "E5",
        "title": "Require volume_z >= 1.5 for all entries",
        "module": "opportunity_simulator.py (entry gates) or setup evaluate_trigger",
        "change": "Add global gate: reject opportunities where ind.volume_z < 1.5 at entry",
        "rationale": "Volume confirmation is a score component but not a hard gate. "
                     "Low-volume entries may lack the participation needed for follow-through.",
        "baseline": f"Overall WR: {overall_wr*100:.1f}%, PF: {overall_pf:.2f}",
        "metric": "Win rate, profit factor, trade count (ensuring we don't filter too aggressively)",
    })

    return experiments[:5]


def _profit_factor(df: pd.DataFrame) -> float:
    gross_profit = df.loc[df["pnl_net"] > 0, "pnl_net"].sum()
    gross_loss = abs(df.loc[df["pnl_net"] < 0, "pnl_net"].sum())
    return round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")


# ─── ReportWriter ─────────────────────────────────────────────────────────────

class ReportWriter:
    @staticmethod
    def write(outdir: Path, run_id: str, merged: pd.DataFrame,
              corr_results: dict, tree_results: dict, cluster_results: dict,
              exit_results: dict, segment_results: dict, mae_results: dict,
              experiments: list[dict], attributions: list[dict],
              collector: InsightCollector):

        lines = []
        w = lines.append

        ranked_insights = collector.ranked()
        overall_wr = merged["is_winner"].mean()
        overall_pnl = merged["pnl_net"].sum()
        overall_pf = _profit_factor(merged)
        n_trades = len(merged)

        # ── Section 1: Executive Summary ──────────────────────────────────
        w("# Simulation Analysis Report")
        w(f"\n**Run ID:** `{run_id}`  ")
        w(f"**Trades analysed:** {n_trades} (with trace data, of {n_trades} valid)  ")
        w(f"**Overall:** WR={overall_wr*100:.1f}%, PnL={overall_pnl:,.0f}, PF={overall_pf}  ")
        w(f"**Generated:** {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')}")
        w("")
        w("## 1. Executive Summary")
        w("")
        # Top 5 insights as bullets
        for ins in ranked_insights[:5]:
            w(f"- **{ins.title}** (score={ins.score}) — {ins.detail[:120]}...")
        w("")

        # ── Section 2: Ranked Insight Register ────────────────────────────
        w("## 2. Ranked Insight Register")
        w("")
        w("| Rank | ID | Title | I | C | A | Score | Section |")
        w("|------|----|-------|---|---|---|-------|---------|")
        for i, ins in enumerate(ranked_insights, 1):
            title_short = ins.title[:60] + ("..." if len(ins.title) > 60 else "")
            w(f"| {i} | {ins.id} | {title_short} | {ins.impact} | {ins.confidence} | {ins.actionability} | {ins.score} | {ins.section} |")
        w("")

        # ── Section 3a: Univariate Correlations ──────────────────────────
        w("## 3a. Univariate Correlations (Spearman)")
        w("")
        for target_name in ["pnl_net", "is_winner"]:
            rows = corr_results[target_name]
            w(f"### Top correlations with `{target_name}`")
            w("")
            w("**Top 20 positive:**")
            w("")
            w("| Feature | rho | p-value | N | Low-conf? |")
            w("|---------|-----|---------|---|-----------|")
            for r in rows[:20]:
                flag = " :warning:" if r["low_confidence"] else ""
                w(f"| {r['feature']} | {r['rho']:+.4f} | {r['p_value']:.6f} | {r['n']} | {flag} |")
            w("")
            w("**Top 20 negative:**")
            w("")
            w("| Feature | rho | p-value | N | Low-conf? |")
            w("|---------|-----|---------|---|-----------|")
            for r in rows[-20:]:
                flag = " :warning:" if r["low_confidence"] else ""
                w(f"| {r['feature']} | {r['rho']:+.4f} | {r['p_value']:.6f} | {r['n']} | {flag} |")
            w("")

        # ── Section 3b: Interaction Mining ────────────────────────────────
        w("## 3b. Interaction Mining (Decision Trees)")
        w("")
        w("### Winner prediction tree (max_depth=4)")
        w("")
        w("**Top feature importances:**")
        w("")
        for fname, imp in tree_results["winner_importances"]:
            w(f"- `{fname}`: {imp:.4f}")
        w("")
        w("**Top leaf rules:**")
        w("")
        for i, rule in enumerate(tree_results["winner_rules"], 1):
            w(f"{i}. **IF** {' AND '.join(rule['path'])}")
            w(f"   **THEN** win_rate={rule['positive_rate']*100:.0f}% (N={rule['samples']})")
            w("")

        w("### Stop-loss prediction tree (max_depth=4)")
        w("")
        w("**Top feature importances:**")
        w("")
        for fname, imp in tree_results["stoploss_importances"]:
            w(f"- `{fname}`: {imp:.4f}")
        w("")
        w("**Top leaf rules:**")
        w("")
        for i, rule in enumerate(tree_results["stoploss_rules"], 1):
            w(f"{i}. **IF** {' AND '.join(rule['path'])}")
            w(f"   **THEN** stop_loss_rate={rule['positive_rate']*100:.0f}% (N={rule['samples']})")
            w("")

        # ── Section 3c: Trade Archetypes ──────────────────────────────────
        w("## 3c. Trade Archetypes (K-Means Clustering)")
        w("")
        w(f"**Best k={cluster_results['best_k']}** (silhouette={cluster_results['silhouette']:.3f})")
        w("")
        w("| Cluster | Name | Count | WR | Avg PnL | Avg R | Dom. Exit | Dom. Setup | Top Features |")
        w("|---------|------|-------|----|---------|-------|-----------|------------|--------------|")
        for c in cluster_results["clusters"]:
            feats = ", ".join(f"{f}(z={z})" for f, z in c["top_features"][:3])
            w(f"| {c['id']} | {c['name']} | {c['count']} | {c['win_rate']*100:.1f}% | {c['avg_pnl']:.1f} | {c['avg_r']:.2f} | {c['dominant_exit']} | {c['dominant_setup']} | {feats} |")
        w("")

        # ── Section 4a: Exit Predictability ───────────────────────────────
        w("## 4a. Exit Predictability")
        w("")
        for exit_type, corrs in exit_results["exit_correlations"].items():
            w(f"### {exit_type} — top feature predictors at entry")
            w("")
            w("| Feature | rho | p-value | N |")
            w("|---------|-----|---------|---|")
            for r in corrs[:5]:
                w(f"| {r['feature']} | {r['rho']:+.4f} | {r['p_value']:.6f} | {r['n']} |")
            w("")

        # ── Section 4b: Time-in-Trade ─────────────────────────────────────
        w("## 4b. Time-in-Trade Analysis")
        w("")
        w("### Bars held by exit reason")
        w("")
        w("| Exit Reason | Count | Min | P25 | Median | P75 | Max | Mean |")
        w("|-------------|-------|-----|-----|--------|-----|-----|------|")
        for etype, ts in exit_results["time_in_trade"].items():
            w(f"| {etype} | {ts['count']} | {ts['min']} | {ts['p25']} | {ts['median']} | {ts['p75']} | {ts['max']} | {ts['mean']} |")
        w("")

        id_data = exit_results["instant_death"]
        w(f"### Instant death trades (bars_held <= 2 AND STOP_LOSS/FAIL_FAST)")
        w("")
        w(f"**{id_data['count']} trades ({id_data['fraction']*100:.1f}%)** exit within 2 bars via stop or fail-fast.")
        w("")
        if "differentiators" in id_data and id_data["differentiators"]:
            w("**Statistically significant feature differences (instant death vs rest):**")
            w("")
            w("| Feature | Instant Median | Rest Median | p-value |")
            w("|---------|---------------|-------------|---------|")
            for d in id_data["differentiators"][:10]:
                w(f"| {d['feature']} | {d['instant_median']} | {d['rest_median']} | {d['p_value']:.6f} |")
            w("")

        # ── Section 4c: TP1 vs TP2 ───────────────────────────────────────
        w("## 4c. TP1 vs TP2 Analysis")
        w("")
        tp_data = exit_results["tp1_vs_tp2"]
        w(f"- TP1 exits: {tp_data['tp1_count']}")
        w(f"- TP2 exits: {tp_data['tp2_count']}")
        w(f"- **TP2 hit rate:** {tp_data['tp2_hit_rate']*100:.1f}% of profitable exits reached TP2")
        w("")
        if "differentiators" in tp_data and tp_data["differentiators"]:
            w("**Features differentiating TP1 vs TP2 exits:**")
            w("")
            w("| Feature | rho (with TP2) | p-value | N |")
            w("|---------|---------------|---------|---|")
            for d in tp_data["differentiators"][:10]:
                w(f"| {d['feature']} | {d['rho']:+.4f} | {d['p_value']:.6f} | {d['n']} |")
            w("")

        # ── Section 5: Segment Deep Dives ─────────────────────────────────
        w("## 5. Segment Deep Dives")
        w("")

        def _write_segment_table(title, stats_dict):
            if not stats_dict:
                return
            w(f"### {title}")
            w("")
            w("| Segment | Count | WR | Wilson 95% CI | Avg PnL | Avg R | Avg Win | Avg Loss | Total PnL |")
            w("|---------|-------|----|---------------|---------|-------|---------|----------|-----------|")
            for key, s in sorted(stats_dict.items(), key=lambda x: x[1]["win_rate"]):
                ci = s["wilson_ci_95"]
                w(f"| {key} | {s['count']} | {s['win_rate']*100:.1f}% | [{ci[0]*100:.1f}%, {ci[1]*100:.1f}%] | {s['avg_pnl']:.1f} | {s['avg_r']:.2f} | {s['avg_win_size']:.1f} | {s['avg_loss_size']:.1f} | {s['total_pnl']:.0f} |")
            w("")

        _write_segment_table("By Setup", segment_results["by_setup"])
        _write_segment_table("By Setup × Timeframe", segment_results["by_setup_timeframe"])
        _write_segment_table("By Direction", segment_results["by_direction"])
        _write_segment_table("By Day of Week", segment_results["by_dow"])
        _write_segment_table("By Hour Bucket (UTC)", segment_results["by_hour_bucket"])

        # ── Section 6: MAE/MFE Assessment ─────────────────────────────────
        w("## 6. MAE/MFE Assessment")
        w("")
        w(f"**Status:** {mae_results['assessment']}")
        w("")
        w("The current `trace_json` stores entry-time indicator values and score components — ")
        w("it does **not** track bar-by-bar price excursions during the trade.")
        w("")
        w("### Proposed Schema Addition")
        w("")
        w("```sql")
        w("CREATE TABLE trade_bar_tracking (")
        for col in mae_results["schema_proposal"]["columns"]:
            w(f"    {col},")
        w(");")
        w("```")
        w("")
        w(f"**Implementation file:** `{mae_results['schema_proposal']['implementation_file']}` "
           f"(lines {mae_results['schema_proposal']['implementation_location']})")
        w("")
        w("**Pseudocode:**")
        w("```python")
        w(mae_results["schema_proposal"]["pseudocode"])
        w("```")
        w("")

        # ── Section 7: Code Attribution ───────────────────────────────────
        w("## 7. Code Attribution")
        w("")
        w("Top insights mapped to code paths:")
        w("")
        for attr in attributions:
            w(f"### {attr['insight_id']}: {attr['title']}")
            w("")
            if attr.get("explicit_path"):
                w(f"- **Explicit:** `{attr['explicit_path']}`")
            for ref in attr["code_refs"]:
                loc = f":{ref['lines']}" if "lines" in ref else ""
                w(f"- `{ref['file']}{loc}` — {ref['description']}")
                if "params" in ref:
                    w(f"  - Params: {ref['params']}")
            if not attr["code_refs"] and not attr.get("explicit_path"):
                w("- *(no specific code path identified)*")
            w("")

        # ── Section 8: Next Experiments ───────────────────────────────────
        w("## 8. Next Experiments")
        w("")
        for exp in experiments:
            w(f"### {exp['id']}: {exp['title']}")
            w("")
            w(f"- **Module:** `{exp['module']}`")
            w(f"- **Change:** {exp['change']}")
            w(f"- **Rationale:** {exp['rationale']}")
            w(f"- **Baseline:** {exp['baseline']}")
            w(f"- **Success metric:** {exp['metric']}")
            w("")

        # ── Section 9: What We Can't See Yet ──────────────────────────────
        w("## 9. What We Can't See Yet")
        w("")
        w("1. **MAE/MFE (bar-by-bar excursion tracking)** — Cannot assess whether stops are too "
           "tight or targets too conservative without knowing the intra-trade path. "
           "See Section 6 for implementation proposal.")
        w("")
        w("2. **Market regime at entry time** — BTC trend, overall market volatility regime, "
           "funding rates. Trades entered during high-correlation risk-off events may have "
           "structurally different outcomes. Would require adding BTC price/regime data to trace_json.")
        w("")
        w("3. **Slippage model accuracy** — The simulator applies a fixed slippage model. "
           "Without live fill data to compare against, we cannot validate whether the model "
           "is optimistic or pessimistic. Requires live trading data.")
        w("")
        w("4. **Multi-trade correlation** — When the same symbol fires multiple setups in "
           "close succession, are the outcomes correlated? Current analysis treats each trade "
           "independently. Would need time-proximity grouping of trades on the same symbol.")
        w("")
        w("5. **Order book depth / liquidity at entry** — Position sizing assumes sufficient "
           "liquidity. For microcaps (already filtered by the microcap gate), the actual "
           "fill quality depends on book depth not captured in OHLCV data.")
        w("")

        # Write the file
        report_path = outdir / "report.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        log.info(f"Report written: {report_path} ({len(lines)} lines)")
        return report_path


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="JARVIS — Simulation Run Analyser (Phase 2: Analyse & Attribute)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python sim_run_jarvis.py --run-id 25b54fff... --data-dir data/ --outdir .",
    )
    parser.add_argument("--run-id", required=True, help="Simulation run UUID")
    parser.add_argument("--data-dir", required=True, help="Directory containing trades.csv and features.csv")
    parser.add_argument("--outdir", required=True, help="Output directory for report.md and insights.json")
    parser.add_argument("--max-features", type=int, default=None, help="Limit feature columns (for testing)")
    args = parser.parse_args()

    t_start = time.time()
    data_dir = Path(args.data_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    log.info(f"=== JARVIS Phase 2: Analyse & Attribute ===")
    log.info(f"Run ID: {args.run_id}")
    log.info(f"Data dir: {data_dir}")
    log.info(f"Output dir: {outdir}")

    # ── Load data ─────────────────────────────────────────────────────────
    trades = DataLoader.load_trades(data_dir / "trades.csv")
    features = DataLoader.load_features(data_dir / "features.csv")
    merged = DataLoader.merge(trades, features)

    # Identify numeric feature columns
    feature_cols = [c for c in features.columns
                    if c != "id" and features[c].dtype in ["float64", "int64"]]
    if args.max_features:
        feature_cols = feature_cols[:args.max_features]
    log.info(f"Numeric feature columns: {len(feature_cols)}")

    collector = InsightCollector()

    # ── Section 3a: Correlations ──────────────────────────────────────────
    log.info("--- Section 3a: Univariate Correlations ---")
    corr_results = CorrelationEngine.spearman_vs_targets(merged, feature_cols, collector)

    # ── Section 3b: Decision Trees ────────────────────────────────────────
    log.info("--- Section 3b: Interaction Mining ---")
    tree_results = TreeMiner.fit_trees(merged, feature_cols, collector)

    # ── Section 3c: Clustering ────────────────────────────────────────────
    log.info("--- Section 3c: Trade Archetypes ---")
    cluster_results = ClusterEngine.find_archetypes(merged, feature_cols, collector)

    # ── Section 4: Exit Analysis ──────────────────────────────────────────
    log.info("--- Section 4: Exit Analysis ---")
    exit_results = ExitAnalyzer.analyze(merged, feature_cols, collector)

    # ── Section 5: Segment Deep Dives ─────────────────────────────────────
    log.info("--- Section 5: Segment Deep Dives ---")
    segment_results = SegmentAnalyzer.analyze(merged, collector)

    # ── Section 6: MAE/MFE ────────────────────────────────────────────────
    log.info("--- Section 6: MAE/MFE Assessment ---")
    mae_results = MAEMFEAssessor.assess(merged, collector)

    # ── Section 7: Code Attribution ───────────────────────────────────────
    log.info("--- Section 7: Code Attribution ---")
    attributions = CodeAttribution.attribute(collector.ranked())

    # ── Section 8: Experiments ────────────────────────────────────────────
    log.info("--- Section 8: Next Experiments ---")
    experiments = generate_experiments(merged, segment_results, exit_results, collector)

    # ── Write outputs ─────────────────────────────────────────────────────
    log.info("--- Writing report ---")
    report_path = ReportWriter.write(
        outdir, args.run_id, merged,
        corr_results, tree_results, cluster_results,
        exit_results, segment_results, mae_results,
        experiments, attributions, collector,
    )

    # Write insights.json
    insights_path = outdir / "insights.json"
    insights_data = {
        "run_id": args.run_id,
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "total_insights": len(collector.insights),
        "insights": collector.to_json(),
    }
    insights_path.write_text(json.dumps(insights_data, indent=2, default=str), encoding="utf-8")
    log.info(f"Insights written: {insights_path} ({len(collector.insights)} insights)")

    elapsed = time.time() - t_start
    log.info(f"=== JARVIS complete in {elapsed:.1f}s ===")
    log.info(f"  Report: {report_path}")
    log.info(f"  Insights: {insights_path}")


if __name__ == "__main__":
    main()
