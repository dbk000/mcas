# Sector Briefing — Industrials

**Agent:** `phase_b_industrials_opus`
**Scope:** 976 symbols, 25 countries, 30.69% weight. Massive sector — defence (LMT, RTX, NOC, GD, BA, GE-defense, TDG, HII, HEI, LHX, BAE.L, Rheinmetall, Saab), machinery (CAT, DE, CMI, PH, EMR, ETN, DOV), trucking/logistics (UPS, FDX, CSX, UNP, NSC), staffing (ADP, PAYX), conglomerates (GE Aerospace, HON, MMM, ITW, IR, ROP, ROK, AME), commercial services (VRSK, BR), airlines (DAL, UAL, AAL, LUV, RYAAY, LHA, IAG), Japanese industrials (7013 IHI, 6501 Hitachi, 7011 Mitsubishi Heavy, 9020 East JR, Komatsu 6301, Fanuc 6954), European (Siemens, ABB, Schneider), heavy equipment.

## Dominant sub-industries

- `aerospace_defense`  — LMT, RTX, NOC, GD, BA, HII, LHX, TDG, HEI, CW, SPR, KTOS, CAE; EU (BAE.L, AIR.PA/Airbus, LDO.MI/Leonardo, HO.PA/Thales, SAF.PA/Safran, RHM.DE/Rheinmetall, MTX.DE/MTU, SAAB-B.ST); Japan (7012 Kawasaki Heavy, 7011 Mitsubishi Heavy, 7013 IHI)
- `construction_machinery_heavy_trucks`  — CAT, DE, CMI, AGCO, PCAR, OSK, MTW, CNHI; Japan (6305 Hitachi Construction, 6301 Komatsu, 7012 Kubota 6326)
- `industrial_machinery_supplies_components`  — PH, EMR, ETN, DOV, ROP, AME, FTV, XYL, PNR, WWD, ROK, AOS, BMI, NDSN; Japan (6367 Daikin, 6501 Hitachi, 6954 Fanuc, 6723 Renesas — hybrid with IT)
- `electrical_components_equipment`  — ROK, AME, HUBB, NVT, SPXC, EMR (hybrid); EU (SIE.DE/Siemens, ABBN.SW/ABB, SU.PA/Schneider Electric, LR.PA/Legrand)
- `agricultural_farm_machinery`  — DE, AGCO, CNHI (all overlap with construction)
- `air_freight_logistics`  — UPS, FDX, EXPD, XPO, CHRW, LSTR, GXO, WERN, JBHT, SAIA; EU (DPW.DE/DHL, DSV.CO, Kuehne+Nagel KNIN.SW)
- `airlines_passenger`  — DAL, UAL, AAL, LUV, ALK, JBLU, HA, ULCC, ALGT; EU (RYAAY, EZJ.L, LHA.DE, IAG.L/IAG, AF.PA/Air France-KLM); Japan (9201 JAL, 9202 ANA); Asia (SIA.SG, CX 0293, 600029 China Southern)
- `marine_ports_services`  — SEAS (Seaspan); EU (DSV, Kuehne+Nagel, Maersk MAERSK-B.CO, Hapag-Lloyd HLAG.DE); Asia (0066 MTR, 9107 Kawasaki Kisen, 9104 Mitsui OSK, 9101 Nippon Yusen)
- `passenger_ground_transportation`  — UBER, LYFT, DASH (hybrid); EU FLIXBUS (private), FLIX.DE-adjacent
- `passenger_rail` (not really in US; heavy in Japan) — Japan (9020 East JR, 9021 West JR, 9022 Central JR, 9001 Tokyu, 9008 Keio); EU (VT.L Vossloh, CAF.MC Construcciones y Auxiliar, SNCF state-owned)
- `trucking` — ODFL, ARCB, XPO, SAIA
- `commercial_services_supplies`  — WM, RSG, WCN, CLH, SRCL, UNF, RBC-related, PWR, MMS (Maximus)
- `human_resource_employment_services`  — ADP, PAYX, PAYC, ROL, TNET, HQY, ASGN
- `industrial_conglomerates`  — MMM, HON, IR, GE (now split), IBKR (brokerage — ignore here), HXL, DOV, TFX; Japan (6501 Hitachi); KR (005930 Samsung — IT though)
- `research_consulting_services`  — VRSK, EFX, BR, MMS, FICO (tech-like), ICE (exchanges)
- `security_alarm_services` — ALLE, AZZ, ASRT
- `office_services_supplies` — SCL, ACCO, PBI, XRX (IT-ish)
- `diversified_support_services` — CTAS, UNF, ARMK, ABM, MHK-adj, HEI-related
- `environmental_facilities_services` — WM, RSG, WCN, CLH, CWST, SRCL, ADN.L
- `transportation_infrastructure`  — ATSG, infra operators; EU (FER.MC Ferrovial, VINC.PA/VIN, ACS.MC, FLT, ACC.PA, MAp-adj)

## Sub-industries warranting override (MANY)

- `aerospace_defense_prime` — LMT/RTX/NOC/GD/BA/HII. News diet: Defense News, Breaking Defense, Aviation Week, Jane's, Janes Defence Weekly, The War Zone, FlightGlobal, Barron's Defense. Conferences: AIAA, AUSA, Paris Air Show, Farnborough, Dubai Air Show, DSEI. DoD daily contract announcements (defense.gov/News/Contracts).
- `aerospace_defense_commercial` — BA / AIR.PA / spirit / TDG / HEI. Aviation Week, The Air Current, Flightglobal, Leeham News. Overlaps defense prime on BA.
- `machinery_heavy_equipment` — Engineering News-Record, Construction Equipment, Komatsu monthly deliveries (JP-reporting).
- `airlines` — Cirium, ATW (Air Transport World), Paddle Your Own Kanoo, Simple Flying, View From The Wing (consumer angle), DoT consumer complaint data, TSA throughput numbers daily.
- `logistics_freight` — FreightWaves (freightwaves.com), Journal of Commerce (JOC.com), Supply Chain Dive, Cass Freight Index, Drewry container rate index.
- `marine_shipping` — Lloyd's List, Tradewinds, Splash247, Clarksons Research, BIMCO.
- `rail` — Railway Gazette, Progressive Railroading, RAC (Japan) stats.
- `environmental_waste` — Waste360, Waste Dive.

Author sub_industry YAMLs for AT LEAST: `aerospace_defense_prime`, `airlines`, `logistics_freight`, `machinery_heavy_equipment`, `marine_shipping`.

## Canonical sector-level sources

- Defense News (defensenews.com)
- Breaking Defense (breakingdefense.com)
- Aviation Week (aviationweek.com)
- Flightglobal (flightglobal.com)
- Jane's (janes.com, paid)
- The War Zone (twz.com / theaviationgeekclub)
- FreightWaves (freightwaves.com)
- Journal of Commerce (joc.com, paid)
- Supply Chain Dive (supplychaindive.com)
- Trucks.com + Transport Topics (transporttopics.com)
- Railway Gazette (railwaygazette.com)
- Lloyd's List (lloydslist.com, paid)
- Tradewinds (tradewindsnews.com, paid)
- Engineering News-Record (enr.com)
- Construction Dive (constructiondive.com)
- Industry Week (industryweek.com)
- Modern Materials Handling
- Assembly Magazine
- Waste360 / Waste Dive
- Environmental Finance (environmental-finance.com)
- **DoD press** (defense.gov/News)
- **DoD Contracts** (defense.gov/News/Contracts) — daily contract announcements, market-moving for defense primes
- **FAA press** (faa.gov/newsroom)
- **EASA** (easa.europa.eu/en/newsroom-and-events)
- **CAA UK** (caa.co.uk/news)
- **JCAB Japan** (mlit.go.jp/koku)
- **NHTSA recalls** (nhtsa.gov/recalls)
- **FMCSA** (fmcsa.dot.gov)
- **OSHA** (osha.gov/news)
- **EPA press** (epa.gov/newsreleases)
- **TSA daily throughput data** (tsa.gov/coronavirus/passenger-throughput)
- **ATI / IATA** traffic monthly
- **ACI** airport traffic
- **ARC** air fare transaction data (airline revenue proxy)
- **ISM PMI Mfg + Services** monthly
- **BLS Productivity** quarterly
- **Markit/S&P Global PMI** monthly global
- **Cass Freight Index** monthly
- **Drewry WCI (container)** weekly

## Common pitfalls

- **DoD daily contracts at 5pm ET move defence primes.** Build adapter for defense.gov/News/Contracts — each contract announcement is structured (firm, amount, contract number).
- **Aircraft orders at airshows (Paris, Farnborough, Dubai, Singapore)** — dense news flow 1 week per event, 4x per year.
- **Airline monthly traffic reports** (DAL, UAL, AAL publish 15th-ish of month). LUV publishes weekly booked-revenue sometimes.
- **Rail weekly carload data** (AAR.org for US, JR-companies for JP). Market-moving for CSX/UNP/NSC.
- **Waste management weekly landfill data** (EPA, state-by-state).
- **European industrials in DE/FR/IT** — DGAP/AMF/Consob lead English wires. Handelsblatt scoop rate is high for German primes.
- **Japanese industrials** — TDnet + Nikkei Business Daily JP-first.
- **Aerospace is heavily regulated for export** — AECA/ITAR/CAAT issues move names. State Dept + Commerce BIS announcements.

## Language / jurisdiction specifics

- **JP** — 7011 MHI, 7012 Kawasaki Heavy, 7013 IHI, 6301 Komatsu, 6954 Fanuc, 6367 Daikin — TDnet + Nikkei JP. Monthly machine-tool orders from JMTBA (Japan Machine Tool Builders' Assoc).
- **DE** — SIE.DE, MBG.DE (autos though), RHM.DE, MTX.DE — DGAP + Handelsblatt.
- **FR** — AIR.PA, SAF.PA, HO.PA — AMF + Les Echos. Air France-KLM dual-listed (AF.PA + KLM.AS).
- **UK** — BAE.L, RR.L (Rolls-Royce), MNG.L — RNS. Defense export approvals tracked at UK Parliament ECJU.
- **IT** — LDO.MI (Leonardo), FNC.MI — Consob + Il Sole.
- **Nordic** — SAAB-B.ST, VOLV-B.ST, ATCO-A.ST — SE geo + Affärsvärlden.

## Sanity numbers

- sector.industrials.* entries: **35–55**.
- sub_industry overrides: **5–6**.
- Overlay fill: IR_URL ≥95%, Press_Release_Feed ≥75%, X handle ≥50% (industrial CEOs very quiet on X), transcript provider 100%, filings URL ≥95%.
