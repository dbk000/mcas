# Sector Briefing — Energy

**Agent:** `phase_b_energy_opus`
**Scope:** 191 symbols, 20 countries, 7.18% weight. US supermajors (XOM, CVX, COP, EOG, OXY, PSX, VLO, MPC, HES, FANG, DVN, PXD), midstream MLPs (EPD, ET, WMB, OKE, KMI), service (SLB, HAL, BKR, NOV), Canadian (SU, CNQ, IMO, CVE), European (SHEL, BP, TTE, EQNR, REP, ENI), Japanese trading houses (8031 Mitsui, 8058 Mitsubishi Corp, 8053 Sumitomo, 2768 Sojitz, 8001 Itochu, 8002 Marubeni), oil service (TechnipFMC, WHD, FTI, CHX), renewables-adjacent (FSLR, ENPH, SEDG, PLUG, FCEL).

## Dominant sub-industries

- `integrated_oil_gas`  — XOM, CVX, SHEL, BP, TTE, EQNR, ENI, REP, OMV, LUPE, PBR, ECOPETROL, PKN, CEPSA
- `oil_gas_exploration_production`  — COP, EOG, OXY, HES, FANG, DVN, MRO, PXD, APA, AR, CRC, MTDR, CHX, CNX, CNRL, TELL, SBOW, CPE, LPI, REI, OXY (overlap)
- `oil_gas_refining_marketing`  — PSX, VLO, MPC, DK, PARR, CVI, HFC (merged), PBF, CENX (alum), PCR (Phillips 66 PSXP), Canadian refiners overlap with IOCs
- `oil_gas_storage_transportation`  — EPD, ET, WMB, OKE, KMI, TRP (TC Energy), ENB (Enbridge), PPL, HEP (HollyFrontier), CQP, ENLC, NGL, SXC; Canadian midstream (PPL.TO, ENB.TO, TRP.TO)
- `oil_gas_equipment_services`  — SLB, HAL, BKR, NOV, WHD, FTI, CHX, WFRD (Weatherford), PTEN, NEX, LBRT, HP, RIG, NBR, PUMP, CJ
- `oil_gas_drilling`  — HP, NBR, PDS, RIG, VAL (Valaris), NE (Noble), SDRL (Seadrill reorg'd)
- `coal_consumable_fuels`  — BTU (Peabody), ARCH, METC, CEIX, HCC, AMR, SXC; Australia (WHC.AX Whitehaven, NHC.AX New Hope, YAL.AX Yancoal)
- `integrated_renewable_electricity` — technically Utilities sector; but solar/wind pure-plays sometimes Energy: FSLR, ENPH, SEDG, ARRY, NOVA, RUN, PLUG, FCEL, BLDP, BEP (RE-focused Brookfield)
- `renewable_electricity_equipment`  — FSLR, ENPH, SEDG, ARRY, NOVA (hybrid consumer solar), MXL, SHLS (Shoals), TPIC, HGS

## Sub-industries warranting override

- `oil_gas_upstream` — EIA weekly petroleum data (Wed 10:30 ET), API weekly (Tue 4:30pm ET, paid), Baker Hughes rig count (Fri 1pm ET). Oil Price Information Service (OPIS). RigData.
- `oil_gas_midstream` — FERC filings (ferc.gov), pipeline-specific trade press (Pipeline & Gas Journal, Oil & Gas Journal).
- `oil_gas_service_equipment` — Spears & Associates drilling survey, SEA (Service Equipment Activity), land-rig utilisation from BHI/PTEN data.
- `oil_gas_refining` — OPIS refining margin data, Reuters/Argus crack spreads, IIR Energy refinery outage tracker.
- `renewable_energy` — PV Magazine, PV-Tech, Greentech Media (acquired, now Wood Mac), CleanTechnica, Recharge News, Wind Power Monthly, BNEF terminal (paid), S&P Global Platts Renewables.

Author sub_industry YAMLs for: `oil_gas_upstream`, `oil_gas_midstream`, `oil_gas_service_equipment`, `renewable_energy`, `oil_gas_refining`.

## Canonical sector-level sources

- Oil & Gas Journal (ogj.com)
- World Oil (worldoil.com)
- Hart Energy (hartenergy.com) — E&P trade
- Rigzone (rigzone.com)
- Upstream Online (upstreamonline.com)
- Petroleum Economist (petroleum-economist.com)
- S&P Global Platts (spglobal.com/platts, paid)
- Argus Media (argusmedia.com, paid)
- Reuters Energy (universal)
- Bloomberg Energy (universal)
- OPIS (opisnet.com, paid)
- **EIA** (eia.gov/news and eia.gov/petroleum/weekly) — Wednesday weekly inventory report is a mega-event
- **IEA** (iea.org/news) — monthly oil market report
- **OPEC+** (opec.org/press-releases) — meeting announcements
- **IMF WEO + Commodity Outlook**
- **World Bank Commodity Markets**
- **Baker Hughes Rig Count** (bakerhughes.com/rig-count) — Friday 1pm ET
- **API weekly** (api.org, paid)
- **FERC news** (ferc.gov/news-events/news)
- **BLM onshore leasing** (blm.gov/programs/energy-and-minerals/oil-and-gas)
- **BOEM offshore leasing** (boem.gov)
- Pipeline & Gas Journal (pgjonline.com)
- Pipeline News (pipelinenews.com)
- Rystad Energy press (rystadenergy.com/newsroom)
- Wood Mackenzie press (woodmac.com/press-releases)
- **PV Magazine** (pv-magazine.com)
- **PV-Tech** (pv-tech.org)
- **Recharge News** (rechargenews.com)
- **Wind Power Monthly** (windpowermonthly.com)
- **CleanTechnica** (cleantechnica.com)
- **Renewables Now** (renewablesnow.com)
- **Ember Climate** (ember-climate.org) — power sector data

## Common pitfalls

- **EIA Wednesday weekly** (10:30 ET) is the single biggest weekly event for the sector. Hourly precision matters for the adapter.
- **OPEC+ meeting announcements** — Saudi Aramco's Monday-morning official selling prices (OSPs) telegraph policy. Follow Reuters OPEC team on X.
- **Hurricane season** (Jun-Nov) — NOAA, NHC tracks move refining + offshore production names.
- **US crude inventories reported in barrels, European in tonnes** — unit conversion errors common.
- **Rig count on Fri 1pm ET** is the weekly US drilling activity proxy.
- **Backed-up: Japanese trading houses** (Mitsui, Mitsubishi Corp, etc.) behave like diversified holding cos — news diet is broader than oil.
- **Canadian oil sands names** (SU, CNQ, CVE, IMO) — heavy differential to WTI (WCS vs WTI spread) is the key metric. Baytex, MEG Energy similar.
- **Pipeline politics**: Keystone XL, Mountain Valley, Line 5 — regulatory approvals shift pipeline co valuations.
- **Refining crack spreads** are the real earnings driver. Not in EDGAR — come from OPIS/Platts/Argus.
- **Renewables exposed to policy** — IRA, EU Green Deal, Chinese solar dumping tariffs. Policy news moves FSLR/ENPH/SEDG more than fundamentals.

## Language / jurisdiction specifics

- **NO** — EQNR (Equinor), AKR (Aker), DNO, SUBC (Subsea 7) — Oslo Børs newsweb + DN (Dagens Næringsliv) lead. Petroleumstilsynet safety authority.
- **GB** — SHEL, BP — RNS + FT + Telegraph. BP's trading updates are market events.
- **FR** — TTE (TotalEnergies) — AMF + Les Echos.
- **NL/GB** — Shell dual-HQ (Shell plc LSE primary but historically NL too).
- **CA** — SU, CNQ, CVE, IMO — SEDAR+ + Globe and Mail ROB. Alberta Energy Regulator (AER) for operational.
- **JP trading houses** (8001 Itochu, 8031 Mitsui, 8058 Mitsubishi Corp) — diversified, file TDnet. Sector source catalogue should pick this up as "JP sogo shosha" — effectively a niche.
- **AU coal** — WHC.AX, NHC.AX, YAL.AX — ASX + AFR. Department of Industry coal-export data monthly.
- **BR** — PBR (Petrobras) — CVM + Valor + Agência Estado. Brazilian politics drives PBR more than oil prices.

## Sanity numbers

- sector.energy.* entries: **20–35**.
- sub_industry overrides: **4–5**.
- Overlay fill: IR_URL ≥95%, Press_Release_Feed ≥80%, X handle ≥45% (energy CEOs quiet on X, exception: Chesapeake / Pioneer / Hamm), transcript provider 100%, filings URL ≥95%.
