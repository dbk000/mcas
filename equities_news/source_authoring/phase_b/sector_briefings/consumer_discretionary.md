# Sector Briefing — Consumer Discretionary

**Agent:** `phase_b_consumer_discretionary_opus`
**Scope:** 636 symbols, 20 countries, 19.16% weight. US retail + ecom giants (AMZN, TSLA, HD, LOW, NKE, SBUX, MCD), auto OEMs (TM, GM, F, STLA, HMC, VW.DE), luxury (LVMH, HERMES, RMS.PA, CFR.SW), European + Asian apparel, hotels/travel, restaurants.

## Dominant sub-industries

- `automobile_manufacturers`  — TSLA, TM, HMC, F, GM, STLA, RACE, POAHY, NSANY, 7203 Toyota, MBG.DE, BMW.DE, VOW3.DE, STLA.MI, HYUNDAI, KIA
- `auto_parts_equipment`  — APTV, LEA, BWA, MGA, ALV (Autoliv), CTSH, 6471 NSK, 6473 JTEKT, 7211 Mitsubishi Motors parts chain
- `broadline_retail`  — AMZN, COST, TGT, WMT (WMT is staples), DG, DLTR; EU (OCADO, AHOLD)
- `apparel_accessories_luxury`  — LVMH (MC.PA), HERMES (RMS.PA), KER.PA (Kering), EL.PA (Essilorluxottica), RLX (Swatch/Rolex-adjacent), BRBY, MONC (Moncler), GUCCI-parent
- `apparel_retail`  — TJX, ROST, GPS, LULU, URBN, FL (Foot Locker), ANF; EU (H&M, INDITEX/Zara, NXT LN)
- `footwear`  — NKE, DECK, CROX, ONON, BIRK, SKX, ADS.DE (Adidas), PUMA.DE, ASICS 7936
- `hotels_resorts_cruise_lines`  — MAR, HLT, H, WYNN, LVS, MGM, RCL, CCL, NCLH; IHG LN, ACCOR FP
- `restaurants`  — MCD, SBUX, CMG, YUM, DRI, QSR, DPZ, WING, CAKE; Japan (NISSIN-KYO, Kura Sushi, Zensho, Yoshinoya 9861), UK (GRG LN Greggs, WTB LN Whitbread)
- `leisure_products`  — POOL, BC, YETI, WKHS, HOG
- `home_furnishings`  — WSM, RH, LZB, TPX (Tempur), MHK (Mohawk), LLB
- `distributors`  — LKQ, POOL, GPC, SITE, FAST (FAST is industrials)
- `homebuilding`  — LEN, DHI, PHM, NVR, KBH; Japan (HKSEKI 1925 Daiwa House, 1928 Sekisui House)
- `casinos_gaming`  — WYNN, LVS, MGM, PENN, CZR, DKNG, FLUT (Flutter), EDR
- `specialty_stores`  — ORLY, AZO, TSCO, BURL, ULTA
- `education_services`  — CHGG, COUR, LAUR, STRA, LOPE; China ADRs TAL, EDU

## Sub-industries warranting override

- `automobile_manufacturers` — WARDS Auto, Automotive News, Electrek (EV-focused), InsideEVs, Reuters Autos, Nikkei Automobile Daily (JP-language). Chinese EV coverage: CnEVPost, ElectricVehiclesWorld.
- `luxury_apparel` — BoF (Business of Fashion), WWD (Women's Wear Daily), Jing Daily (China luxury), Vogue Business, LaConceria (Italian leather). Differs from mass apparel.
- `restaurants` — Restaurant Business Online, Nation's Restaurant News, QSR Magazine, Eater (consumer side).
- `gaming_casinos` — Gambling Compliance (paid), Casino.org, iGaming Business, GeoComply data, DC-geolocation data vendors.
- `hotels_cruise` — STR (Smith Travel Research hotel data), CruiseCritic, Seatrade Cruise News.

Author sub_industry YAMLs for at minimum: `automobile_manufacturers`, `luxury_apparel`, `restaurants`, `casinos_gaming`, `homebuilding` (real-estate-adjacent news diet).

## Canonical sector-level sources

- Retail Dive (retaildive.com)
- Bloomberg Retail (universal + Bloomberg terminal)
- Modern Retail (modernretail.co)
- Coresight Research press (coresight.com)
- WSJ Markets — Business of Retail section (universal)
- Automotive News (autonews.com)
- WWD / Business of Fashion / Vogue Business (paid)
- Restaurant Business Online
- Calculated Risk (calculatedriskblog.com) — housing starts/sales data
- NAHB Economics + Housing Market Index (nahb.org/news-and-economics)
- NADA car sales + used-car price data (nada.com)
- Manheim + Cox Automotive used-car index
- Lombard Odier Luxury + Altagamma reports (seasonal)
- ISI Evercore/Telsey Advisory group previews (sell-side; track via X)

## Common pitfalls

- **AMZN is tech-like not retail-like.** AWS earnings drive stock more than retail. Handled same as mega-cap tech for source priority, but officially in this sector.
- **TSLA moves on delivery numbers (quarterly) + Elon's X posts** — Official_X_Handle should be `elonmusk` NOT `tesla` for fastest market-moving social signal. Flag this exception in the author log.
- **Japanese autos file TDnet + publish Monthly Production statistics.** Monthly sales data from JAMA (Japan Auto Makers Assoc) moves the group.
- **Chinese EV names (NIO, LI, XPEV, BYD)** — weekly insurance-registration data (CnEVPost, ThinkerCar) is the leading indicator, not the monthly wholesale number.
- **Luxury names move on Chinese tourism data.** China outbound travel numbers, tax-free sales (Global Blue). Macau gaming GGR = casinos_gaming sub-industry primary.
- **Cruise lines move on booking curves, not SEC filings.** Cruise Critic, WAVE season reports.
- **Retail earnings season pre-announcements** (Redbook weekly retail sales, Mastercard SpendingPulse) — key macro inputs.
- **LVMH, L'Oreal, Hermes** report in French first (Les Echos, AMF). Press in FR leads English by ~30-60 min.

## Language / jurisdiction specifics

- **DE automakers** (VW, BMW, MBG) — DGAP ad-hoc + Handelsblatt lead. Monthly WKB registrations (Kraftfahrt-Bundesamt, kba.de).
- **JP automakers/retailers** — TDnet lead. Monthly production reports on individual IR pages.
- **FR luxury** — AMF + Les Echos lead English wires by 30 min.
- **Chinese retail ADRs (JD, BABA, PDD)** — file HKEX + CNINFO first. Retail-holiday data (Singles Day, 618) moves the group — monitored via Xinhua.

## Sanity numbers

- sector.consumer_discretionary.* entries: **35–50**.
- sub_industry overrides: **5–7**.
- Overlay fill targets: IR_URL ≥95%, Press_Release_Feed ≥60% (many small-cap retailers have no feed), X handle ≥80%, transcript provider 100%, filings URL ≥95%.
