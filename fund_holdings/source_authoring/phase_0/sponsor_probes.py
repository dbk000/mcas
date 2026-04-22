"""Bulk sponsor probe. For each priority sponsor, attempt a set of candidate
per-fund URL patterns against known tickers. Record which pattern + format
works. This informs sponsor_endpoints.yaml authoring.

The goal here is discovery — §6.2 Step 1. We run one sample fetch per sponsor
pattern (using a well-known ticker like SPY/VOO/QQQ/IVV), content-verify the
response, and record the result. No bible mutation happens in this script.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from http_client import new_session, fetch
from content_verify import verify_holdings_url

# Per-sponsor: (probe_ticker, [candidate_pattern_template, ...], format)
# Patterns support {TICKER} / {ticker_lower} / {ticker_lower_clean}
# Each entry is (pattern_label, template, format, expected_ticker_for_probe)
PROBES = {
    "ishares": [
        ("ivv_by_product_id", "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund", "csv", "IVV"),
    ],
    "state_street": [
        ("ssga_xlsx_intermediary", "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker_lower}.xlsx", "xlsx", "spy"),
        ("ssga_xlsx_alt", "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker_lower}.xlsx", "xlsx", "spy"),
    ],
    "direxion": [
        ("csv_direct", "https://www.direxion.com/holdings/{TICKER}.csv", "csv", "SOXL"),
    ],
    "vanguard": [
        # Vanguard has no public CSV; profile page is JS rendered
        ("profile", "https://investor.vanguard.com/investment-products/etfs/profile/{ticker_lower}", "html_table", "vti"),
        ("portfolio_api", "https://api.vanguard.com/rs/ire/01/ind/fund/{ticker_upper}/portfolio-holding/stock.json", "json", "VTI"),
        ("holdings_page", "https://advisors.vanguard.com/advisors-home/public/product-detail-etf?ticker={ticker_upper}&tab=holdings", "html_table", "VTI"),
    ],
    "invesco": [
        ("csv_invesco", "https://www.invesco.com/us/financial-products/etfs/holdings?audienceType=Investor&ticker={ticker_upper}&action=download&format=CSV", "csv", "QQQ"),
        ("csv_qqq_alt", "https://www.invesco.com/us-rest/invesco-etf-holdings-csv?ticker={ticker_upper}", "csv", "QQQ"),
        ("product_detail", "https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker={ticker_upper}", "html_table", "QQQ"),
    ],
    "proshares": [
        ("csv_download", "https://www.proshares.com/our-etfs/download-file?ticker={ticker_upper}&downloadType=holdings", "csv", "SH"),
        ("csv_services", "https://services.proshares.com/proshares-portal-web-app/rest/v1/service/portal/fund/{ticker_upper}/holdings/CSV", "csv", "SH"),
        ("csv_assets", "https://www.proshares.com/globalassets/proshares/resources/holdings/{ticker_lower}-holdings.csv", "csv", "sh"),
    ],
    "first_trust": [
        ("aspx_holdings", "https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker={ticker_upper}", "html_table", "FVD"),
        ("csv_blob", "https://www.ftportfolios.com/RetailData/Etf/ETFHoldings.csv?Ticker={ticker_upper}", "csv", "FVD"),
        ("csv_assetmgr", "https://www.ftportfolios.com/Retail/Etf/EtfHoldingsCSV.aspx?Ticker={ticker_upper}", "csv", "FVD"),
    ],
    "global_x": [
        ("page", "https://www.globalxetfs.com/funds/{ticker_lower}/", "html_table", "qyld"),
        ("csv_ep", "https://www.globalxetfs.com/funds/{ticker_lower}/holdings/csv/", "csv", "qyld"),
        ("csv_api", "https://www.globalxetfs.com/api/funds/{ticker_lower}/holdings?format=csv", "csv", "qyld"),
    ],
    "vaneck": [
        ("holdings_page", "https://www.vaneck.com/us/en/investments/morningstar-wide-moat-etf-{ticker_lower}/holdings/", "html_table", "moat"),
        ("holdings_csv_redirect", "https://www.vaneck.com/etf/etf-holdings-csv/?fund={ticker_upper}", "csv", "MOAT"),
    ],
    "schwab": [
        ("holdings_csv", "https://www.schwabassetmanagement.com/allholdings/{ticker_upper}", "csv", "SCHX"),
        ("page", "https://www.schwabassetmanagement.com/products/{ticker_lower}", "html_table", "schx"),
    ],
    "jpmorgan": [
        ("amp_page", "https://am.jpmorgan.com/us/en/asset-management/adv/products/{ticker_lower}-etf-shares-46641q332", "html_table", "jpmorgan-equity-premium-income"),
        ("holdings_json", "https://am.jpmorgan.com/services/etf-holdings?ticker={ticker_upper}", "json", "JEPI"),
    ],
    "wisdomtree": [
        ("etf_page", "https://www.wisdomtree.com/investments/etfs/equity/{ticker_lower}", "html_table", "dgrw"),
    ],
    "fidelity": [
        ("comp_page", "https://fundresearch.fidelity.com/mutual-funds/composition/{ticker_upper}", "html_table", "FTEC"),
        ("holdings_csv", "https://fundresearch.fidelity.com/mutual-funds/download-csv/composition/{ticker_upper}", "csv", "FTEC"),
    ],
    "pacer": [
        ("page", "https://www.paceretfs.com/products/{ticker_lower}", "html_table", "cowz"),
    ],
    "dimensional": [
        ("page", "https://us.dimensional.com/funds/{ticker_lower}", "html_table", "dfac"),
    ],
    "blackrock": [
        # Non-iShares BlackRock funds (e.g. closed-end or smart-beta wrappers)
        ("blackrock_pg", "https://www.blackrock.com/us/individual/products/{ticker_lower}", "html_table", "bkch"),
    ],
    "franklin_templeton": [
        ("page", "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/{ticker_lower}-etf", "html_table", "flx"),
        ("fund_card", "https://www.franklintempleton.com/api/funds/etfs/{ticker_upper}", "json", "FLX"),
    ],
    "krane": [
        ("etf_page", "https://kraneshares.com/etf/{ticker_lower}/", "html_table", "kweb"),
        ("holdings_csv", "https://kraneshares.com/api/etf-holdings/{ticker_upper}/csv", "csv", "KWEB"),
    ],
    "innovator": [
        ("page", "https://www.innovatoretfs.com/etf/?ticker={ticker_upper}", "html_table", "BOCT"),
    ],
    "nuveen": [
        ("page_us", "https://www.nuveen.com/en-us/exchange-traded-funds/{ticker_upper}", "html_table", "NULC"),
    ],
    "graniteshares": [
        ("page", "https://graniteshares.com/institutional/us/en-us/etfs/{ticker_lower}/", "html_table", "nvdl"),
    ],
    "pimco": [
        ("page", "https://www.pimco.com/en-us/investments/etf/{ticker_lower}", "html_table", "bond"),
    ],
    "alliancebernstein": [
        ("page", "https://www.abfunds.com/link/en/etf/{ticker_lower}-overview", "html_table", "yeld"),
    ],
    "harbor": [
        ("page", "https://www.harborcapital.com/etfs/{ticker_lower}/", "html_table", "hlal"),
    ],
    "columbia": [
        ("page", "https://www.columbiathreadneedleus.com/etfs/{ticker_upper}", "html_table", "RQI"),
    ],
    "janus_henderson": [
        ("page", "https://www.janushenderson.com/en-us/investor/product/{ticker_lower}", "html_table", "jaaa"),
    ],
    "alpha_architect": [
        ("etf_site", "https://etfsite.alphaarchitect.com/{ticker_lower}/", "html_table", "qmom"),
    ],
    "tidal": [
        # Tidal is a multi-subadviser white-label host — no single pattern exists
        ("tidal_etfs", "https://tidaletfservices.com/etfs/{ticker_upper}/", "html_table", "FEPI"),
    ],
    "xtrackers": [
        # DWS xtrackers — dns issues with etfus.dws.com
        ("dws_alt", "https://etf.dws.com/en-us/{ticker_upper}", "html_table", "DBEF"),
    ],
    "bondbloxx": [
        ("page", "https://bondbloxx.com/product/{ticker_lower}/", "html_table", "xccc"),
    ],
    "victory_capital": [
        ("etf_page", "https://www.vcm.com/etfs/{ticker_lower}", "html_table", "carz"),
    ],
    "northern_trust": [
        # FlexShares is Northern Trust's ETF arm
        ("flexshares", "https://flexshares.com/en-us/fund/{ticker_lower}", "html_table", "asha"),
    ],
    "t_rowe_price": [
        ("tr_page", "https://www.troweprice.com/financial-intermediary/us/en/investments/etfs/products/{ticker_lower}.html", "html_table", "tcaf"),
    ],
    "capital_group": [
        ("page", "https://www.capitalgroup.com/advisor/investments/etf/{ticker_lower}", "html_table", "cgus"),
    ],
    "pgim": [
        ("pgim_inv", "https://www.pgim.com/investments/etf/{ticker_lower}", "html_table", "ptst"),
    ],
    "principal": [
        ("page", "https://www.principalam.com/us/etf/{ticker_lower}", "html_table", "psfd"),
    ],
    "neuberger_berman": [
        ("etf_page", "https://www.nb.com/en/global/etf/{ticker_lower}", "html_table", "nbcm"),
    ],
    "john_hancock": [
        ("page", "https://www.jhinvestments.com/etf/multifactor-{ticker_lower}", "html_table", "jhmm"),
    ],
    "alps": [
        ("etf_page", "https://www.alpsfunds.com/etfs/{ticker_lower}", "html_table", "amlp"),
    ],
    "allspring": [
        ("page", "https://www.allspringglobal.com/api/etfs/{ticker_upper}", "json", "WLRG"),
    ],
    "hartford": [
        ("hfg_page", "https://www.hartfordfunds.com/etfs/{ticker_lower}-overview.html", "html_table", "hegd"),
    ],
    "inspire": [
        ("etf_page", "https://www.inspireetf.com/{ticker_lower}/", "html_table", "bibl"),
    ],
    "touchstone": [
        ("page", "https://www.touchstoneinvestments.com/etfs/{ticker_upper}", "html_table", "tser"),
    ],
    "doubleline": [
        ("etf_page", "https://www.doubleline.com/etfs/{ticker_lower}/", "html_table", "dbnd"),
    ],
    "strategy_shares": [
        ("etf_page", "https://www.strategysharesetfs.com/etfs/{ticker_lower}/", "html_table", "cefs"),
    ],
    "unaffiliated_tradr": [
        ("tradr_page", "https://tradretfs.com/etfs/{ticker_upper}/", "html_table", "MSTX"),
    ],
    "frontier_asset": [
        ("etf_page", "https://www.frontierasset.com/etfs/{ticker_lower}", "html_table", "fafm"),
    ],
    "gmo": [
        ("gmo_page", "https://www.gmo.com/americas/product-index/etfs/{ticker_lower}/", "html_table", "qlty"),
    ],
    "macquarie": [
        ("etf_page", "https://www.delawarefunds.com/etfs/{ticker_lower}", "html_table", "dlax"),
    ],
    "putnam": [
        ("putnam_page", "https://www.putnam.com/individual/etf/{ticker_upper}", "html_table", "PBDC"),
    ],
    "sprott": [
        ("etf_page", "https://sprottetfs.com/sprott-physical-miner-etfs/{ticker_lower}-fund/", "html_table", "sgdm"),
    ],
    "bny_mellon": [
        ("page", "https://im.bnymellon.com/us/en/intermediary/product-detail.etfs-{ticker_lower}.html", "html_table", "bkch"),
    ],
    "virtus": [
        ("virtus_page", "https://www.virtusetfs.com/funds/{ticker_upper}", "html_table", "STR"),
    ],
    "goldman_sachs": [
        ("gsam_page", "https://www.gsam.com/content/gsam/us/en/advisors/fund-center/etf-fund-finder.html?fundId={ticker_upper}", "html_table", "GSEE"),
    ],
    "abrdn": [
        ("abrdn_page", "https://www.abrdn.com/en-us/us/etf/fund-centre/etf-fund-centre/{ticker_lower}", "html_table", "aint"),
    ],
    "alger": [
        ("alger_page", "https://www.alger.com/etf/{ticker_lower}", "html_table", "atfv"),
    ],
    "bondbloxx_alt": [
        ("page_alt", "https://bondbloxx.com/etfs/{ticker_lower}/", "html_table", "xccc"),
    ],
    "mfs": [
        ("mfs_page", "https://www.mfs.com/en-us/investment-professional/products/mfs-etfs.html#{ticker_upper}", "html_table", "MFSC"),
    ],
    "russell_investments": [
        ("page", "https://russellinvestments.com/us/etfs/{ticker_upper}", "html_table", "RIET"),
    ],
    "lazard": [
        ("lazard_page", "https://lazardfunds.com/etf/{ticker_lower}", "html_table", "lqfi"),
    ],
    "tcw": [
        ("tcw_page", "https://www.tcw.com/products/etf/{ticker_lower}", "html_table", "fmhi"),
    ],
    "tortoise": [
        ("tortoise_page", "https://tortoiseetfs.com/etfs/{ticker_lower}/", "html_table", "tyld"),
    ],
    "tema": [
        ("tema_page", "https://www.temaetfs.com/{ticker_upper}/", "html_table", "hrts"),
    ],
    "unaffiliated_oneascent": [
        ("oa_page", "https://oneascent.com/etf/{ticker_upper}/", "html_table", "OALC"),
    ],
    "themes": [
        ("themes_page", "https://themesetfs.com/themes/{ticker_upper}", "html_table", "PAVE"),
    ],
    "exchange_traded_concepts": [
        # White-label host — no single pattern
        ("etcfunds", "https://www.etfarchitect.com/{ticker_upper}/", "html_table", "RAYE"),
    ],
    "neos": [
        ("neos_page", "https://neosfunds.com/etfs/{ticker_lower}/", "html_table", "spyi"),
    ],
    "calamos": [
        ("calamos_page", "https://www.calamos.com/funds/etfs/{ticker_lower}/", "html_table", "cgra"),
    ],
}


def expand(template: str, ticker: str) -> str:
    return (
        template
        .replace("{TICKER}", ticker.upper())
        .replace("{ticker_upper}", ticker.upper())
        .replace("{ticker_lower}", ticker.lower())
        .replace("{ticker_lower_clean}", ticker.lower().replace(".", "").replace("-", ""))
    )


def probe_sponsor(sponsor: str, candidates):
    sess = new_session()
    out = []
    for label, tmpl, fmt, ticker in candidates:
        url = expand(tmpl, ticker)
        t0 = time.monotonic()
        ok, reason, info = verify_holdings_url(url, fmt, sess, expected_ticker=ticker)
        dt = time.monotonic() - t0
        out.append({
            "sponsor": sponsor,
            "label": label,
            "template": tmpl,
            "format": fmt,
            "probe_ticker": ticker,
            "expanded_url": url,
            "ok": ok,
            "reason": reason,
            "info": info,
            "took_s": round(dt, 2),
        })
    return out


def main():
    out_path = Path(__file__).parent / "sponsor_probes_result.jsonl"
    sponsors = list(PROBES.items())
    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(probe_sponsor, s, c): s for s, c in sponsors}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                r = fut.result()
                results.extend(r)
                print(f"[{s:25s}] {len(r)} probes")
            except Exception as e:
                print(f"[{s:25s}] ERR {type(e).__name__}: {e}")
    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    # Summary
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = sum(1 for r in results if not r["ok"])
    print(f"\nTotal probes: {len(results)}  OK: {ok_count}  FAIL: {fail_count}")
    print(f"\nWorking patterns (ok=True):")
    for r in results:
        if r["ok"]:
            print(f"  {r['sponsor']:25s} {r['label']:30s} {r['format']:10s} {r['expanded_url'][:80]}")
    print(f"\nFailing patterns (sample of first 20):")
    for r in [r for r in results if not r["ok"]][:30]:
        print(f"  {r['sponsor']:25s} {r['label']:30s} reason={r['reason']}")
    print(f"\nWrote: {out_path}")


if __name__ == "__main__":
    main()
