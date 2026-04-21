# Sector Briefing — Communication Services

**Agent:** `phase_b_communication_services_opus`
**Sector YAML:** `registry/news_sources/sectors/communication_services.yaml`
**Scope:** 238 symbols, 19 countries, 14.24% weight. Dominated by US mega-caps (META, GOOGL, NFLX, TMUS, VZ, T, DIS, CMCSA) plus Japanese telcos (NTT, KDDI, SoftBank), UK media (RELX, WPP, ITV), German telecoms (DTE), Nordic media (Axel Springer private but others public).

## Dominant sub-industries

- `interactive_media_services`  — META, GOOGL, SNAP, PINS, RBLX, MTCH, IAC
- `entertainment`  — DIS, NFLX, PARA, WBD, LYV, EDR (Endeavor), SONY, NTES
- `movies_entertainment`  — more narrow, overlaps entertainment. Live-event production (LYV).
- `interactive_home_entertainment`  — EA, TTWO, ATVI (now MSFT), NTDOY, SE, NTES, BILI
- `publishing`  — NYT, NWS, PSO (Pearson), RELX, INFO (now ISVC), WLY (Wiley)
- `broadcasting`  — SIRI, IHRT, CMCSA (dual with cable), TEGNA, NXST
- `cable_satellite`  — CHTR, CMCSA, DISH/SATS, VSAT
- `wireless_telecommunication_services`  — TMUS, VZ, T, VOD, VZ, BCE, RCI, ORAN (Orange FR), TEF (Telefonica), NTT, 9433 KDDI, 9434 SBG, TLS (Telstra)
- `integrated_telecommunication_services`  — T, VZ, CMCSA (hybrid), DTE, BT-A, ORAN, TEF, 9432 NTT
- `advertising`  — WPP, OMC, IPG, PUB (Publicis), DN (Dentsu), TWLO-adjacent

## Sub-industries warranting override

- `social_interactive_media` — META, SNAP, PINS, MTCH. Driven by ad-revenue cycle, MAU/DAU metrics, regulator moves (DMA, KOSA, Section 230). Distinct source diet from legacy media.
- `streaming_video` — NFLX, DIS, WBD, PARA, SPOT. Driven by subscriber trends, content deals, content cost walks. Reliant on Nielsen ratings + app intelligence firms (Sensor Tower, data.ai).
- `wireless_carriers` — universal capex-cycle watchers. FCC spectrum auction news, handset launch cycles.

## Canonical sources to include

- Hollywood Reporter (hollywoodreporter.com)
- Variety (variety.com)
- Deadline (deadline.com)
- Axios Media + Pro Streaming (axios.com)
- MediaPost (mediapost.com)
- Digiday (digiday.com)
- The Information — media vertical
- Ad Age (adage.com)
- TheWrap (thewrap.com)
- Puck News (puck.news) — paid, strong on media/Hollywood C-suite
- Light Reading (lightreading.com) — telecom infra
- FierceTelecom (fiercetelecom.com)
- FierceWireless (fiercewireless.com)
- Mobile World Live (mobileworldlive.com)
- TeleGeography (telegeography.com)
- Matthew Ball (matthewball.co) — gaming/streaming strategist essays
- Protocol Media (archived), Axios Communicators newsletter
- **FCC news + orders** (fcc.gov/news-events/headlines) — US regulatory
- **Ofcom** (ofcom.org.uk/news-centre) — UK telecoms regulator
- **ARCEP** (arcep.fr/publications) — France
- **Bundesnetzagentur** (bundesnetzagentur.de) — Germany
- **MIC Japan** (soumu.go.jp) — Japan telecoms ministry
- **IAB Media reports** (iab.com/news)
- **Nielsen** + **Comscore** press (free press-release tier)

## Common pitfalls

- **META/GOOGL/SNAP move on ad-market data not in SEC filings.** IAB reports, Magna Global forecasts, Pivotal Research notes. Build X lists for ad-sector sell-side (Brian Wieser, Michael Nathanson, Rich Greenfield).
- **Streaming names move on subscriber leaks to trades (Variety/THR), not press releases.** Historical pattern: NFLX churn leaked via Deadline before official disclosure.
- **Disney/WBD/Paramount — content-deal announcements often in trade papers first.** Box office numbers from Box Office Mojo daily.
- **Telecoms: regulatory approval is the event, not product launch.** FCC spectrum auction outcomes, EU DMA designations, Ofcom wholesale rulings.
- **Japanese telcos (NTT, KDDI, SBG) file TDnet first, ~3h lead over English.** Nikkei Japanese leads English further on M&A scoops.
- **Gaming names have unique signals:** Steam concurrent user counts (steamdb.info), App Store/Play Store chart positions (data.ai, Sensor Tower), Twitch viewership.

## Language / jurisdiction specifics

- JP: NTT/KDDI/SoftBank Group all file TDnet JP-first.
- FR: ORAN (Orange) announcements via AMF + Les Echos lead English wires by 30–60 min.
- GB: VOD/BT-A use RNS; The Times + FT lead on M&A scoops.
- DE: DTE (Deutsche Telekom) — DGAP ad-hoc + Handelsblatt.
- CN/HK: Tencent (700 HK) — HKEX + Xinhua; Alibaba (9988 HK/BABA) — dual jurisdiction.

## Sanity numbers

- sector.communication_services.*: **18–30** entries.
- sub_industry overrides: **3–4** (social_interactive_media, streaming_video, wireless_carriers; optionally gaming).
- Overlay fill: IR_URL ≥95%, X handle ≥90% (communication-sector CEOs are loud on X), transcript provider 100%.
