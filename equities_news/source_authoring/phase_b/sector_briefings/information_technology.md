# Sector Briefing — Information Technology

**Agent:** `phase_b_information_technology_opus`
**Sector YAML:** `registry/news_sources/sectors/information_technology.yaml`
**Sub-industry dir:** `registry/news_sources/sub_industries/information_technology/`
**Scope:** 559 symbols, 19 countries, 41.28% combined weight — dominated by US mega-caps (NVDA, AAPL, MSFT, GOOGL, AMZN*, META*) plus Japanese (Sony, Tokyo Electron, Keyence), Dutch (ASML), Taiwanese ADRs (TSM), German (SAP, Infineon), Israeli tech (Check Point, Cyberark).

*AMZN and META are Consumer Discretionary and Communication Services respectively under GICS; don't expect them here.

## Dominant sub-industries

GICS sub-industry slugs (authoritative):
- `semiconductors`  — fabless (NVDA, AMD, QCOM, AVGO) + IDM (INTC, TXN, MU) + equipment (ASML, AMAT, LRCX, KLAC, TEL, SCREEN)
- `software_application`  — ERP/CRM (CRM, ADBE, NOW, INTU, SAP), industrial/design (ANSS, CDNS, SNPS)
- `software_systems`  — OS/infra (MSFT, ORCL, VMW), cybersecurity (PANW, CRWD, ZS, S, FTNT, CHKP)
- `it_services`  — staff-heavy integrators (ACN, IBM, INFY, WIT, TCS, CTSH, GIB, DXC, CGI)
- `tech_hardware_storage`  — servers/networking (DELL, HPE, NTAP), PCs (HPQ), printers/imaging (XRX, CAJ, RICOH)
- `electronic_equipment_instruments`  — test/measurement (KEYS, TDY, FTV), connectors (APH, TEL), Japanese precision (KYCCF, 6981 Murata, 6857 Advantest)
- `communications_equipment`  — CSCO, JNPR, NOK, ERIC, ANET, MSI, HIK, IIVI

## Sub-industries warranting override

- `semiconductors.fabless` vs `semiconductors.equipment` vs `semiconductors.foundry_idm` — three distinct news diets. Fabless reads TrendForce, DIGITIMES, ChannelE2E. Equipment reads VLSIresearch. Foundry reads TSMC investor day transcripts + Taiwan Economic News.
- `cybersecurity` — dedicated trade press (The Record by Recorded Future, BleepingComputer, KrebsOnSecurity, Risky.Biz, TheCyberWire), conference-driven (RSA Conf, Black Hat, DEF CON).
- `ai_ml_infrastructure` — not strictly a GICS sub-industry but behaviourally distinct (HuggingFace releases, arXiv, Stratechery, SemiAnalysis).

Recommended to author sub_industry YAMLs for: `semiconductors`, `semiconductors_equipment`, `cybersecurity`, `ai_infrastructure`.

## Canonical sources to include at sector level

**Trade press (English):**
- The Information (theinformation.com) — scoop-driven, paid, strong on enterprise software + semis
- SemiAnalysis (semianalysis.com) — deep semi + AI infra research
- Stratechery (stratechery.com, Ben Thompson) — strategic analysis, widely-read
- TechCrunch (techcrunch.com) — venture + early-stage
- The Register (theregister.com) — enterprise IT, UK-originated
- Protocol/Business Insider Tech — enterprise + consumer tech

**Semiconductor-specialist:**
- DIGITIMES (digitimes.com) — Taiwan-based, supply chain scoops
- EE Times (eetimes.com)
- IEEE Spectrum (spectrum.ieee.org)
- VLSIresearch reports (vlsiresearch.com, paid)
- TrendForce (trendforce.com) — memory + display cycle

**Software-specialist:**
- CRN (crn.com) — channel news
- The New Stack (thenewstack.io) — cloud-native/DevOps
- InfoQ (infoq.com) — architecture

**Cybersecurity-specialist (if not splitting to sub_industry):**
- KrebsOnSecurity (krebsonsecurity.com)
- BleepingComputer (bleepingcomputer.com)
- The Record (therecord.media)
- SecurityWeek (securityweek.com)
- Risky Business podcast (risky.biz)

**Conferences / embargoed drops:**
- Apple events (apple.com/newsroom)
- Google I/O (blog.google)
- Microsoft Ignite (news.microsoft.com)
- Nvidia GTC (blogs.nvidia.com)
- WWDC, Build, re:Invent, CES

**Analyst/research houses:**
- Gartner press releases (gartner.com/en/newsroom)
- IDC press releases (idc.com/getdoc.jsp?containerId=prUS...)
- Forrester (forrester.com/press-room)
- Canalys (canalys.com/newsroom)

## Common pitfalls

- **NVDA/AMD/INTC moves on GPU channel data, not SEC filings.** JPR + Steam Survey quarterly data moves the group. Add channel-data specialists under sub_industry.semiconductors.
- **ASML and TSMC follow each other.** ASML earnings guide TSMC sentiment; TSMC monthly revenue release is a market event. Add `geo.tw.tsmc_monthly_revenue` as sub_industry.semiconductors source (TW geo doesn't exist yet in Phase A — flag for Phase A patch if needed, otherwise scope under sub_industry.semiconductors.tw_stats).
- **Japanese tech hardware files via TDnet, but most commentary is in JP-language Nikkei Electronics + EE Times Japan.** Don't overfit to English-only sources for names like 6981 Murata, 6857 Advantest.
- **Israeli cyber names dual-list (US + TASE).** News leads from Israeli sources sometimes (Globes), sometimes SEC 6-K (which is late). Both are already in universal/geo.
- **Founder-CEO X accounts move markets.** Satya Nadella, Sundar Pichai, Jensen Huang, Elon Musk. Collect Official_X_Handle diligently for top 50 mega/large IT names. Retail often trades on Satya's Ignite tweets before the official press drop.
- **LinkedIn slugs often different from the public name.** `linkedin.com/company/nvidia` works but `linkedin.com/company/alphabet` resolves to a redirect. Always HTTP HEAD verify.
- **IR pages at large IT firms use three-letter subdomain convention:** `investor.microsoft.com`, `investor.nvidia.com`, `ir.apple.com`, `abc.xyz/investor`. This heuristic works for ~70% of US mega-cap IT.

## Language / jurisdiction specifics

- **JP IT names** (Sony, Keyence, Fanuc, Nintendo) file TDnet first (JP-language, ~3h lead). The English-language press release on their own IR site typically lags by 2-4h. Prioritise TDnet for earnings prints.
- **Taiwan ADRs** (TSM, UMC, ASX) report to the Taiwan Stock Exchange first (MOPS), then file 6-K to SEC. TWSE MOPS not yet in universal/geo (Taiwan not in Bible universe but Taiwan-listed ADRs are). Sub-industry sources should fill this gap.
- **Indian IT services** (INFY, WIT, TCS) report to BSE/NSE first. Bombay Stock Exchange announcements portal (bseindia.com/corporates) is the primary filing; US 6-K trails by hours.
- **Chinese tech names** in the Bible universe appear as HK-listed or ADR; primary filings on CNINFO (Chinese-language) + HKEX (English).
- **Israeli security names** — press releases in English but Israeli news coverage often first from Globes.

## Hardware + workspace-mining extras

When extracting sources from existing lists/research, check:
- `~/Desktop/MCAS/Backend_v5` and related research dirs for any prior IT-sector source catalogues.
- `Global_Equities_Bible_20260420.xlsx` (the per-country variant) — may contain supplementary notes useful for cross-reference.

## Sanity numbers

Expected outputs for this sector:
- sector.information_technology.* entries: **25–40** (heavy sector, well-covered trade press).
- sub_industry overrides: **3–5** (semiconductors, semiconductors_equipment, cybersecurity, optionally ai_infrastructure + it_services).
- Overlay fill rate target: **IR_URL ≥ 95%**, **Press_Release_Feed_URL ≥ 70%** (small/mid caps rarely expose feeds), **Official_X_Handle ≥ 85%**, **Official_LinkedIn_URL ≥ 90%**, **Earnings_Transcript_Provider 100%** (always fillable via heuristic), **Local_Regulatory_Filings_URL ≥ 95%** (SEC/TDnet/others all have per-filer URL patterns).
