# Sector Briefing — Materials

**Agent:** `phase_b_materials_opus`
**Scope:** 359 symbols, 23 countries, 9.29% weight. Diversified chemicals (LIN, APD, SHW, PPG, ECL, DD, DOW, LYB, CE, IFF, EMN, ALB, FMC, ASH, AXTA), metals/mining (BHP, RIO, NEM, FCX, AA, VALE, GLEN.L, AAL.L), gold miners (NEM, GOLD, AEM, KGC, GFI, HMY, AU, CDE, PAAS), industrial gases (LIN, APD), fertilizers (NTR, MOS, CF, SQM, YAR), steel (NUE, STLD, X, RS, WOR, CMC, CLF, ATI, VMC-related), forest products (WY, PCH, LPX, OSB, CFX), packaging (IP, WRK, PKG, AMCR, SON, SLGN, BERY, CCK).

## Dominant sub-industries

- `diversified_chemicals`  — DD, DOW, LYB, CE, EMN, ASH, FUL, HWKN, OLN, WLK, NEU
- `specialty_chemicals`  — SHW, PPG, ECL, IFF, ALB, FMC, CBT (Cabot), CC (Chemours), AXTA, ESI (Element Solutions), NGVT (Ingevition), HUN (Huntsman), AVTR (Avantor — life-sci instrumentation though), KWR (Quaker)
- `industrial_gases`  — LIN (Linde), APD (Air Products); EU (AI.PA Air Liquide — already French)
- `commodity_chemicals`  — DOW, LYB, WLK, CE, OLN, MEOH; EU (BASF BAS.DE, CVC (Covestro 1COV.DE), LANXESS LXS.DE)
- `fertilizers_agricultural_chemicals`  — NTR (Nutrien), MOS (Mosaic), CF (CF Industries), SQM (Sociedad Quimica), CORTEVA (CTVA); EU (YAR.OL Yara, K+S SDF.DE); Asian (Sinofert, ICL ICL.TA)
- `steel`  — NUE, STLD, X (US Steel), RS (Reliance), WOR (Worthington), CMC (Commercial Metals), CLF (Cleveland-Cliffs), ATI (ATI Metals); EU (MT AMS Steel, SZG.DE Salzgitter, VOE.VI voestalpine); Asian (5401 Nippon Steel, 5411 JFE, 5486 Hitachi Metals)
- `aluminum`  — AA (Alcoa), KALU (Kaiser), CENX (Century); EU (NHY.OL Norsk Hydro); AU (AWAC with Alumina/Alcoa)
- `copper_metals` — FCX (Freeport-McMoRan), SCCO (Southern Copper), ERO (Ero Copper), ANTO.L (Antofagasta), GLEN.L (Glencore), TCK (Teck Resources), HBM.TO (Hudbay), CS.TO (Capstone), MUX (McEwen), LUN.TO (Lundin); CHILE codelco (state-owned)
- `diversified_metals_mining`  — BHP, RIO, VALE, GLEN.L, AAL.L (Anglo American), FCX, MT (ArcelorMittal); AU (S32.AX South32, IGO.AX, OZL.AX)
- `gold`  — NEM, GOLD (Barrick), AEM (Agnico Eagle), KGC, HMY (Harmony), AU (AngloGold), CDE (Coeur), PAAS, WPM (Wheaton Precious Metals — streaming), FNV (Franco-Nevada — streaming), RGLD (Royal Gold — streaming), SSRM, BTG, NG-adj; AU (NCM.AX Newcrest merged, EVN.AX Evolution, NST.AX Northern Star, RRL.AX Regis Resources)
- `silver` — PAAS, HL (Hecla), FSM (Fortuna), CDE (overlap gold), MAG, WPM overlap; AG (First Majestic)
- `precious_metals_minerals`  — NEM, GOLD, AEM — same as gold mostly
- `coal_consumable_fuels` — Energy sector (but AU WHC.AX, NHC.AX, YAL.AX sometimes classified Materials)
- `construction_materials`  — VMC (Vulcan Materials), MLM (Martin Marietta), EXP (Eagle Materials), USCR (now acquired), SUM (Summit Materials), FBIN (Fortune Brands), BECN (Beacon Roofing); EU (HeidelbergCement HEI.DE, HOLN.SW Holcim, CRH CRH.L)
- `paper_forest_products`  — WY (Weyerhaeuser), PCH (Potlatch), LPX (Louisiana-Pacific), OSB (Norbord now merged), CFX, UFPI; Finland (STERV.HE Stora Enso, UPM.HE UPM-Kymmene); Sweden (SCA.ST Svenska Cellulosa, ESSITY.ST Essity — staples though), Billerud BILL.ST
- `containers_packaging`  — IP (International Paper), WRK (WestRock), PKG (Packaging Corp), AMCR (Amcor), SON (Sonoco), SLGN (Silgan), BERY (Berry Global), CCK (Crown Holdings), BLL (Ball), GPK (Graphic Packaging), AVY (Avery Dennison — specialty), SEE (Sealed Air), TSE (Trinseo)
- `metal_glass_plastic_containers`  — BLL, CCK, OI (O-I Glass), AMCR, SON, SLGN, PKG overlap

## Sub-industries warranting override

- `gold` — gold miners have a UNIQUE news diet. LBMA (London Bullion Market) price, COMEX open interest, CFTC COT, World Gold Council, Kitco, Gold Hub, Mining Journal, Mining.com, Northern Miner, Streetwise Reports. Distinct from copper/iron/aluminum.
- `copper_base_metals` — LME (London Metal Exchange) price, SHFE (Shanghai) price, Codelco output, Chinese smelter treatment charges (TC/RCs).
- `iron_ore` — BHP/RIO/VALE — Platts 62% Fe CFR China index, MySteel (Chinese), steelhome.
- `fertilizers` — Green Markets, Argus FMB, Fertilizer Week, FAO food index, Chinese ammonia.
- `steel` — SteelOrbis, American Metal Market, S&P Global Platts Steel, MEPS, China Iron & Steel Assoc.
- `packaging` — Packaging Digest, PaperAge, Fastmarkets RISI.
- `specialty_chemicals` — ICIS, Chemical Week (chemweek.com), Chemical & Engineering News (cen.acs.org), EUWID-ish.

Author sub_industry YAMLs for AT LEAST: `gold`, `copper_base_metals`, `iron_ore`, `fertilizers`, `steel`, `specialty_chemicals`, `packaging`.

## Canonical sector-level sources

- Mining.com (mining.com) — daily mining industry
- Northern Miner (northernminer.com, paid)
- Mining Weekly (miningweekly.com)
- Mining Journal (mining-journal.com, paid)
- S&P Global Market Intelligence — Metals & Mining (paid)
- Reuters Metals & Mining (universal)
- Bloomberg Metals (universal)
- **LME** (lme.com/News) — London Metal Exchange
- **COMEX / CME Metals** (cmegroup.com/markets/metals)
- **SHFE** (shfe.com.cn) — Shanghai futures
- **Gold Hub / World Gold Council** (gold.org)
- **Kitco** (kitco.com)
- Streetwise Reports (streetwisereports.com)
- CRU Group (crugroup.com) — paid, steel/aluminium/fertilizer
- Wood Mackenzie Metals (woodmac.com)
- ICIS (icis.com, paid) — chemicals
- Chemical & Engineering News (cen.acs.org)
- Chemical Week (chemweek.com, paid)
- Fertilizer Week (argusmedia.com)
- Green Markets (fertilizerpricing.com, paid)
- Platts Metals & Steel (spglobal.com/commodityinsights)
- SteelOrbis (steelorbis.com)
- American Metal Market (amm.com, paid, Fastmarkets)
- Fastmarkets (fastmarkets.com) — metals + forest products
- RISI (now Fastmarkets RISI) — pulp + paper
- Packaging Digest (packagingdigest.com)
- PaperAge (paperage.com)
- **USGS Mineral Commodity Summaries** (usgs.gov/centers/nmic)
- **USDA reports for fertiliser demand** (nass.usda.gov)
- **NOAA/USDA crop reports** move NTR/MOS/CF
- **ISM PMI Mfg** (universal) — demand proxy
- **EU ETS carbon price** (ember-climate.org)

## Common pitfalls

- **Commodity prices drive this sector more than earnings.** Tracking spot + futures curves matters as much as SEC filings.
- **Gold miners AISC (all-in sustaining cost)** is the key metric. Reported quarterly + discussed in MD&A.
- **Iron ore Platts 62% Fe CFR China** index (free tier limited, paid required for intraday). Affects BHP/RIO/VALE.
- **Chinese stimulus announcements move all base metals.** Politburo meetings, NDRC announcements, PBOC policy.
- **Fertiliser + food complex**: NTR/MOS/CF move on DAP/MAP/urea prices + agricultural seasonal patterns.
- **Steel earnings are highly levered to HRC (hot-rolled coil) price.** HRC futures on CME.
- **Chemicals highly exposed to naphtha + ethane feedstock.** Crude price + US ethane price (MontBelvieu).
- **Packaging (glass, aluminum cans) exposed to aluminum + silica + energy.**
- **Japanese materials** — 5401 Nippon Steel, 5411 JFE, 5486 Hitachi Metals (now Proterial) — TDnet + Nikkei. Monthly production stats from METI.
- **Australian mining majors** — BHP, RIO, S32 — dual-listed (ASX/LSE). ASX + LSE RNS + AFR.
- **Brazilian VALE, SUZANO, USIM, CSNA3** — CVM + Valor + Agência Estado. Political risk premium high.

## Language / jurisdiction specifics

- **AU** — BHP.AX, RIO.AX, FMG.AX, NST.AX, NCM.AX (merged), S32.AX — ASX + AFR. AU Reserves disclosure (JORC-compliant).
- **GB** — RIO.L, BHP.L (secondary), AAL.L, GLEN.L, ANTO.L, FRES.L (Fresnillo), EVR.L (Evraz delisted post-sanction), POLY.L (Polymetal delisted) — RNS + Mining Journal.
- **CA** — NEM (dual NYSE/TSX), AEM.TO, KGC.TO, TECK.B.TO, FM.TO (First Quantum), NTR.TO (Nutrien), LUN.TO — SEDAR+ + Northern Miner + Globe and Mail ROB. NI 43-101 technical reports are events.
- **BR** — VALE, SUZANO, USIM5, CSNA3 — CVM + Valor.
- **CL** — SQM (Chile lithium + fertilisers) — CMF Chile regulator (outside our 30-country universe; source stub).
- **CN** — Zijin 2899 HK, Jiangxi Copper 358 HK, CHALCO 2600 HK — HKEX + CNINFO.
- **DE** — BAS.DE (BASF), LXS.DE (Lanxess), 1COV.DE (Covestro), BAYN.DE (hybrid with Health Care), HEI.DE (Heidelberg Materials) — DGAP + Handelsblatt.
- **NO** — YAR.OL (Yara fertilisers), NHY.OL (Norsk Hydro aluminium) — OBX newsweb + DN.
- **NL/FI/SE** — STERV.HE, UPM.HE, SCA.ST, BILL.ST — forest products. Finnish + Swedish business dailies.

## Sanity numbers

- sector.materials.* entries: **25–40**.
- sub_industry overrides: **6–8**.
- Overlay fill: IR_URL ≥95%, Press_Release_Feed ≥70%, X handle ≥40% (miners very quiet on X; occasional loud exec), transcript provider 100%, filings URL ≥95%.
