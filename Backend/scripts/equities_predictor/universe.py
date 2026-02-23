"""Deterministic equities universe (50 large-cap stocks + ETFs)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EquitySymbol:
    symbol: str
    name: str
    sector: str
    is_etf: bool = False


# 50 liquid US equities + broad/sector ETFs.
UNIVERSE: tuple[EquitySymbol, ...] = (
    EquitySymbol("AAPL", "Apple Inc.", "Technology"),
    EquitySymbol("MSFT", "Microsoft Corp.", "Technology"),
    EquitySymbol("NVDA", "NVIDIA Corp.", "Technology"),
    EquitySymbol("AMZN", "Amazon.com Inc.", "Consumer Discretionary"),
    EquitySymbol("GOOGL", "Alphabet Inc. Class A", "Communication Services"),
    EquitySymbol("META", "Meta Platforms Inc.", "Communication Services"),
    EquitySymbol("TSLA", "Tesla Inc.", "Consumer Discretionary"),
    EquitySymbol("BRK-B", "Berkshire Hathaway Inc. Class B", "Financials"),
    EquitySymbol("JPM", "JPMorgan Chase & Co.", "Financials"),
    EquitySymbol("V", "Visa Inc.", "Financials"),
    EquitySymbol("MA", "Mastercard Inc.", "Financials"),
    EquitySymbol("BAC", "Bank of America Corp.", "Financials"),
    EquitySymbol("GS", "Goldman Sachs Group Inc.", "Financials"),
    EquitySymbol("WFC", "Wells Fargo & Co.", "Financials"),
    EquitySymbol("C", "Citigroup Inc.", "Financials"),
    EquitySymbol("UNH", "UnitedHealth Group Inc.", "Health Care"),
    EquitySymbol("JNJ", "Johnson & Johnson", "Health Care"),
    EquitySymbol("PFE", "Pfizer Inc.", "Health Care"),
    EquitySymbol("MRK", "Merck & Co. Inc.", "Health Care"),
    EquitySymbol("ABBV", "AbbVie Inc.", "Health Care"),
    EquitySymbol("TMO", "Thermo Fisher Scientific Inc.", "Health Care"),
    EquitySymbol("XOM", "Exxon Mobil Corp.", "Energy"),
    EquitySymbol("CVX", "Chevron Corp.", "Energy"),
    EquitySymbol("COP", "ConocoPhillips", "Energy"),
    EquitySymbol("SLB", "Schlumberger NV", "Energy"),
    EquitySymbol("PG", "Procter & Gamble Co.", "Consumer Staples"),
    EquitySymbol("KO", "Coca-Cola Co.", "Consumer Staples"),
    EquitySymbol("PEP", "PepsiCo Inc.", "Consumer Staples"),
    EquitySymbol("WMT", "Walmart Inc.", "Consumer Staples"),
    EquitySymbol("COST", "Costco Wholesale Corp.", "Consumer Staples"),
    EquitySymbol("MCD", "McDonald's Corp.", "Consumer Discretionary"),
    EquitySymbol("HD", "Home Depot Inc.", "Consumer Discretionary"),
    EquitySymbol("LOW", "Lowe's Companies Inc.", "Consumer Discretionary"),
    EquitySymbol("NKE", "Nike Inc.", "Consumer Discretionary"),
    EquitySymbol("CRM", "Salesforce Inc.", "Technology"),
    EquitySymbol("ORCL", "Oracle Corp.", "Technology"),
    EquitySymbol("ADBE", "Adobe Inc.", "Technology"),
    EquitySymbol("AMD", "Advanced Micro Devices Inc.", "Technology"),
    EquitySymbol("QCOM", "QUALCOMM Inc.", "Technology"),
    EquitySymbol("AVGO", "Broadcom Inc.", "Technology"),
    EquitySymbol("TXN", "Texas Instruments Inc.", "Technology"),
    EquitySymbol("INTC", "Intel Corp.", "Technology"),
    EquitySymbol("IBM", "International Business Machines Corp.", "Technology"),
    EquitySymbol("CAT", "Caterpillar Inc.", "Industrials"),
    EquitySymbol("GE", "GE Aerospace", "Industrials"),
    EquitySymbol("HON", "Honeywell International Inc.", "Industrials"),
    EquitySymbol("RTX", "RTX Corp.", "Industrials"),
    EquitySymbol("LMT", "Lockheed Martin Corp.", "Industrials"),
    EquitySymbol("NEE", "NextEra Energy Inc.", "Utilities"),
    EquitySymbol("DUK", "Duke Energy Corp.", "Utilities"),
    EquitySymbol("SPY", "SPDR S&P 500 ETF Trust", "Index ETF", True),
    EquitySymbol("QQQ", "Invesco QQQ Trust", "Index ETF", True),
    EquitySymbol("XLK", "Technology Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLF", "Financial Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLE", "Energy Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLV", "Health Care Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLY", "Consumer Discretionary Select Sector SPDR", "Sector ETF", True),
    EquitySymbol("XLI", "Industrial Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLP", "Consumer Staples Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLU", "Utilities Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLB", "Materials Select Sector SPDR Fund", "Sector ETF", True),
    EquitySymbol("XLRE", "Real Estate Select Sector SPDR Fund", "Sector ETF", True),
)


def get_default_symbols() -> list[str]:
    return [s.symbol for s in UNIVERSE]


def get_universe_rows() -> list[dict[str, object]]:
    rows = []
    for item in UNIVERSE:
        aliases = [item.symbol]
        if item.symbol == "BRK-B":
            aliases.extend(["BRK.B", "BRKB"])
        rows.append(
            {
                "symbol": item.symbol,
                "name": item.name,
                "sector": item.sector,
                "is_etf": item.is_etf,
                "aliases": aliases,
            }
        )
    return rows
