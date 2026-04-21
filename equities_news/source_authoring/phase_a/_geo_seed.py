"""One-shot seed generator for registry/news_sources/geo/*.yaml.

Run once (or re-run to regenerate from scratch — it overwrites). After this,
future edits should go via geo_author.py --propose.

Per country, we provide 3-10 country-specific sources that are NOT already in
universal.yaml. Rules:
  - Country-specific business media (local-language or local-focus).
  - Local regulatory news beyond the universal filings regime entry.
  - Country macro/economic data feeds.
  - Local exchange news for listings, trading halts, etc.
  - NO sector-specific sources (Mining.com, Endpoints, etc.) — those are Phase B.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import NEWS_SOURCES_DIR, dump_yaml, utc_now_iso

GEO_DIR = NEWS_SOURCES_DIR / "geo"
NOW = utc_now_iso()


def entry(
    sid: str, name: str, url: str, adapter: str, juris: str, lang: str,
    cost: str, cadence: str, events: list[str], edge: float, lead: int, notes: str,
) -> dict:
    return {
        "source_id": sid,
        "source_name": name,
        "url": url,
        "adapter_type": adapter,
        "jurisdiction": juris,
        "language": lang,
        "cost_tier": cost,
        "update_cadence": cadence,
        "event_types_covered": events,
        "edge_weight_prior": edge,
        "typical_lead_time_vs_english_minutes": lead,
        "notes": notes,
        "lineage": {"authored_by": "phase_a_opus", "authored_at": NOW},
    }


MACRO = ["macro_data_release"]
CORP = ["earnings_report", "ma_announcement", "executive_change"]
REGULATORY = ["regulatory_action", "sector_regulation", "investigation_disclosure"]
DISCLOSURE = ["material_disclosure", "regulatory_filing"]


def seed() -> dict[str, list[dict]]:
    """cc -> list of entries."""
    return {
        # -------------------------------------------------------------------
        # US (2540 symbols)
        # -------------------------------------------------------------------
        "us": [
            entry("geo.us.federal_register", "Federal Register (US Government)",
                  "https://www.federalregister.gov/", "api", "US", "en",
                  "free", "daily", ["sector_regulation", "regulatory_action"], 0.9, 0,
                  "Authoritative gazette for US federal agency rules + notices. Not issuer-specific; covers all sectors."),
            entry("geo.us.cnbc", "CNBC", "https://www.cnbc.com/world/?region=world", "rss", "US", "en",
                  "free", "realtime", CORP + ["sell_side_rating_change"], 0.75, 0,
                  "US cable business news. Strong on US corporate earnings + Fed coverage."),
            entry("geo.us.marketwatch", "MarketWatch", "https://www.marketwatch.com/", "rss", "US", "en",
                  "free", "realtime", CORP + ["macro_data_release"], 0.7, 0,
                  "Dow Jones retail finance portal. Free web. Fast aggregator, not usually first-break."),
            entry("geo.us.yahoo_finance", "Yahoo Finance", "https://finance.yahoo.com/", "rss", "US", "en",
                  "free", "realtime", CORP + ["earnings_report"], 0.65, 15,
                  "Retail-popular finance portal. Aggregator rather than original source; useful for sentiment/retail signal."),
            entry("geo.us.barrons", "Barron's", "https://www.barrons.com/", "scraper", "US", "en",
                  "paid", "weekly", CORP + ["sell_side_rating_change"], 0.75, 60,
                  "Weekly value-investing flagship. Feature stories + Roundtable move mid-cap prices on Monday opens."),
            entry("geo.us.investors_business_daily", "Investor's Business Daily",
                  "https://www.investors.com/", "scraper", "US", "en",
                  "paid", "daily", CORP + ["sell_side_rating_change"], 0.7, 0,
                  "CAN SLIM growth-investing paper. Proprietary ratings."),
            entry("geo.us.bls_releases", "US Bureau of Labor Statistics — News Releases",
                  "https://www.bls.gov/schedule/news_release/", "rss", "US", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, NFP, PPI, JOLTS. Authoritative US labor + inflation data."),
            entry("geo.us.bea_news", "US Bureau of Economic Analysis — News",
                  "https://www.bea.gov/news", "rss", "US", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "GDP, personal income, trade balance."),
            entry("geo.us.cftc_press", "CFTC Press Releases",
                  "https://www.cftc.gov/PressRoom/PressReleases", "rss", "US", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Commodity futures regulator. Enforcement actions + position limits."),
            entry("geo.us.finra_news", "FINRA Newsroom",
                  "https://www.finra.org/media-center/news-releases", "rss", "US", "en",
                  "free", "ad_hoc", REGULATORY, 0.85, 0,
                  "Broker-dealer self-regulator. Enforcement fines + rule changes."),
        ],

        # -------------------------------------------------------------------
        # JP (924)
        # -------------------------------------------------------------------
        "jp": [
            entry("geo.jp.nikkei_business_daily", "Nikkei Business Daily (JP-language)",
                  "https://www.nikkei.com/", "scraper", "JP", "ja",
                  "paid", "realtime", CORP + ["sell_side_rating_change", "macro_data_release"], 0.9, -120,
                  "Japan's business daily, JP-language flagship. Distinct from universal.nikkei_asia_english. Leads English wires."),
            entry("geo.jp.toyo_keizai", "Toyo Keizai Online",
                  "https://toyokeizai.net/", "rss", "JP", "ja",
                  "metered", "daily", CORP + ["macro_data_release"], 0.8, -60,
                  "Long-form business weekly + daily online. Strong on corporate strategy analysis."),
            entry("geo.jp.diamond_online", "Diamond Online",
                  "https://diamond.jp/", "rss", "JP", "ja",
                  "metered", "daily", CORP, 0.75, -60,
                  "Japanese business weekly. Scoop-heavy editorial on listed companies."),
            entry("geo.jp.quick_news", "QUICK — Nikkei market data service",
                  "https://corporate.quick.co.jp/", "api", "JP", "ja",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -60,
                  "QUICK is Nikkei's market data/news terminal, Japanese equivalent of Bloomberg for JP desks."),
            entry("geo.jp.jpx_announcements", "JPX Group — Announcements",
                  "https://www.jpx.co.jp/english/news/", "rss", "JP", "en",
                  "free", "daily", ["sector_regulation", "regulatory_action", "delisting", "index_inclusion"], 0.9, 0,
                  "Japan Exchange Group parent-company announcements. Listing changes + index reviews."),
            entry("geo.jp.metis_press", "Ministry of Economy, Trade and Industry (METI) — Press",
                  "https://www.meti.go.jp/english/press/", "rss", "JP", "en",
                  "free", "ad_hoc", ["sector_regulation", "macro_data_release", "contract_award"], 0.85, 0,
                  "METI industrial policy announcements. Subsidies, export controls, sector strategy."),
            entry("geo.jp.mof_press", "Ministry of Finance (Japan) — Press",
                  "https://www.mof.go.jp/english/index.htm", "rss", "JP", "en",
                  "free", "ad_hoc", MACRO + ["sector_regulation"], 0.9, 0,
                  "Fiscal policy, FX intervention announcements, debt auctions."),
            entry("geo.jp.cao_gdp", "Cabinet Office — Quarterly GDP",
                  "https://www.esri.cao.go.jp/en/sna/menu.html", "scraper", "JP", "en",
                  "free", "weekly", MACRO, 1.0, 0,
                  "Authoritative quarterly GDP release."),
            entry("geo.jp.stat_cpi", "Statistics Bureau Japan — CPI",
                  "https://www.stat.go.jp/english/data/cpi/", "scraper", "JP", "en",
                  "free", "weekly", MACRO, 1.0, 0,
                  "Monthly CPI release."),
            entry("geo.jp.boj_tankan", "BOJ Tankan Survey",
                  "https://www.boj.or.jp/en/statistics/tk/", "scraper", "JP", "en",
                  "free", "weekly", MACRO, 1.0, 0,
                  "Quarterly business sentiment survey — widely-watched leading indicator."),
            entry("geo.jp.mainichi_economist", "Mainichi Keizaishi (Weekly Economist)",
                  "https://weekly-economist.mainichi.jp/", "scraper", "JP", "ja",
                  "paid", "weekly", CORP + ["macro_data_release"], 0.7, 0,
                  "Mainichi's weekly business magazine. Analytical rather than breaking."),
        ],

        # -------------------------------------------------------------------
        # GB (260)
        # -------------------------------------------------------------------
        "gb": [
            entry("geo.gb.the_times_business", "The Times — Business",
                  "https://www.thetimes.com/business-money", "rss", "GB", "en",
                  "paid", "daily", CORP, 0.8, 0,
                  "UK broadsheet business desk. Scoop-driven on UK corporates."),
            entry("geo.gb.telegraph_business", "The Telegraph — Business",
                  "https://www.telegraph.co.uk/business/", "rss", "GB", "en",
                  "paid", "daily", CORP, 0.75, 0,
                  "UK broadsheet business desk. Strong on FTSE 250 + small-cap."),
            entry("geo.gb.city_am", "City A.M.",
                  "https://www.cityam.com/", "rss", "GB", "en",
                  "free", "realtime", CORP + ["sell_side_rating_change"], 0.75, 0,
                  "London financial-district free daily. Strong on UK listed company coverage."),
            entry("geo.gb.investors_chronicle", "Investors' Chronicle",
                  "https://www.investorschronicle.co.uk/", "scraper", "GB", "en",
                  "paid", "weekly", CORP + ["sell_side_rating_change"], 0.8, 0,
                  "FT-owned weekly. Strong analytical coverage of UK-listed mid/small caps."),
            entry("geo.gb.companies_house_stream", "Companies House — Streaming / Filing History",
                  "https://developer.company-information.service.gov.uk/", "api", "GB", "en",
                  "free", "realtime", DISCLOSURE + ["executive_change", "regulatory_filing"], 0.9, 0,
                  "Statutory company registrar. Director appointments, accounts, charges — deeper than RNS."),
            entry("geo.gb.ons_releases", "Office for National Statistics — Releases",
                  "https://www.ons.gov.uk/releasecalendar", "rss", "GB", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "UK CPI, GDP, employment, retail sales."),
            entry("geo.gb.hm_treasury", "HM Treasury — News",
                  "https://www.gov.uk/government/organisations/hm-treasury", "rss", "GB", "en",
                  "free", "ad_hoc", ["sector_regulation"] + MACRO, 0.9, 0,
                  "UK fiscal announcements, Budget, Spring Statement."),
            entry("geo.gb.sfo_news", "Serious Fraud Office — News",
                  "https://www.sfo.gov.uk/news/", "rss", "GB", "en",
                  "free", "ad_hoc", REGULATORY + ["litigation"], 0.85, 0,
                  "UK fraud investigator. Corporate charges + DPAs."),
        ],

        # -------------------------------------------------------------------
        # AU (232)
        # -------------------------------------------------------------------
        "au": [
            entry("geo.au.sydney_morning_herald_biz", "Sydney Morning Herald — Business",
                  "https://www.smh.com.au/business", "rss", "AU", "en",
                  "paid", "daily", CORP, 0.7, 0,
                  "Nine Media broadsheet. Backup to AFR for ASX corporate coverage."),
            entry("geo.au.the_australian_business", "The Australian — Business",
                  "https://www.theaustralian.com.au/business", "rss", "AU", "en",
                  "paid", "daily", CORP, 0.75, 0,
                  "News Corp broadsheet. Strong on mining + resources."),
            entry("geo.au.abc_news_business", "ABC News — Business",
                  "https://www.abc.net.au/news/business/", "rss", "AU", "en",
                  "free", "realtime", CORP + ["macro_data_release"], 0.7, 0,
                  "Public broadcaster. Strong on regulation + macro."),
            entry("geo.au.rba_media", "Reserve Bank of Australia — Media Releases",
                  "https://www.rba.gov.au/media-releases/", "rss", "AU", "en",
                  "free", "ad_hoc", MACRO + ["sector_regulation"], 1.0, 0,
                  "RBA rate decisions, Statement on Monetary Policy."),
            entry("geo.au.abs_releases", "Australian Bureau of Statistics — Releases",
                  "https://www.abs.gov.au/media-centre/media-releases", "rss", "AU", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, employment, GDP, retail trade."),
            entry("geo.au.asic_media", "ASIC — Media Centre",
                  "https://asic.gov.au/about-asic/news-centre/", "rss", "AU", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Australian Securities & Investments Commission enforcement + guidance."),
            entry("geo.au.aer_releases", "Australian Energy Regulator — Media",
                  "https://www.aer.gov.au/news", "rss", "AU", "en",
                  "free", "ad_hoc", ["sector_regulation"], 0.75, 0,
                  "Energy regulator. Relevant for utilities + AEMO distribution businesses."),
        ],

        # -------------------------------------------------------------------
        # SE (154)
        # -------------------------------------------------------------------
        "se": [
            entry("geo.se.dagens_industri", "Dagens Industri",
                  "https://www.di.se/", "scraper", "SE", "sv",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -60,
                  "Sweden's leading business daily. Bonnier group. Leads English wires on Nordic corporates."),
            entry("geo.se.affarsvarlden", "Affärsvärlden",
                  "https://www.affarsvarlden.se/", "scraper", "SE", "sv",
                  "paid", "weekly", CORP + ["sell_side_rating_change"], 0.75, 0,
                  "Weekly business magazine. Analytical coverage of Stockholm-listed."),
            entry("geo.se.svd_naringsliv", "Svenska Dagbladet — Näringsliv",
                  "https://www.svd.se/naringsliv/", "rss", "SE", "sv",
                  "paid", "daily", CORP, 0.7, 0,
                  "Svenska Dagbladet's business section."),
            entry("geo.se.fi_press", "Finansinspektionen — Press Releases",
                  "https://www.fi.se/en/press/press-releases/", "rss", "SE", "en",
                  "free", "ad_hoc", REGULATORY + DISCLOSURE, 0.9, 0,
                  "Swedish FSA. Insider-transaction registry + enforcement."),
            entry("geo.se.scb_releases", "Statistics Sweden (SCB) — Statistical News",
                  "https://www.scb.se/en/finding-statistics/statistical-news/", "rss", "SE", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "SCB is Sweden's statistics office. CPI, GDP, employment."),
            entry("geo.se.riksbank_press", "Sveriges Riksbank — Press Releases",
                  "https://www.riksbank.se/en-gb/press-and-published/notices-and-press-releases/", "rss", "SE", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Swedish central bank. Rate decisions + financial stability reports."),
        ],

        # -------------------------------------------------------------------
        # DE (142) — already have Handelsblatt, FAZ, DGAP in universal
        # -------------------------------------------------------------------
        "de": [
            entry("geo.de.manager_magazin", "Manager Magazin",
                  "https://www.manager-magazin.de/", "rss", "DE", "de",
                  "metered", "daily", CORP + ["activist_campaign"], 0.8, -30,
                  "Spiegel-group monthly/online business magazine. Strong on M&A exclusives."),
            entry("geo.de.wirtschaftswoche", "WirtschaftsWoche",
                  "https://www.wiwo.de/", "rss", "DE", "de",
                  "paid", "weekly", CORP, 0.75, 0,
                  "Weekly German business magazine. Analytical."),
            entry("geo.de.boersen_zeitung", "Börsen-Zeitung",
                  "https://www.boersen-zeitung.de/", "scraper", "DE", "de",
                  "paid", "daily", CORP + ["sell_side_rating_change"], 0.85, -30,
                  "Frankfurt-based specialist markets daily. Strong on fixed income + equities."),
            entry("geo.de.destatis_press", "Destatis — Statistisches Bundesamt",
                  "https://www.destatis.de/EN/Press/", "rss", "DE", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "German federal statistics office. CPI, GDP, industrial production."),
            entry("geo.de.bundesbank_press", "Deutsche Bundesbank — Press",
                  "https://www.bundesbank.de/en/press", "rss", "DE", "en",
                  "free", "ad_hoc", MACRO + ["sector_regulation"], 0.95, 0,
                  "Bundesbank notes complement ECB. Relevant for German-specific financial stability."),
            entry("geo.de.bafin_news", "BaFin — News",
                  "https://www.bafin.de/EN/PublikationenDaten/PublikationenDaten_node.html", "rss", "DE", "en",
                  "free", "ad_hoc", REGULATORY, 0.95, 0,
                  "German financial regulator. Enforcement + rule-making. DGAP covers issuer disclosures; BaFin covers the regulator side."),
        ],

        # -------------------------------------------------------------------
        # FR (132) — Les Echos + AMF already in universal
        # -------------------------------------------------------------------
        "fr": [
            entry("geo.fr.lagefi", "L'Agefi",
                  "https://www.agefi.fr/", "scraper", "FR", "fr",
                  "paid", "daily", CORP + ["sell_side_rating_change"], 0.85, -30,
                  "Paris finance-industry daily. Peers to Les Echos on banking + markets."),
            entry("geo.fr.le_figaro_economie", "Le Figaro — Économie",
                  "https://www.lefigaro.fr/economie", "rss", "FR", "fr",
                  "paid", "daily", CORP + MACRO, 0.75, 0,
                  "Le Figaro's economy desk."),
            entry("geo.fr.la_tribune", "La Tribune",
                  "https://www.latribune.fr/", "rss", "FR", "fr",
                  "metered", "daily", CORP, 0.75, 0,
                  "French economy-focused online daily."),
            entry("geo.fr.insee_press", "INSEE — Publications",
                  "https://www.insee.fr/en/statistiques", "rss", "FR", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "French statistics institute. CPI, GDP, industrial output."),
            entry("geo.fr.banque_de_france_press", "Banque de France — Press",
                  "https://www.banque-france.fr/en/communique-de-presse", "rss", "FR", "en",
                  "free", "ad_hoc", MACRO + ["sector_regulation"], 0.9, 0,
                  "French central bank. Complements ECB."),
            entry("geo.fr.acpr_news", "ACPR — Autorité de contrôle prudentiel et de résolution",
                  "https://acpr.banque-france.fr/en/news", "rss", "FR", "en",
                  "free", "ad_hoc", REGULATORY, 0.85, 0,
                  "French banking + insurance prudential regulator."),
        ],

        # -------------------------------------------------------------------
        # IL (113)
        # -------------------------------------------------------------------
        "il": [
            entry("geo.il.globes", "Globes (Israeli Business News, English)",
                  "https://en.globes.co.il/en/", "rss", "IL", "en",
                  "metered", "realtime", CORP + ["macro_data_release"], 0.85, -60,
                  "Israeli business daily with English edition. Leads on TASE + Israeli tech."),
            entry("geo.il.calcalist_english", "Calcalist — English edition",
                  "https://www.calcalistech.com/", "rss", "IL", "en",
                  "free", "realtime", CORP, 0.75, 0,
                  "Tech-focused Israeli business publication. Strong on start-ups."),
            entry("geo.il.themarker_english", "TheMarker (Haaretz) — English",
                  "https://www.haaretz.com/israel-news/business", "rss", "IL", "en",
                  "paid", "daily", CORP, 0.7, 0,
                  "Haaretz business desk in English."),
            entry("geo.il.isa_press", "Israel Securities Authority — Press",
                  "https://www.isa.gov.il/sites/ISAEng/", "rss", "IL", "en",
                  "free", "ad_hoc", REGULATORY + DISCLOSURE, 0.9, 0,
                  "Israeli securities regulator."),
            entry("geo.il.tase_maya", "TASE Maya — Disclosure portal",
                  "https://maya.tase.co.il/", "scraper", "IL", "en",
                  "free", "realtime", DISCLOSURE + CORP, 1.0, 0,
                  "Tel Aviv Stock Exchange mandatory disclosure portal. Issuer-level filings."),
            entry("geo.il.cbs_israel", "Central Bureau of Statistics (Israel)",
                  "https://www.cbs.gov.il/en/", "rss", "IL", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, trade balance."),
            entry("geo.il.boi_press", "Bank of Israel — Press Releases",
                  "https://www.boi.org.il/en/communication-and-publications/press-releases/", "rss", "IL", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Israeli central bank."),
        ],

        # -------------------------------------------------------------------
        # CH (107)
        # -------------------------------------------------------------------
        "ch": [
            entry("geo.ch.nzz", "Neue Zürcher Zeitung",
                  "https://www.nzz.ch/", "rss", "CH", "de",
                  "paid", "daily", CORP + MACRO, 0.8, -30,
                  "Zurich-based Swiss daily. Serious economics + business desk."),
            entry("geo.ch.finanz_und_wirtschaft", "Finanz und Wirtschaft",
                  "https://www.fuw.ch/", "scraper", "CH", "de",
                  "paid", "daily", CORP + ["sell_side_rating_change"], 0.85, -30,
                  "Swiss specialist finance weekly + online. Strong on SIX-listed."),
            entry("geo.ch.letemps_economie", "Le Temps — Économie",
                  "https://www.letemps.ch/economie", "rss", "CH", "fr",
                  "paid", "daily", CORP, 0.75, 0,
                  "Swiss French-language daily business desk."),
            entry("geo.ch.bfs_releases", "Swiss Federal Statistical Office — Releases",
                  "https://www.bfs.admin.ch/bfs/en/home/news.html", "rss", "CH", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, trade."),
            entry("geo.ch.snb_press", "Swiss National Bank — Press",
                  "https://www.snb.ch/en/iabout/pub/media/id/pub_media", "rss", "CH", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Swiss central bank. Rate + FX policy."),
            entry("geo.ch.finma_news", "FINMA — News",
                  "https://www.finma.ch/en/news/", "rss", "CH", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Swiss financial market supervisor."),
        ],

        # -------------------------------------------------------------------
        # IT (86) — Borsa Italiana already in universal
        # -------------------------------------------------------------------
        "it": [
            entry("geo.it.il_sole_24_ore", "Il Sole 24 Ore",
                  "https://www.ilsole24ore.com/", "rss", "IT", "it",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -30,
                  "Italy's leading business daily. Confindustria-owned. Leads English wires on Italian corporates."),
            entry("geo.it.milano_finanza", "Milano Finanza (MF)",
                  "https://www.milanofinanza.it/", "scraper", "IT", "it",
                  "paid", "daily", CORP, 0.8, -15,
                  "Specialist finance daily. Strong on M&A scoops."),
            entry("geo.it.la_repubblica_affari", "La Repubblica — Affari&Finanza",
                  "https://www.repubblica.it/economia/", "rss", "IT", "it",
                  "metered", "daily", CORP, 0.7, 0,
                  "Repubblica's economy desk."),
            entry("geo.it.consob_news", "Consob — Comunicati Stampa",
                  "https://www.consob.it/web/consob/news-and-media", "rss", "IT", "it",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Italian securities regulator. Enforcement + rule updates."),
            entry("geo.it.istat_releases", "Istat — Releases",
                  "https://www.istat.it/en/press-releases", "rss", "IT", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Italian statistics office. CPI, GDP, industrial production."),
            entry("geo.it.banca_italia_press", "Banca d'Italia — Press",
                  "https://www.bancaditalia.it/media/comunicati/index.html", "rss", "IT", "it",
                  "free", "ad_hoc", MACRO + ["sector_regulation"], 0.9, -15,
                  "Italian central bank."),
        ],

        # -------------------------------------------------------------------
        # HK (85) — HKEX already in universal
        # -------------------------------------------------------------------
        "hk": [
            entry("geo.hk.hkej", "Hong Kong Economic Journal (信報)",
                  "https://www.hkej.com/", "scraper", "HK", "zh",
                  "paid", "daily", CORP + MACRO, 0.85, -60,
                  "Chinese-language HK business daily. Leads on HK-listed Chinese companies."),
            entry("geo.hk.am730", "AM730 — Business",
                  "https://www.am730.com.hk/", "rss", "HK", "zh",
                  "free", "daily", CORP, 0.7, 0,
                  "HK free paper with business desk."),
            entry("geo.hk.sfc_news", "Securities and Futures Commission — News",
                  "https://www.sfc.hk/en/News-and-announcements", "rss", "HK", "en",
                  "free", "ad_hoc", REGULATORY + ["investigation_disclosure"], 0.9, 0,
                  "HK securities regulator."),
            entry("geo.hk.hkma_press", "Hong Kong Monetary Authority — Press",
                  "https://www.hkma.gov.hk/eng/news-and-media/press-releases/", "rss", "HK", "en",
                  "free", "ad_hoc", MACRO + ["sector_regulation"], 0.9, 0,
                  "HK monetary authority. FX peg management, banking supervision."),
            entry("geo.hk.census_stats", "Census and Statistics Department (HK)",
                  "https://www.censtatd.gov.hk/en/", "rss", "HK", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, trade."),
        ],

        # -------------------------------------------------------------------
        # SG (70)
        # -------------------------------------------------------------------
        "sg": [
            entry("geo.sg.business_times", "The Business Times (Singapore)",
                  "https://www.businesstimes.com.sg/", "rss", "SG", "en",
                  "paid", "realtime", CORP + ["macro_data_release"], 0.9, 0,
                  "SPH-owned business daily. Leads on SGX corporate stories."),
            entry("geo.sg.straits_times_business", "The Straits Times — Business",
                  "https://www.straitstimes.com/business", "rss", "SG", "en",
                  "paid", "realtime", CORP, 0.8, 0,
                  "SPH broadsheet business section."),
            entry("geo.sg.sgx_announcements", "SGX — Company Announcements",
                  "https://www.sgx.com/securities/company-announcements", "scraper", "SG", "en",
                  "free", "realtime", DISCLOSURE + CORP, 1.0, 0,
                  "Singapore Exchange mandatory disclosure portal."),
            entry("geo.sg.mas_news", "Monetary Authority of Singapore — News",
                  "https://www.mas.gov.sg/news", "rss", "SG", "en",
                  "free", "ad_hoc", MACRO + REGULATORY, 0.95, 0,
                  "Singapore's central bank + financial regulator (dual role)."),
            entry("geo.sg.singstat_releases", "Department of Statistics Singapore — News",
                  "https://www.singstat.gov.sg/whats-new", "rss", "SG", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, manufacturing output."),
        ],

        # -------------------------------------------------------------------
        # NO (55)
        # -------------------------------------------------------------------
        "no": [
            entry("geo.no.dagens_naeringsliv", "Dagens Næringsliv (DN)",
                  "https://www.dn.no/", "scraper", "NO", "no",
                  "paid", "realtime", CORP + ["reserve_update"], 0.9, -60,
                  "Norway's business daily. Strong on oil + gas + shipping."),
            entry("geo.no.finansavisen", "Finansavisen",
                  "https://www.finansavisen.no/", "scraper", "NO", "no",
                  "paid", "daily", CORP + ["sell_side_rating_change"], 0.8, -15,
                  "Finance-focused Norwegian daily."),
            entry("geo.no.e24", "E24 — VG Business",
                  "https://e24.no/", "rss", "NO", "no",
                  "free", "realtime", CORP + MACRO, 0.75, 0,
                  "VG Nett business portal. Free."),
            entry("geo.no.obx_announcements", "Oslo Børs — News",
                  "https://newsweb.oslobors.no/", "scraper", "NO", "en",
                  "free", "realtime", DISCLOSURE + CORP, 1.0, 0,
                  "Oslo Børs mandatory disclosure portal (newsweb)."),
            entry("geo.no.ssb_releases", "Statistics Norway (SSB) — Latest",
                  "https://www.ssb.no/en/", "rss", "NO", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, oil production stats."),
            entry("geo.no.norges_bank_press", "Norges Bank — Press Releases",
                  "https://www.norges-bank.no/en/news-events/news-publications/Press-releases/", "rss", "NO", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Norwegian central bank."),
        ],

        # -------------------------------------------------------------------
        # NL (53)
        # -------------------------------------------------------------------
        "nl": [
            entry("geo.nl.fd", "Het Financieele Dagblad (FD)",
                  "https://fd.nl/", "scraper", "NL", "nl",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -30,
                  "Dutch business daily. Leads English wires on AEX corporates."),
            entry("geo.nl.nrc_economie", "NRC Handelsblad — Economie",
                  "https://www.nrc.nl/sectie/economie/", "rss", "NL", "nl",
                  "paid", "daily", CORP + MACRO, 0.75, 0,
                  "NRC business section."),
            entry("geo.nl.afm_news", "AFM — News",
                  "https://www.afm.nl/en/over-de-afm/actueel", "rss", "NL", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Dutch financial markets authority."),
            entry("geo.nl.cbs_releases", "CBS Netherlands — News",
                  "https://www.cbs.nl/en-gb/news", "rss", "NL", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, industrial production."),
            entry("geo.nl.dnb_press", "De Nederlandsche Bank — Press",
                  "https://www.dnb.nl/en/news-items/press-releases/", "rss", "NL", "en",
                  "free", "ad_hoc", MACRO + REGULATORY, 0.9, 0,
                  "Dutch central bank + prudential regulator."),
        ],

        # -------------------------------------------------------------------
        # ES (51)  — CNMV already in universal
        # -------------------------------------------------------------------
        "es": [
            entry("geo.es.expansion", "Expansión",
                  "https://www.expansion.com/", "rss", "ES", "es",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -30,
                  "Spain's leading business daily. Leads English wires on IBEX 35."),
            entry("geo.es.cinco_dias", "Cinco Días",
                  "https://cincodias.elpais.com/", "rss", "ES", "es",
                  "paid", "daily", CORP, 0.8, -15,
                  "PRISA business daily. Complements Expansión."),
            entry("geo.es.el_economista", "El Economista",
                  "https://www.eleconomista.es/", "rss", "ES", "es",
                  "metered", "daily", CORP + MACRO, 0.75, 0,
                  "Independent business daily."),
            entry("geo.es.bolsa_madrid_hechos", "Bolsa de Madrid — Hechos Relevantes",
                  "https://www.bolsamadrid.es/esp/aspx/Empresas/Empresas.aspx", "scraper", "ES", "es",
                  "free", "realtime", DISCLOSURE + CORP, 1.0, -60,
                  "Bolsa de Madrid issuer disclosure portal."),
            entry("geo.es.ine_releases", "INE — Statistics Spain",
                  "https://www.ine.es/en/prensa/calendario_en.htm", "rss", "ES", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Instituto Nacional de Estadística."),
            entry("geo.es.bde_press", "Banco de España — Press",
                  "https://www.bde.es/wbe/en/sala-prensa/notas-informativas/", "rss", "ES", "en",
                  "free", "ad_hoc", MACRO + REGULATORY, 0.9, 0,
                  "Spanish central bank."),
        ],

        # -------------------------------------------------------------------
        # DK (40)
        # -------------------------------------------------------------------
        "dk": [
            entry("geo.dk.borsen", "Børsen",
                  "https://borsen.dk/", "scraper", "DK", "da",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -45,
                  "Denmark's business daily. Leads English wires on OMX Copenhagen 25."),
            entry("geo.dk.finans_dk", "Finans (Jyllands-Posten)",
                  "https://finans.dk/", "rss", "DK", "da",
                  "paid", "daily", CORP, 0.8, -15,
                  "Jyllands-Posten business vertical."),
            entry("geo.dk.dfsa_press", "Finanstilsynet — Press",
                  "https://www.dfsa.dk/news", "rss", "DK", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Danish FSA."),
            entry("geo.dk.dst_news", "Statistics Denmark — News",
                  "https://www.dst.dk/en/Statistik/nyheder-analyser-publ", "rss", "DK", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP."),
            entry("geo.dk.nationalbanken_press", "Danmarks Nationalbank — Press",
                  "https://www.nationalbanken.dk/en/news/", "rss", "DK", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Danish central bank (manages DKK-EUR peg)."),
        ],

        # -------------------------------------------------------------------
        # BE (39)
        # -------------------------------------------------------------------
        "be": [
            entry("geo.be.de_tijd", "De Tijd",
                  "https://www.tijd.be/", "rss", "BE", "nl",
                  "paid", "realtime", CORP + MACRO, 0.85, -30,
                  "Flemish-language business daily. Leads English wires on BEL 20."),
            entry("geo.be.lecho", "L'Echo",
                  "https://www.lecho.be/", "rss", "BE", "fr",
                  "paid", "realtime", CORP + MACRO, 0.85, -30,
                  "French-language Belgian business daily, sister of De Tijd."),
            entry("geo.be.fsma_press", "FSMA Belgium — Press",
                  "https://www.fsma.be/en/news", "rss", "BE", "en",
                  "free", "ad_hoc", REGULATORY + DISCLOSURE, 0.9, 0,
                  "Belgian financial services authority."),
            entry("geo.be.statbel_releases", "Statbel — News",
                  "https://statbel.fgov.be/en/news", "rss", "BE", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Belgian stats office. CPI, GDP."),
            entry("geo.be.nbb_press", "National Bank of Belgium — Press",
                  "https://www.nbb.be/en/news", "rss", "BE", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Belgian central bank."),
        ],

        # -------------------------------------------------------------------
        # FI (35)
        # -------------------------------------------------------------------
        "fi": [
            entry("geo.fi.kauppalehti", "Kauppalehti",
                  "https://www.kauppalehti.fi/", "scraper", "FI", "fi",
                  "paid", "realtime", CORP + ["sell_side_rating_change"], 0.9, -45,
                  "Finland's business daily. Leads English wires on OMX Helsinki 25."),
            entry("geo.fi.helsingin_sanomat_talous", "Helsingin Sanomat — Talous",
                  "https://www.hs.fi/talous/", "rss", "FI", "fi",
                  "paid", "daily", CORP + MACRO, 0.75, 0,
                  "HS business section."),
            entry("geo.fi.fin_fsa_news", "Finanssivalvonta (FIN-FSA) — News",
                  "https://www.finanssivalvonta.fi/en/news/", "rss", "FI", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Finnish FSA."),
            entry("geo.fi.stat_fi_releases", "Statistics Finland — Releases",
                  "https://www.stat.fi/index_en.html", "rss", "FI", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP."),
            entry("geo.fi.suomen_pankki_press", "Bank of Finland — Press",
                  "https://www.suomenpankki.fi/en/media-and-publications/releases/", "rss", "FI", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Finnish central bank."),
        ],

        # -------------------------------------------------------------------
        # AT (19)
        # -------------------------------------------------------------------
        "at": [
            entry("geo.at.der_standard_wirtschaft", "Der Standard — Wirtschaft",
                  "https://www.derstandard.at/wirtschaft", "rss", "AT", "de",
                  "metered", "daily", CORP + MACRO, 0.8, -15,
                  "Austrian paper, strongest business desk in Vienna."),
            entry("geo.at.wiener_zeitung_wirtschaft", "Wiener Zeitung — Wirtschaft",
                  "https://www.wienerzeitung.at/nachrichten/wirtschaft/", "rss", "AT", "de",
                  "free", "daily", CORP, 0.7, 0,
                  "Austrian official gazette + business news."),
            entry("geo.at.fma_news", "FMA Austria — News",
                  "https://www.fma.gv.at/en/news/", "rss", "AT", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "Austrian financial market authority."),
            entry("geo.at.statistik_austria_press", "Statistik Austria — Press",
                  "https://www.statistik.at/en/news", "rss", "AT", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Austrian stats. CPI, GDP."),
            entry("geo.at.oenb_press", "Oesterreichische Nationalbank — Press",
                  "https://www.oenb.at/en/Press.html", "rss", "AT", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Austrian central bank."),
        ],

        # -------------------------------------------------------------------
        # PT (13)
        # -------------------------------------------------------------------
        "pt": [
            entry("geo.pt.jornal_negocios", "Jornal de Negócios",
                  "https://www.jornaldenegocios.pt/", "rss", "PT", "pt",
                  "paid", "daily", CORP + MACRO, 0.85, -30,
                  "Portuguese business daily. Leads English wires on PSI-20."),
            entry("geo.pt.eco_pt", "ECO — Economia Online",
                  "https://eco.sapo.pt/", "rss", "PT", "pt",
                  "free", "realtime", CORP + MACRO, 0.75, 0,
                  "Portuguese digital economy daily."),
            entry("geo.pt.cmvm_news", "CMVM — News",
                  "https://www.cmvm.pt/en/", "rss", "PT", "en",
                  "free", "ad_hoc", REGULATORY + DISCLOSURE, 0.9, 0,
                  "Portuguese securities regulator."),
            entry("geo.pt.ine_portugal_releases", "INE Portugal — Destaques",
                  "https://www.ine.pt/xportal/xmain?xpgid=ine_main&xpid=INE", "rss", "PT", "pt",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Portuguese stats. CPI, GDP."),
        ],

        # -------------------------------------------------------------------
        # NZ (13)
        # -------------------------------------------------------------------
        "nz": [
            entry("geo.nz.nzherald_business", "NZ Herald — Business",
                  "https://www.nzherald.co.nz/business/", "rss", "NZ", "en",
                  "paid", "realtime", CORP, 0.8, 0,
                  "New Zealand's leading broadsheet business desk."),
            entry("geo.nz.businessdesk", "BusinessDesk NZ",
                  "https://businessdesk.co.nz/", "scraper", "NZ", "en",
                  "paid", "daily", CORP, 0.85, -15,
                  "NZX-focused business daily."),
            entry("geo.nz.nzx_announcements", "NZX — Company Announcements",
                  "https://www.nzx.com/markets/NZSX", "scraper", "NZ", "en",
                  "free", "realtime", DISCLOSURE + CORP, 1.0, 0,
                  "NZX mandatory disclosure."),
            entry("geo.nz.rbnz_news", "Reserve Bank of New Zealand — News",
                  "https://www.rbnz.govt.nz/hub/news", "rss", "NZ", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "NZ central bank."),
            entry("geo.nz.stats_nz", "Stats NZ — Information Releases",
                  "https://www.stats.govt.nz/information-releases/", "rss", "NZ", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP."),
        ],

        # -------------------------------------------------------------------
        # CA (12) — SEDAR+ already in universal
        # -------------------------------------------------------------------
        "ca": [
            entry("geo.ca.financial_post", "Financial Post (National Post)",
                  "https://financialpost.com/", "rss", "CA", "en",
                  "metered", "realtime", CORP, 0.8, 0,
                  "Postmedia business portal. Complements Globe and Mail ROB."),
            entry("geo.ca.stockhouse", "Stockhouse",
                  "https://stockhouse.com/", "scraper", "CA", "en",
                  "free", "realtime", CORP + ["reserve_update"], 0.55, 0,
                  "Retail-focused Canadian small-cap coverage. Strong on junior miners."),
            entry("geo.ca.statcan_releases", "Statistics Canada — The Daily",
                  "https://www150.statcan.gc.ca/n1/dai-quo/index-eng.htm", "rss", "CA", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP, employment."),
            entry("geo.ca.boc_news", "Bank of Canada — News",
                  "https://www.bankofcanada.ca/press/", "rss", "CA", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Canadian central bank."),
            entry("geo.ca.osc_news", "Ontario Securities Commission — News",
                  "https://www.osc.ca/en/news-events", "rss", "CA", "en",
                  "free", "ad_hoc", REGULATORY, 0.9, 0,
                  "OSC is the largest CSA regulator."),
        ],

        # -------------------------------------------------------------------
        # IE (11)
        # -------------------------------------------------------------------
        "ie": [
            entry("geo.ie.irish_times_business", "Irish Times — Business",
                  "https://www.irishtimes.com/business/", "rss", "IE", "en",
                  "paid", "daily", CORP + MACRO, 0.85, 0,
                  "Ireland's leading broadsheet business desk."),
            entry("geo.ie.independent_business", "Irish Independent — Business",
                  "https://www.independent.ie/business/", "rss", "IE", "en",
                  "metered", "daily", CORP, 0.75, 0,
                  "Independent News & Media business section."),
            entry("geo.ie.cbi_press", "Central Bank of Ireland — Press",
                  "https://www.centralbank.ie/news", "rss", "IE", "en",
                  "free", "ad_hoc", MACRO + REGULATORY, 0.9, 0,
                  "Irish central bank + regulator. Important for Dublin-domiciled funds."),
            entry("geo.ie.cso_releases", "Central Statistics Office Ireland",
                  "https://www.cso.ie/en/", "rss", "IE", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "CPI, GDP — Irish GDP distorted by multinational tax domiciling, use with care."),
        ],

        # -------------------------------------------------------------------
        # BR (5) — already have CVM + Agência Estado + Valor in universal
        # -------------------------------------------------------------------
        "br": [
            entry("geo.br.infomoney", "InfoMoney",
                  "https://www.infomoney.com.br/", "rss", "BR", "pt",
                  "free", "realtime", CORP + MACRO, 0.75, -30,
                  "Brazilian retail-focused business portal."),
            entry("geo.br.b3_news", "B3 — News",
                  "https://www.b3.com.br/en_us/news/", "scraper", "BR", "en",
                  "free", "daily", DISCLOSURE + CORP, 0.9, 0,
                  "Brazilian exchange (B3 SA). Listing changes, circuit breakers."),
            entry("geo.br.ibge_releases", "IBGE — Indicators",
                  "https://www.ibge.gov.br/en/home-eng.html", "rss", "BR", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Brazilian stats. CPI (IPCA), GDP, employment."),
            entry("geo.br.bcb_press", "Banco Central do Brasil — Press",
                  "https://www.bcb.gov.br/en/#press", "rss", "BR", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "Brazilian central bank. Selic rate decisions."),
        ],

        # -------------------------------------------------------------------
        # BM (5) — Bermuda, minimal
        # -------------------------------------------------------------------
        "bm": [
            entry("geo.bm.bsx_announcements", "Bermuda Stock Exchange — Announcements",
                  "https://www.bsx.com/market-announcements/", "scraper", "BM", "en",
                  "free", "daily", DISCLOSURE, 0.85, 0,
                  "BSX listed-issuer announcements. Many Bermuda-domiciled issuers primary-list elsewhere (NYSE, LSE) — this is the secondary disclosure venue."),
            entry("geo.bm.bma_news", "Bermuda Monetary Authority — News",
                  "https://www.bma.bm/news", "rss", "BM", "en",
                  "free", "ad_hoc", REGULATORY, 0.8, 0,
                  "Bermuda financial regulator. Important for re/insurance."),
        ],

        # -------------------------------------------------------------------
        # MC (3) — Monaco, minimal
        # -------------------------------------------------------------------
        "mc": [
            entry("geo.mc.monaco_economie", "Gouvernement Princier — Economie",
                  "https://www.gouv.mc/Action-Gouvernementale/Economie-et-Finances", "scraper", "MC", "fr",
                  "free", "ad_hoc", MACRO, 0.75, 0,
                  "Monaco government economy portal. Minimal coverage — most Monaco listings dual-list in FR/IT/CH."),
        ],

        # -------------------------------------------------------------------
        # PA (1) — Panama, minimal
        # -------------------------------------------------------------------
        "pa": [
            entry("geo.pa.smv_panama", "SMV Panamá — Comunicados",
                  "https://www.supervalores.gob.pa/", "scraper", "PA", "es",
                  "free", "ad_hoc", REGULATORY + DISCLOSURE, 0.85, 0,
                  "Superintendencia del Mercado de Valores. Panamanian securities regulator."),
        ],

        # -------------------------------------------------------------------
        # GR (1) — Greece, minimal
        # -------------------------------------------------------------------
        "gr": [
            entry("geo.gr.ase_announcements", "Athens Stock Exchange — Announcements",
                  "https://www.athexgroup.gr/en/web/guest/company-announcements", "scraper", "GR", "en",
                  "free", "realtime", DISCLOSURE + CORP, 0.95, 0,
                  "ATHEX corporate disclosure."),
            entry("geo.gr.hcmc_news", "Hellenic Capital Market Commission — News",
                  "https://www.hcmc.gr/en/", "rss", "GR", "en",
                  "free", "ad_hoc", REGULATORY, 0.85, 0,
                  "Greek securities regulator."),
        ],

        # -------------------------------------------------------------------
        # PE (1) — Peru, minimal
        # -------------------------------------------------------------------
        "pe": [
            entry("geo.pe.smv_peru", "SMV Perú — Comunicados",
                  "https://www.smv.gob.pe/", "scraper", "PE", "es",
                  "free", "ad_hoc", REGULATORY + DISCLOSURE, 0.85, 0,
                  "Superintendencia del Mercado de Valores de Perú."),
            entry("geo.pe.bvl_announcements", "Bolsa de Valores de Lima — Noticias",
                  "https://www.bvl.com.pe/", "scraper", "PE", "es",
                  "free", "daily", DISCLOSURE, 0.85, 0,
                  "Lima Stock Exchange."),
        ],

        # -------------------------------------------------------------------
        # ZA (1) — South Africa, minimal
        # -------------------------------------------------------------------
        "za": [
            entry("geo.za.business_day", "Business Day (SA)",
                  "https://www.businesslive.co.za/bd/", "rss", "ZA", "en",
                  "paid", "realtime", CORP + MACRO, 0.85, 0,
                  "South Africa's business daily."),
            entry("geo.za.jse_sens", "JSE SENS — Stock Exchange News Service",
                  "https://www.jse.co.za/news", "scraper", "ZA", "en",
                  "free", "realtime", DISCLOSURE + CORP, 1.0, 0,
                  "JSE issuer disclosure portal."),
            entry("geo.za.fsca_news", "Financial Sector Conduct Authority — News",
                  "https://www.fsca.co.za/News%20Documents/Forms/AllItems.aspx", "scraper", "ZA", "en",
                  "free", "ad_hoc", REGULATORY, 0.85, 0,
                  "SA financial conduct regulator."),
            entry("geo.za.sarb_press", "South African Reserve Bank — Publications",
                  "https://www.resbank.co.za/en/home/publications/news", "rss", "ZA", "en",
                  "free", "ad_hoc", MACRO, 1.0, 0,
                  "SA central bank."),
        ],
    }


def main() -> int:
    data = seed()
    written = 0
    for cc, entries in data.items():
        if not entries:
            continue
        path = GEO_DIR / f"{cc}.yaml"
        dump_yaml(path, sorted(entries, key=lambda e: e["source_id"]))
        print(f"  wrote {path.name}  entries={len(entries)}")
        written += 1
    print(f"wrote {written} geo files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
