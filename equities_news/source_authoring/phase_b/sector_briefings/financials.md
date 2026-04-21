# Sector Briefing — Financials

**Agent:** `phase_b_financials_opus`
**Scope:** 800 symbols, 26 countries (widest country spread), 36.11% combined weight. US banks (JPM, BAC, WFC, C, GS, MS), regionals (big tail), insurers (BRK.B, AIG, PGR, TRV, MET, PRU, ALL, CB, TFC), asset managers (BLK, BX, KKR, APO, ARES), exchanges (ICE, CME, CBOE, NDAQ, LSEG), Japanese megabanks (MUFG, SMFG, MIZUHO), European banks (HSBA, BARC, LLOY, BNP, GLE, DB, UCG, ISP, SAN, BBVA, SEB, SHB, NDA, ING), insurers (AXA, ALV, ZURN, MUV2, SREN), REITs are separate.

## Dominant sub-industries

- `diversified_banks`  — JPM, BAC, WFC, C, HSBC, BNP, SAN, UBS, CS, DB, ISP, UCG, MUFG 8306, SMFG 8316, MIZUHO 8411, RBC, BNS, TD, CIBC, BMO, CBA.AX, WBC.AX, NAB.AX, ANZ.AX, DBS, OCBC, UOB
- `regional_banks`  — US mid-cap banks: USB, PNC, TFC, MTB, CFG, KEY, RF, HBAN, FITB, CMA, ZION; plus the ~200 smaller US community banks in Russell 2000
- `commercial_finance`  — ALLY, AXP (Amex is Diversified Financial), SYF, COF (cap one)
- `investment_banking_brokerage`  — GS, MS, PJT, HLI, LAZ, EVR, MC (Moelis), PWP (Perella Weinberg), JEF
- `asset_management_custody_banks`  — BLK, TROW, BEN, IVZ, AMG, BK (BNY), STT, NTRS, WETF, VRTS; EU (DWS.DE, ABDN.L, MAN.L, SDR.L)
- `alternative_asset_management`  — BX (Blackstone), KKR, APO (Apollo), ARES, CG (Carlyle), TPG, BAM (Brookfield), OWL (Blue Owl), HLNE, PARS (Patria); EU (EQT.ST, PARTNERS GROUP PGHN.SW, INTERMEDIATE CAP ICP.L)
- `financial_exchanges_data`  — ICE, CME, CBOE, NDAQ, LSEG, DB1.DE, ENX (Euronext), HKEX 0388, JPX 8697, SGX.SG, ASX.AX, MOEX, B3 (BBAS3.SA sector adjacent)
- `insurance_brokers`  — MMC, AON, AJG, WTW, BRO, AOS.AX; Jardine Lloyd Thompson (now MMC), Lockton (private)
- `life_health_insurance`  — MET, PRU, MFC, GWO.TO, SLF.TO, DL (Dai-ichi Life 8750), T&D 8795, NIPPON LIFE (private), GENERALI G.MI, AV.L (Aviva), PRU.L (Prudential UK), MNG.L, ZURN.SW, AXA (CS.PA)
- `property_casualty_insurance`  — TRV, ALL, CB (Chubb), PGR, AIG, HIG, WRB, CNA, RGA, BRK.B (Berkshire — sprawling), FFH.TO (Fairfax), IAG.AX, SUN.AX, QBE.AX; EU (MUV2.DE Munich Re, HNR1.DE Hannover Re, SREN.SW Swiss Re, SCR.PA Scor)
- `reinsurance`  — RNR, ESGR, EG, AXS, ARGO (private now); EU (MUV2, HNR1, SREN, SCR) overlap
- `insurance_brokers` (dup above)
- `multi_line_insurance`  — AIG, HIG, ALLEGHENY Y, ANY MRE
- `consumer_finance`  — SLM, NAVI, SOFI, UPST, LC, LU (Hong Kong), ADS (Bread Financial)
- `mortgage_reits` — Real Estate sector
- `financial_technology` (fintech) — PYPL, COIN, SQ/BLOCK, HOOD, SOFI, TOAST, MELI (hybrid), MQ, ALLY (hybrid); EU (WISE.L, ADYEN.AS)

## Sub-industries warranting override (MANY)

- `diversified_banks` — S&P Global Market Intelligence (S&P CIQ), American Banker, Risk.net, Banking Dive, Fed H.4.1 weekly, FDIC call reports. Major regional regulators: OCC, Fed, FDIC, CFPB.
- `regional_banks` — Community bank news: American Banker, BankDirector, SNL Community Bank (paid).
- `investment_banking_brokerage` — Bloomberg DealBook, NYT DealBook, IFR Asia, Euromoney, The Deal, Institutional Investor, Global Capital.
- `asset_management_custody_banks` — Pensions & Investments, Institutional Investor, Funds Global Asia, Ignites (paid), IPE (pensions), ETF.com.
- `alternative_asset_management` — Private Equity International, PitchBook News, Bloomberg Private Equity, Secondaries Investor, Infrastructure Investor, Hedge Fund Alert.
- `financial_exchanges_data` — Waters Technology, Traders Magazine, Markets Media, FTF News, Risk.net.
- `insurance_life_health` — LifeHealthPro, Think Advisor, Insurance ERM, Annuity Journal.
- `insurance_property_casualty` — Insurance Insider, Reinsurance News, Artemis.bm (catastrophe bonds), A.M. Best press, S&P Insurance ratings, Swiss Re Sigma, Munich Re NatCat.
- `reinsurance` — Artemis.bm, Reinsurance News, Monte Carlo Rendezvous preview, PCI Annual coverage.
- `financial_technology` — TechCrunch Fintech, American Banker Digital, Finextra, PYMNTS, The Block (crypto), CoinDesk, TheBlock, CoinTelegraph.

Author sub_industry YAMLs for AT LEAST: `diversified_banks`, `alternative_asset_management`, `insurance_property_casualty`, `financial_technology`, `asset_management_custody_banks`, `financial_exchanges_data`.

## Canonical sector-level sources

- American Banker (americanbanker.com)
- Banking Dive (bankingdive.com)
- Risk.net (risk.net) — risk + regulation-heavy
- Financial News London (fnlondon.com, paid)
- Euromoney (euromoney.com, paid)
- Institutional Investor (institutionalinvestor.com)
- Reuters Finance Hub (universal)
- Axios Pro Financial Services
- **Fed press releases + H.4.1 + H.8** (federalreserve.gov, universal too)
- **OCC news** (occ.gov/news-issuances)
- **FDIC press + call-report data** (fdic.gov/news)
- **CFPB** (consumerfinance.gov/about-us/newsroom)
- **Treasury / OFR** (home.treasury.gov, financialresearch.gov)
- **Federal Housing Finance Agency** (fhfa.gov/Media)
- **SEC releases** (universal already)
- **ECB Banking Supervision** (bankingsupervision.europa.eu)
- **EBA** (eba.europa.eu/news-press)
- **PRA UK** (bankofengland.co.uk/prudential-regulation, covered via BoE in geo.gb)
- **BIS** (bis.org/press) — Basel standards
- **FSB** (fsb.org/press-releases)
- Pensions & Investments (pionline.com)
- Barron's Financial Advisors (universal)
- The Deal (thedeal.com) — M&A specifics
- DealReporter (paid)
- **Artemis.bm** — catastrophe bonds
- **Insurance Insider** (insuranceinsider.com)
- **A.M. Best ratings** (ambest.com)
- **PitchBook News** (pitchbook.com/news)
- **Preqin Insights** (preqin.com/insights)
- **The Block** (theblock.co) — crypto/fintech
- **CoinDesk** (coindesk.com)
- **Bloomberg Crypto** (universal via Bloomberg)

## Common pitfalls

- **Big banks have OVERLAPPING regulators** — SEC + Fed + OCC + FDIC for US, FCA + PRA for UK. Dedupe by event: Fed FSR is a macro event, OCC enforcement is per-filer.
- **Earnings season is tightly-clustered** — mid-Jan + mid-Apr + mid-Jul + mid-Oct for US. All banks report within ~2 weeks. Pre-announcements via Bloomberg/FT are the real lead.
- **SNL Financial / S&P CIQ** is the gold-standard data vendor but expensive. Many agents need a workaround — FDIC call reports are the free alternative.
- **European banks** — English RNS for dual-listed, but German Sparkassen/Landesbanken + French mutuelles are in Handelsblatt/Les Echos first.
- **Japanese banks** — MUFG 8306, SMFG 8316, MIZUHO 8411 report via TDnet. Nikkei JP leads. Boards approve capital plans in March (fiscal year-end).
- **Asset managers** report AUM monthly** — monthly AUM disclosure is a market event for BLK, TROW, IVZ, BEN. Template: e.g. `www.blackrock.com/corporate/newsroom/press-releases` monthly.
- **Insurance catastrophe events**: hurricane season (Jun-Nov) + California wildfire season (Sep-Dec) + Japanese typhoon season (Aug-Oct) drive P&C / reinsurance. Monitor via Artemis + NOAA + JMA.
- **Fintech / crypto** — SEC enforcement + CFTC enforcement dominate. SEC v. COIN, SEC v. BNBS cases. Follow court docket watches (CourtListener).

## Language / jurisdiction specifics

- **JP** — MUFG/SMFG/MIZUHO/Nomura 8604 etc: TDnet. Ad-hoc disclosure common around rate-decision weeks.
- **DE** — DB (Deutsche Bank), CB (Commerzbank) — DGAP ad-hoc + Handelsblatt lead.
- **CH** — UBSG.SW (UBS, post-CS-merger), ZURN.SW — SIX ad-hoc + NZZ + Finanz und Wirtschaft.
- **FR** — BNP, GLE (SocGen), ACA.PA (Credit Agricole), CS.PA (AXA) — AMF + Les Echos.
- **UK** — HSBA, BARC, LLOY, NWG, STAN, PRU.L, AV.L — RNS + FT + The Times.
- **IT** — ISP (Intesa Sanpaolo), UCG (Unicredit), G.MI (Generali) — Consob + Il Sole 24 Ore lead.
- **ES** — SAN, BBVA, BKT, CABK — CNMV hechos relevantes + Expansión.
- **BR** — ITUB, BBD, BBAS3, B3SA3 — CVM + Valor. B3 itself self-reports under geo.br.
- **IL** — Israeli banks (LUMI, POLI, HAPOAL, DSCT) — TASE Maya. ICL-adjacent.

## Sanity numbers

- sector.financials.* entries: **40–60** (largest of any sector's sector-level catalogue).
- sub_industry overrides: **5–7**.
- Overlay fill: IR_URL ≥95% (banks well-groomed IR), Press_Release_Feed ≥85%, X handle ≥65%, transcript provider 100%, filings URL ≥98% (SEC/FDIC/Fed patterns all known).
