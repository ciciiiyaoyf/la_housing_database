# Additional Variables Audit

Audit date: 2026-08-03. Scope: variables in `LA_CA_Housing_Additional_Variables_Methodology.docx`.

| Variable | Status | Implemented construction / stopping point |
|---|---|---|
| Closing-cost scenarios | Available | `closing_cost_2pct`, `closing_cost_3pct`, and `closing_cost_5pct` equal Redfin county median sale price times the labeled scenario. They are scenarios, not observations. Loan-amount sensitivities remain absent because the analytical panel has no single agreed mortgage-amount scenario. |
| One-unit housing permits | Available | `permits_1_unit` is loaded from Census BPS. It is explicitly an authorization/permit proxy, not a start. |
| IRS net migration | Available | Annual net returns, exemptions, and AGI are inflow minus outflow. Exemptions are the preferred people proxy. The official IRS site still publishes county inflow/outflow and gross files through 2022-2023; the repo snapshot ends in 2023. |
| Zillow price-to-rent | Available | `price_to_rent_ratio = ZHVI / (12 * ZORI)` at the same county-month using the active smoothed SA Zillow series. |
| ACS rental availability | Available | The personalized Census key was used to archive 15 LA County ACS 5-year vintages for 2010-2024. Each request retrieves B25004 for-rent vacant units/MOE and the same-vintage B25003 renter-occupied denominator. `acs_rental_availability_rate = for_rent_vacant / (renter_occupied + for_rent_vacant)` is stored at county annual frequency; observed rates range from 0.032351 to 0.045105 in the archived vintage. |
| HUD-USPS quarterly residential vacancy proxy | Available (restricted) | Authenticated official rebuild completed for 2008 Q1-2026 Q1. All 73 California NCWM responses, the 2026 Q1 tract-to-ZIP crosswalk, and Census county-name reference are archived with checksums outside Git. The active rate is `(short-term vacant + long-term vacant) / total residential addresses`; it covers all residential addresses and is not renter-only or rental-listing inventory. |
| True monthly rental-listing inventory | Unavailable | No verified public historical Zillow rental-listing stock endpoint was found. ZORI is a rent index and Redfin active listings are for-sale. HUD/USPS remains a separate quarantined all-residential vacancy proxy, not rental inventory. |
| California homeowners insurance | Available | Added official California DOI 2008-2023 Homeowners Multi-Peril written premium, exposure, and `ca_homeowners_avg_written_premium = written_premium / written_exposure`. Native geography/frequency are California/annual. |
| Non-rent-controlled rent growth | Not constructed | ZORI growth exists, but an auditable local/state policy crosswalk and unit-level or ACS structural exposure weights do not. Stopped before multiplying ZORI by an unsupported exposure share. |

The database must continue to preserve native frequency and geography: annual state insurance is not an LA monthly observation; annual ACS availability is not monthly listings; permits are not starts; and exposure-adjusted rent growth is not observed controlled-unit rent growth.
