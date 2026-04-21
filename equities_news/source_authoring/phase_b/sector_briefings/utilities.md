# Sector Briefing — Utilities

**Agent:** `phase_b_utilities_opus`
**Scope:** 143 symbols, 20 countries, 6.08% weight. US IOUs (NEE, DUK, SO, AEP, D, XEL, EXC, ES, PCG, EIX, SRE, WEC, CMS, PEG, DTE, FE, PPL, ATO, PNW, NWE), Canadian utes (EMA, FTS, BIP.TO), European (IBE.MC Iberdrola, ENEL.MI, ENG.MC Enagas, NG.L National Grid, SSE.L, DRX.L, EDF state-owned now), water (AWK, WTRG, CWT, SJW, SBS), renewables-IPP (NEP, AY, CWEN, BEP), Japanese power (9501 TEPCO, 9503 Kansai Electric, 9502 Chubu), gas utilities (ATO, NJR, SWX).

## Dominant sub-industries

- `electric_utilities`  — NEE, DUK, SO, AEP, D, XEL, EXC, ES, PCG, EIX, SRE, WEC, CMS, PEG, DTE, FE, PPL, PNW, NWE, ED, EVRG, LNT, POR, NI (hybrid multi-util); CA (EMA, FTS); EU (IBE.MC, ENEL.MI, NG.L, SSE.L, DRX.L, VER.VI, REE.MC)
- `multi_utilities`  — PEG, D (Dominion), SRE, WEC, CMS, NI, CNP; EU (ENGI.PA Engie, IBE.MC overlap)
- `gas_utilities`  — ATO, NJR, SWX, SR, SJI, NFG, OGE, PNY, SWN (gas-heavy E&P though), CNP (hybrid); EU (SRG.MI Snam, NG.L National Grid gas side)
- `water_utilities`  — AWK, WTRG, CWT, SJW, SBS, YORW, PNRP (ARTNA), MSEX, PRMW (not water), CSR; EU (SBS Sabesp, UU.L United Utilities, SVT.L Severn Trent, PNN.L Pennon)
- `renewable_electricity_utility`  — NEP, AY (Atlantica), CWEN (Clearway), BEP (Brookfield Ren), ORSTED.CO, EDP-R.LS (EDP Renováveis), NOVA (consumer — in Discretionary though)
- `independent_power_producers_energy_traders`  — VST (Vistra), NRG, TLN (Talen), CEG (Constellation), AES, ORA (Ormat), OGE
- `electric_utilities_nuclear` (implicit sub) — CEG, EXC (pre-split), VST (nuclear side), Duke (Brunswick/Harris/Shearon Harris)

## Sub-industries warranting override

- `electric_utilities` — rate-case news dominates. S&P Global Market Intelligence Utilities + SNL Utility (paid), UtilityDive, American Public Power Association (APPA), EEI Issues & Policy.
- `renewable_electricity_utility` — SEIA (Solar Energy Industries Association), AWEA (now American Clean Power), BloombergNEF (paid), Lazard LCOE annual.
- `water_utilities` — very specialist: American Water Works Assoc, Water World, WaterOnline, Utility Dive Water.
- `ipp_power_traders` — ISO/RTO reports (CAISO, ERCOT, PJM, MISO, NYISO, ISO-NE) — market-clearing price data. EIA hourly generation data.

Author sub_industry YAMLs for: `electric_utilities`, `renewable_electricity_utility`, `water_utilities`, `ipp_power_traders`.

## Canonical sector-level sources

- Utility Dive (utilitydive.com)
- S&P Global Utilities (spglobal.com/market-intelligence/en/solutions/utilities)
- American Public Power (publicpower.org)
- Edison Electric Institute (eei.org/news)
- Smart Electric Power Alliance (sepapower.org)
- RTO Insider (rtoinsider.com, paid)
- Platts Power (platts.com, paid, via S&P Global)
- Argus Media Power (argusmedia.com, paid)
- **FERC** (ferc.gov/news-events/news) — rate cases, mergers approval
- **NRC** (nrc.gov/reading-rm/doc-collections/news/index.html) — nuclear events, ops lic amendments
- **CAISO / ERCOT / PJM / MISO / NYISO / ISO-NE** news + operational
- **DOE** (energy.gov/articles)
- **EIA** (eia.gov/electricity + eia.gov/natgas) — weekly natural gas storage (Thu 10:30 ET)
- **EPA power plant data** (epa.gov/ghgreporting)
- **NOAA heating/cooling degree days** — predict load
- **AEMO Australia** (aemo.com.au/newsroom)
- **ENTSO-E Europe** (entsoe.eu) — European TSO data
- **Ofgem** (ofgem.gov.uk/press-releases)
- **CRU Germany** (cru-group.com, paid)
- **CNMC Spain** — utility regulator overlapping geo.es.cnmv
- Solar Power World (solarpowerworldonline.com)
- SolarToday (solartoday.com)
- **BloombergNEF** (about.bnef.com, paid)
- **Lazard LCOE** annual
- **Wood Mac Power & Renewables** (woodmac.com/press-releases)
- Hydrowire (hydroreview.com)
- **APPA**, **EEI**, **LPPC**, **NRECA** org releases

## Common pitfalls

- **Rate cases are multi-year processes.** Status updates from state PSCs (Public Service Commissions) — 50-state regulatory environment. Utility Dive + RTO Insider track these.
- **Weekly natgas storage (Thu 10:30 ET)** is the dominant weekly event for gas utes + some electric names. Hourly adapter precision matters.
- **Nuclear events**: refueling outages, capacity-factor reports, license amendments. NRC event reports (event.nrc.gov/lerreader) move CEG + VST.
- **Hurricanes + wildfires**: PCG exposure to California wildfire liability, SO to Georgia storms, EIX to California. NOAA + Cal Fire.
- **ISO/RTO market prices** move IPPs (VST, CEG, NRG). ERCOT summer peak pricing is a seasonal event for VST.
- **European power policy** — German Energiewende, UK CfD auctions, Spanish social tariff — drive IBE, SSE, ENEL. ENTSO-E data feed.
- **Japanese utilities** are post-Fukushima contingent on nuclear restarts. 9501 TEPCO, 9503 KEPCO, 9502 Chubu. TDnet + METI.
- **Water utilities are small and obscure** — lead times generally favourable (less sell-side attention). Local-region coverage from state water-utility press.

## Language / jurisdiction specifics

- **JP** — 9501 TEPCO, 9503 KEPCO — TDnet. Nuclear restart approvals from Nuclear Regulation Authority (NRA) — regulatory events.
- **DE** — E.ON (EOAN.DE), RWE (RWE.DE) — DGAP + Handelsblatt. BNetzA (Bundesnetzagentur) for grid + auction news.
- **FR** — EDF (state-owned, delisted); ENGI.PA (Engie) — AMF + Les Echos.
- **ES** — IBE.MC (Iberdrola), ENG.MC (Enagas), REE.MC (Redeia) — CNMV + Expansión.
- **IT** — ENEL.MI, SRG.MI (Snam), TERNA TRN.MI — Consob + Il Sole.
- **GB** — NG.L (National Grid), SSE.L, PNN.L (Pennon), SVT.L (Severn Trent), UU.L (United Utilities), DRX.L (Drax) — RNS + FT + Investors Chronicle.
- **AU** — ORG.AX (Origin), AGL.AX, APA.AX — AFR + ASX. AEMO weekly reports.

## Sanity numbers

- sector.utilities.* entries: **15–25**.
- sub_industry overrides: **3–4**.
- Overlay fill: IR_URL ≥98% (utilities have formal IR), Press_Release_Feed ≥85%, X handle ≥30% (very quiet sector), transcript provider 100%, filings URL ≥98%.
