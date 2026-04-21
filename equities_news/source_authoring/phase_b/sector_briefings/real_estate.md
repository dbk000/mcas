# Sector Briefing — Real Estate

**Agent:** `phase_b_real_estate_opus`
**Scope:** 387 symbols, 20 countries, 5.38% weight. US REITs (PLD, AMT, EQIX, WELL, SPG, O, CCI, DLR, PSA, AVB, EQR, VTR, IRM, EXR, WY, ARE, CPT, ESS, MAA, HST, SBAC, INVH, AMH, SUI, ELS), REOCs, Japanese J-REITs (3281, 3282, 3283 etc.), UK (LAND.L British Land, BLND.L Sold, SGRO.L Segro, UKCM.L), EU (URW.PA Unibail, KLEP.PA Klepierre, VNA.DE Vonovia, DWNI.DE-merged).

## Dominant sub-industries

- `industrial_reits`  — PLD, DRE (merged), FR, REXR, STAG, TRNO, EGP, LPT, FR, LXP, BXP (offices but…), EQIX (hybrid data center/industrial)
- `data_center_reits`  — EQIX, DLR, AMT (partial)
- `telecom_tower_reits`  — AMT, CCI, SBAC; EU (Cellnex CLNX.MC)
- `retail_reits`  — SPG, O, REG, KIM, FRT, BRX, ADC, STOR, MAC, PECO, ESRT, IVT; EU (URW.PA, KLEP.PA, LMP.L)
- `office_reits`  — BXP, SLG, VNO, HIW, DEI, KRC, ARE (life-sci), CUZ, PDM, WPC (net-lease), OHI (hc), DOC (hc)
- `residential_reits`  — AVB, EQR, CPT, ESS, MAA, UDR, INVH, AMH, SUI, ELS (manuf housing), NXRT, AIV (apt), BRT
- `health_care_reits`  — WELL, VTR, OHI, DOC (Physicians Realty), MPW (Medical Properties), PEAK, HR, LTC, NHI
- `specialized_reits`  — PSA (self-storage), EXR (self-storage), LSI (self-storage), CUBE (self-storage), IRM (storage/records), GLPI (gaming), VICI (gaming), MGP (merged into VICI), UNIT (fiber), SBAC (towers), AMT (towers); EU KNOW (no specific comparable)
- `hotel_lodging_reits`  — HST, PK, RHP, SHO, APLE, PEB, BHR, RLJ, DRH, CDP
- `real_estate_services`  — CBRE, JLL, CWK (Cushman), COLB, NMRK (Newmark), MLEA (Walker & Dunlop)
- `real_estate_operating_companies`  — EXR (classified REIT though), HHH, HWH, WH (Wyndham — discretionary), MG, IRT
- `diversified_reits`  — STWD, BRMK, VNO (partly office)
- `mortgage_reits`  — NLY, AGNC, STWD (overlap), BXMT, ABR, LADR, ARI, TRTX, KREF, RITM (Rithm Capital), MFA
- `infrastructure_reits` (implicit) — AMT, CCI, SBAC overlap; plus MLPs technically Energy

## Sub-industries warranting override

- `data_center_reits` — data center specialists: Data Center Knowledge, DataCenter Dynamics (dcd), Baxtel, hyperscaler capex tracking.
- `telecom_tower_reits` — Wireless Estimator, TowerXchange (paid), cell-tower lease-rate tracker.
- `health_care_reits` — different from sector.health_care.* — driver is skilled-nursing reimbursement (CMS) + MOB tenancy.
- `hotel_lodging_reits` — STR (Smith Travel Research) RevPAR data + ADR data. Hotel News Now.
- `mortgage_reits` — MBS price indices, Fed balance sheet MBS holdings, prepayment models (eMBS, Bloomberg).

Author sub_industry YAMLs for: `data_center_reits`, `telecom_tower_reits`, `health_care_reits`, `hotel_lodging_reits`, `mortgage_reits`.

## Canonical sector-level sources

- NAREIT (reit.com) — industry org, daily news + T-tracker
- GlobeSt (globest.com) — commercial RE daily
- Commercial Observer (commercialobserver.com) — NYC-heavy
- REBusinessOnline (rebusinessonline.com)
- Real Estate Weekly (realestateweekly.com)
- Bisnow (bisnow.com) — city-specific CRE
- The Real Deal (therealdeal.com) — NYC/Miami/LA/Chicago
- Connect Media (connectmedia.com)
- REIT.com daily news (nareit)
- S&P Global Real Estate (spglobal.com/marketintelligence)
- Green Street Advisors (greenstreetadvisors.com, paid but press headlines free)
- Inside Real Estate (insidere.com)
- Hotel News Now (hotelnewsnow.com)
- STR Global (str.com) — RevPAR data
- CoStar / Loopnet press (costargroup.com/news)
- Data Center Dynamics (datacenterdynamics.com)
- Data Center Knowledge (datacenterknowledge.com)
- Baxtel (baxtel.com)
- **FHFA press** (fhfa.gov/Media)
- **HUD press** (hud.gov/press)
- **Fed Senior Loan Officer Survey** (fed Fed Senior Loan Officer Survey)
- **Fed Flow of Funds** (federalreserve.gov/releases/z1)
- **NAR** (National Association of Realtors) monthly existing home sales
- **Census** new residential construction + sales
- **Case-Shiller HPI** (spglobal.com)
- **FHFA HPI** (fhfa.gov/DataTools/Downloads)
- **CoreLogic** housing indices (corelogic.com)
- **RCA MSCI Real Capital Analytics** (rcanalytics.com)

## Common pitfalls

- **REITs are heavily sensitive to 10-year yield.** Not macro news per se, but macro source weighting matters more for this sector than others.
- **Dividend cuts are the single most market-moving event.** Monitor trailing dividend coverage ratio. Most REITs announce quarterly well in advance, but suspensions happen in stress (PEB, APLE during COVID).
- **J-REITs** (Japanese REITs) trade on Tokyo REIT Index (TOPIX). 3281 GLP J-REIT, 3282 Comforia, etc. TDnet + Nikkei REIT.
- **EU REITs** — SIIC (France), OEIC (Netherlands), SOCIMI (Spain) regimes differ. French reportings to AMF; Dutch to AFM.
- **Mortgage REITs are hit-or-miss** — behave more like bond funds. NLY/AGNC move on MBS basis + Fed purchase cadence.
- **Self-storage** — low-beta but demand-driven by mobility + downsizing. SSA (Self Storage Association) quarterly reports.
- **Office REITs are a special case** — WFH/return-to-office trends drive BXP/SLG/VNO. Kastle Systems "Back to Work Barometer" is a tracked proxy.
- **Health care REITs are bifurcated**: MOB (medical office buildings) are stable, skilled-nursing (SNF) are volatile on CMS reimbursement.

## Language / jurisdiction specifics

- **JP** — J-REITs (3281, 3282, 3283, 8951, 8952, 8954, 3283, 3292) — TDnet + Nikkei JP. Monthly occupancy disclosures.
- **DE** — VNA.DE (Vonovia), DWNI.DE (merged), LEG.DE, AROD (TAG Immobilien) — DGAP.
- **FR** — URW.PA (Unibail), KLEP.PA (Klepierre), GFC.PA (Gecina) — AMF + Les Echos. SIIC regime specifics.
- **GB** — LAND.L (British Land), BLND.L (sold), SGRO.L (Segro), DLG.L — RNS.
- **AU** — SCG.AX (Scentre), GPT.AX, DXS.AX, MGR.AX — AFR + ASX.
- **SG** — Capitaland group + Ascendas REIT (A17U.SG), Mapletree (logistics, industrial, Pan-Asia) — SGX announcements. SG is major REIT hub for Asia-Pac.

## Sanity numbers

- sector.real_estate.* entries: **18–28**.
- sub_industry overrides: **4–5**.
- Overlay fill: IR_URL ≥95%, Press_Release_Feed ≥80%, X handle ≥40% (REIT CEOs very quiet), transcript provider 100%, filings URL ≥95%.
