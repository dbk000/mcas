"""
Correlation Analysis Module
===========================

Identifies patterns in losing trades by analyzing atomic indicator/signal
components from run_trades.trace_json and simulation_results.trace_json.

Components:
- feature_extractor: Extract namespaced features from trace_json
- stats: Cohen's d effect size, binned win-rate analysis
- executor: Job execution with FOR UPDATE SKIP LOCKED
- insights: CI log entry generation for autonomous loops
"""

from .feature_extractor import FeatureExtractor, TradeFeatures
from .stats import CorrelationAnalyzer, FeatureAnalysis, CorrelationReport
from .executor import CorrelationJobExecutor
from .insights import InsightsGenerator, InsightsStore, InsightDraft

__all__ = [
    "FeatureExtractor",
    "TradeFeatures",
    "CorrelationAnalyzer",
    "FeatureAnalysis",
    "CorrelationReport",
    "CorrelationJobExecutor",
    "InsightsGenerator",
    "InsightsStore",
    "InsightDraft",
]
