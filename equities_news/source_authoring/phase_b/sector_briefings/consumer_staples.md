# Sector Briefing — Consumer Staples

**Agent:** `phase_b_consumer_staples_opus`
**Scope:** 293 symbols, 21 countries, 11.22% weight. US mega-caps (WMT, COST, PG, KO, PEP, PM, MO, MDLZ), European consumer giants (NESN.SW, UL, DGE, BN.PA/Danone, BATS.L, HEN3.DE), Japanese (KIRIN, ASAHI, Ajinomoto), tobacco, beverages, household products, food retailers.

## Dominant sub-industries

- `consumer_staples_merchandise_retail`  — WMT, COST, TGT (partly discretionary), DG, DLTR, BJ, AEO-adjacent
- `food_retail`  — KR, ACI (Albertsons), SFM, CASY, GO, AHOLD (AD.AS), CARREFOUR (CA.PA), TESCO, SBRY, MRW, WOOLWORTHS (WOW.AX), COL.AX, 7ELV.T (7-Eleven JP/Seven & i)
- `hypermarkets_super_centers`  — WMT (overlap), COST, BJ
- `packaged_foods_meats`  — GIS, K (Kellogg), CAG, CPB, MDLZ, HRL, TSN, POST; NESN.SW (Nestle), ULVR.L/UL (Unilever), DANONE (BN.PA), KDP
- `beverages_non_alcoholic`  — KO, PEP, MNST, KDP, COKE
- `beverages_alcoholic`  — BUD (AB InBev), DEO, STZ, SAM, TAP, HEINEKEN (HEIA.AS), PERNOD (RI.PA), CARLSB.CO, REMY, ASAHI 2502, KIRIN 2503, SAPPORO 2501
- `distillers_vintners`  — DEO, BF.B (Brown-Forman), RI (Pernod), SUNTORY 2587 (Suntory Beverage & Food)
- `tobacco`  — PM, MO, BTI (BATS.L), JAPAN TOBACCO 2914, ITC, SWMA.ST (Swedish Match now PMI)
- `household_products`  — PG, CL, CHD, CLX, EL (Estee is personal products)
- `personal_care_products`  — EL, CL (overlap), COTY, ELF, NUS, KENT, HEN3.DE (Henkel), BDS (Beiersdorf), FKRT (Revlon private)
- `agricultural_products_services`  — ADM, BG (Bunge), ANDE, CAG (farm side), TSN (overlap)

## Sub-industries warranting override

- `tobacco` — news diet is dominated by regulatory (FDA menthol rule, EU TPD revisions, WHO FCTC). Tobacco Reporter, ECigIntelligence, FDA Center for Tobacco Products.
- `beverages_alcoholic` — IWSR reports, Drinks International, BeverageDaily. Spirits-vs-beer-vs-wine different cycles.
- `packaged_foods_meats` — Food Business News, Food Dive, Nielsen/Circana scanner data (not public but widely quoted).
- `agricultural_products_services` — USDA WASDE reports, weekly grain stocks. DTN Progressive Farmer. Bloomberg/Reuters Ag desks.

Recommended sub_industry YAMLs: `tobacco`, `beverages_alcoholic`, `packaged_foods_meats` (Circana/Nielsen scanner proxies), `agricultural_products_services`.

## Canonical sector-level sources

- Food Dive (fooddive.com)
- Food Business News (foodbusinessnews.net)
- BevNet (bevnet.com) — beverage trade
- Drinks Business (thedrinksbusiness.com)
- Just-Food (just-food.com)
- Just-Drinks (just-drinks.com)
- Progressive Grocer (progressivegrocer.com) — retail trade
- Supermarket News (supermarketnews.com)
- Private Label Manufacturer Association (plma.com) press
- Circana (was IRI/NPD) press (circana.com) — scanner data proxy
- Nielsen press
- USDA ERS publications + WASDE (ers.usda.gov, usda.gov/oce/commodity)
- Tobacco Reporter (tobaccoreporter.com)
- ECigIntelligence (paid)
- IWSR Drinks Data (free headlines, paid detail)
- Beverage Digest (beverage-digest.com)
- Food Navigator (foodnavigator.com, foodnavigator-usa.com)

## Common pitfalls

- **Tobacco names move on regulator, not earnings.** FDA announcements, EU TPD negotiations, China tobacco reform. Don't weight earnings too heavily.
- **Beverages/alcohol move on shipments data (STCA Beer Institute, Spirits Council).** Weekly + monthly shipment data pre-trades earnings.
- **Food retail moves on macro CPI food index + SNAP policy.** Grocery SNAP changes are event-drivers for KR, DG, DLTR.
- **Staples generally — CEO X handles lower priority** — most CEOs do NOT tweet. Board/activist campaigns (Trian on PG/UL, Carl Icahn on KHC) move these more. Mark activist campaigns carefully.
- **European staples (NESN, UL, DGE, PM Davidoff parent)** report in multiple languages. NESN — Swiss German + French; UL/DGE — English RNS; BN.PA — French + English IR PDF. TDnet for Japanese.
- **Private-label war intensity matters more than branded pricing.** Private Label Manufacturer Association + Cuts Intel press.
- **Scanner data is proprietary but sell-side slice it.** J.P. Morgan / Evercore weekly tracker notes — track via X lists.

## Language / jurisdiction specifics

- **JP** — Kirin/Asahi/Sapporo file TDnet JP-first. Ajinomoto (2802), Kao (4452) same pattern.
- **FR** — Danone, L'Oreal (which is Staples-adjacent — in Personal Products officially here) — AMF + Les Echos lead by 30-60 min.
- **Italian food names** (Barilla private; Ferrero private) — public Italian staples scarce.
- **Brazilian staples** — JBSS3, BRFS3, MRFG3 (meat packers) — CVM + Valor leads.

## Sanity numbers

- sector.consumer_staples.*: **15–25** entries.
- sub_industry overrides: **3–4**.
- Overlay fill: IR_URL ≥98% (staples companies all have polished IR sites), Press_Release_Feed ≥80%, X handle ≥65% (many CEOs silent on X), transcript provider 100%, filings URL ≥95%.
