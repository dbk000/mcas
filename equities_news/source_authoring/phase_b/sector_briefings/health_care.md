# Sector Briefing — Health Care

**Agent:** `phase_b_health_care_opus`
**Scope:** 621 symbols, 19 countries, 19.37% weight. US pharma/biotech mega-caps (LLY, JNJ, MRK, PFE, ABBV, BMY, AMGN, GILD, ABT, ISRG, TMO, DHR, UNH, CI, CVS, HUM, ELV), EU pharma (NOVO, NOVN, ROG, GSK, AZN, SNY, BAYN), Japanese (TAKEDA, ASTELLAS, DAIICHI, 4502 Takeda), biotech long tail (many microcaps).

## Dominant sub-industries

- `pharmaceuticals`  — LLY, JNJ, MRK, PFE, ABBV, BMY, AMGN, GILD, NOVO, NOVN, ROG, GSK, AZN, SNY, BAYN, 4502 Takeda, 4503 Astellas, 4568 Daiichi Sankyo
- `biotechnology`  — AMGN (overlap), REGN, VRTX, MRNA, BIIB, ALNY, BMRN, INCY, SGEN, ILMN, 10x, TWST; long tail of microcap biotechs (SRPT, BLUE, EDIT, CRSP, NTLA, BEAM, VERV, etc.)
- `life_sciences_tools_services`  — TMO, DHR, A (Agilent), WAT (Waters), MTD, BRKR, BIO, QGEN, 10x, ILMN (overlap), ICLR (Icon), IQV, CRL (Charles River), MEDP, CVRX
- `health_care_equipment`  — ISRG, MDT, SYK, BDX, BSX, ZBH, EW, ALGN, DXCM, PODD, TDOC
- `health_care_supplies`  — HSIC (Henry Schein), PDCO (Patterson), OMI, BIO-Rad, Avantor
- `health_care_distributors`  — MCK, COR (Cencora), CAH, ABC (now COR)
- `health_care_services`  — UNH (managed care), HCA, THC, UHS, CVS (hybrid), ACHC, CYH, ENSG, AMED, CHE, HQY
- `managed_health_care`  — UNH, CI, HUM, ELV, MOH, CNC, CLOV, ALHC
- `health_care_facilities`  — HCA, THC, UHS, CYH (hospitals), LFST, PRMW
- `health_care_technology`  — DOCS, VEEV, MDRX (now Altera private), PHR, EVHN (Evolent), CRWD-ish for security
- `health_care_reits` — officially Real Estate sector, not here. Exclude.

## Sub-industries warranting override (CRITICAL for this sector)

Health Care is THE sector where sub-industry overrides matter most — biotech microcaps have radically different news diets than pharma mega-caps.

- `biotechnology_clinical_stage`  — pre-revenue names. News diet: Endpoints News, STAT+, BioPharma Catalyst, FiercePharma, BioSpace, ClinicalTrials.gov, PubMed Central, company investor-day webcasts. Conference-heavy (ASCO, ASH, AACR, AHA, ECTRIMS, AAN, EHA, ESMO). Press releases + CTG updates are the events.
- `biotechnology_commercial_stage`  — revenue-generating. Moves more like pharma. Endpoints still dominant but IMS/Symphony/Norstella prescription data matters.
- `pharmaceuticals_big_pharma`  — LLY/JNJ/MRK/PFE etc. Reuters/Bloomberg pharma desks, FiercePharma, Scrip, Pink Sheets (now Citeline Pink), Endpoints News Pharma.
- `medical_devices`  — ISRG/MDT/SYK. Mass Device (massdevice.com), MedTech Dive, MedCity News, MD+DI (mddionline.com).
- `life_sciences_tools`  — TMO/DHR/A. GenomeWeb, Nature Methods, Bio-IT World.
- `managed_care`  — UNH/CI/HUM/ELV. Modern Healthcare, Health Affairs, Becker's Hospital Review, CMS announcements.
- `hospital_operators`  — HCA/THC/UHS/CYH. Becker's, Modern Healthcare, AHA press.

Author sub_industry YAMLs for AT LEAST: `biotechnology_clinical_stage`, `pharmaceuticals_big_pharma`, `medical_devices`, `managed_care`, `life_sciences_tools`.

## Canonical sector-level sources

- FiercePharma (fiercepharma.com) — daily pharma news
- FierceBiotech (fiercebiotech.com) — daily biotech
- Endpoints News (endpts.com) — leading biotech scoop venue; paid (Premium + Pharma+)
- STAT News (statnews.com, statnews.com/pro=STAT+)
- BioPharma Catalyst (biopharmcatalyst.com) — event calendar + FDA PDUFA dates
- BioSpace (biospace.com) — jobs + deal news
- GenomeWeb (genomeweb.com) — life-sciences tools
- GEN (genengnews.com) — biotech trade
- Scrip (scrip.pharmaintelligence.informa.com, paid, Informa)
- Pink Sheet (pinksheet.pharmaintelligence.informa.com, paid)
- Evaluate Vantage (evaluate.com) — deal/pipeline analytics
- BIO industry org releases (bio.org)
- PhRMA industry org releases (phrma.org)
- Medtech Dive (medtechdive.com)
- Mass Device (massdevice.com)
- MD+DI (mddionline.com)
- Modern Healthcare (modernhealthcare.com) — payer/provider
- Becker's Hospital Review (beckershospitalreview.com)
- Health Affairs (healthaffairs.org)
- **FDA** (fda.gov/news-events/fda-newsroom; RSS feeds for drug approvals, medical devices, 483/warning letters, AdComs)
- **FDA Drug Approvals & Databases** (fda.gov/drugs/drug-approvals-and-databases)
- **FDA Medical Device Recalls** (fda.gov/medical-devices/medical-device-recalls)
- **EMA** (ema.europa.eu — CHMP meeting highlights; PRAC agenda)
- **PMDA Japan** (pmda.go.jp/english/about-pmda/0004.html)
- **MHRA UK** (gov.uk/government/organisations/medicines-and-healthcare-products-regulatory-agency)
- **NMPA China** (nmpa.gov.cn)
- **Health Canada** (canada.ca/en/health-canada)
- **TGA Australia** (tga.gov.au)
- **CMS press** (cms.gov/newsroom)
- **ClinicalTrials.gov** (clinicaltrials.gov)
- **EU Clinical Trials Register** (clinicaltrialsregister.eu)
- **PubMed / NEJM / The Lancet / JAMA / Cell / Nature Medicine** — preprint + paper embargoes
- **ASCO Plenary + ESMO** abstracts (embargoed windows)
- **Arxiv-adjacent medical preprint** (medRxiv)

## Common pitfalls

- **Biotech microcaps often have NO IR page** — only an SEC EDGAR filer page + a press-release widget on the corporate site. Set IR_URL=null explicitly for these; log confidence=0.6 with note "microcap biotech, no dedicated IR site".
- **FDA AdCom briefing-document drops are market events** 1-2 days before the meeting. FDA publishes at ~noon ET the day before the committee — widely-watched by biotech traders.
- **Complete Response Letters (CRLs)** are the fastest-moving biotech event. Usually press-released BY THE ISSUER before anything else, but FDA confirms via fda.gov/drugs.
- **Phase 3 trial readouts — check topline press release + conference-abstract embargoed.** Some names (GILD, ALNY) drop topline a week before full data at a conference.
- **Managed care moves on CMS rate notices (Advance Notice + Final), MA bid cycle.** CMS Calendar of Events + rate letters.
- **Pharma M&A leaks to Endpoints/STAT/FT first.** Official RNS/8-K follows.
- **Don't forget 6-K filings** for foreign private issuers (NOVO, NOVN, ROG, GSK, AZN). 6-K is the US-mandated "English translation of foreign filing" — often lags home-country disclosure by hours.

## Language / jurisdiction specifics

- **CH** — ROG (Roche), NOVN (Novartis) — ad-hoc via SIX Exchange Regulation + NZZ/Finanz und Wirtschaft. English IR press is usually simultaneous but leads in native-language fuw.ch by minutes.
- **DK** — NOVO (Novo Nordisk) — Copenhagen Stock Exchange ad-hoc + Borsen. Novo is mega-cap with heavy English presence.
- **DE** — BAYN (Bayer), SATS (Sartorius), FME (Fresenius Medical) — DGAP ad-hoc.
- **FR** — SNY (Sanofi) — AMF + Les Echos. Bi-lingual press releases standard.
- **JP** — 4502 Takeda, 4503 Astellas, 4568 Daiichi Sankyo — TDnet JP + English IR. Takeda has NYSE ADR, files 6-K.
- **UK** — GSK, AZN, SMIN (Smith+Nephew) — RNS. FT + Reuters lead on UK-specific news.
- **CN/HK** — Wuxi Biologics (2269 HK), Innovent (1801 HK) — HKEX + CNINFO. Chinese biopharma coverage in English via Endpoints + FierceBiotech Asia.

## Sanity numbers

- sector.health_care.* entries: **35–55** (sector-level alone; add ~20-30 per sub_industry override).
- sub_industry overrides: **5–6** (the most of any sector).
- Overlay fill targets: IR_URL ≥85% (microcap biotechs drag this down), Press_Release_Feed ≥60%, X handle ≥55% (pharma CEOs often silent), transcript provider — NOTE: for biotechs with no earnings transcripts because no revenue yet, use `none`, logged at confidence 0.95.
