# Automatic BEA updates

The hosted workflow checks BEA at 15:17 and 21:17 UTC on weekdays. It does not assume a fixed monthly release date. Unchanged recent data produce no commit. BEA failures are retried; a failed calculation or export never commits a release. GitHub emails workflow failures according to the account's Actions notification settings.

Sources: BEA NIUnderlyingDetail U20404 (monthly price indexes) and U20405 (monthly nominal expenditures), downloaded directly from https://apps.bea.gov/api/. The API key is the encrypted repository secret `BEA_API_KEY`. No credential is stored in source, fixtures or logs. All 130 categories are explicitly mapped in categories.csv. Package tours uses DHOLRG/DHOLRC. Foreign-travel offsets retain the source Stata sign convention.

## Calculation and benchmarks

AR(1), AR(3), AR(12), each with two, three and four consecutive same-sign residuals; six groups and positive, negative and net components. The fitting dates are t-120 through t inclusive, matching the production Stata implementation. Lagged observations preceding that interval remain available to the regression. All residuals in a run use the same reference month's fitted model. Shares use lagged expenditures. Sequential float32 accumulation preserves Stata share precision.

Ordinary updates replace the latest 16 months and preserve earlier observations. September **data** trigger a full historical benchmark, including when the September update was missed and the next available release is October. Model-version changes also require a full rebuild. A checksum detects a manual CSV upload and forces a fresh full rebuild instead of treating that upload as the validated baseline. Manual Actions > Update ISMI from BEA supports validate (no published files changed), update, or benchmark. Full history can be requested explicitly. The initial corrected release rebuilds January 1969 through October 2026.

## Revision corrections

The prior dashboard called the services series non-housing while the upstream construct.do included housing. This implementation excludes shrm, shtm, srdm and shom from BOTH the services numerator and denominator, matching the revised paper's definition. Headline and the other groups retain their definitions.

Annual PCE inflation is now 100*(P[t]/P[t-12]-1). The old download_haver_data.do divided the change by the current price level. This correction affects the optional PCE overlay, not the ISMI's monthly-log-change regressions.

Do not manually overwrite the dashboard with an old Haver export: the original shared Stata scripts still contain these old definitions. The new pipeline replaces that dashboard update route. The shared scripts, research datasets, paper and Overleaf files were not modified by this migration.

## Validation

Before the two corrections, a full BEA rebuild matched all 112,428 published Stata index values (694 months x 162 series) at CSV precision. Public BEA prices and expenditures were checked against the available Haver inputs. After the corrections, an independent Stata 19 run matched 2,916 values at six decimal places AND at three-decimal CSV precision: all 162 series for 18 dates, including the latest 16 months, January 1969 and October 1993. The fixed public BEA fixture and independent Stata output are in tests/fixtures. Neither fixture contains Haver data or API credentials. Only the 27 services columns and pce_yoy change relative to the prior full rebuild.

Run `python -m pip install -r automation/requirements.txt` followed by `python -m unittest discover -s automation/tests -v`. Tests cover unchanged-release no-ops, explicit rebuilds, manual-upload detection, Stata parity, revised group membership and inflation, annual benchmark/catch-up, preserving the baseline, CSV/Excel consistency, incomplete data, and failed-download protection. Every hosted update runs these tests first, including on Linux to check numerical portability.

Exports validate dates, schema, finite values, group denominators, index ranges and positive-minus-negative identities. Both Excel and CSV are prepared before installation; all nine Excel sheets are reopened and checked. Git receives the pair and state in one commit. A concurrent upload causes git push to fail rather than overwrite the other change. Actions artifacts retain candidate downloads and the report for 30 days. Future BEA revisions can legitimately differ from the prior release; fixed-vintage Stata reference tests remain mandatory.

## Other audit findings

The old monthly and benchmark scripts compute diagnostic ptot/ntot counts by dividing the accumulating total by N inside the category loop. That is not the average count intended by the comments. Those diagnostic counts are not exported to this dashboard; the new pipeline does not use them.

The inactive ARIMA(3) branch of construct.do has a `lqui ocal` typo and malformed coefficient macro references; the active monthly workflow sets arima=0 and uses OLS. The unfinished public-data Stata downloader and webpage_update_auto.do are not used. These files were audited as migration inputs, not silently repaired in the shared research folder. This audit covers the dashboard's acquisition, index construction, monthly/benchmark updates and exports; it is not a validation of all manuscript regressions or causal estimates.

## Publication and activation

GitHub Pages uses an Actions deployment. The update workflow explicitly invokes publication after its data commit, because a GITHUB_TOKEN commit does not trigger a second push workflow. Only the dashboard HTML, CSV, Excel, analytics preference page, preview and index alias are published; API code and test fixtures are not copied to the Pages artifact. Website analytics preferences are preserved.

Scheduled calculation is gated by repository variable `BEA_AUTO_UPDATE_ENABLED=true`, enabled only after hosted validation and the initial benchmark succeed. Set it to false to pause automatic updates. The dashboard remains available. The BEA secret can be rotated in repository Settings > Secrets and variables > Actions.

Download timeouts observed during setup came from BEA full-history API requests. Compressed responses and bounded retries resolved them. A temporary BEA outage delays an update; it does not replace the site with incomplete data.

To reproduce the independent Stata check, run `python automation/tests/prepare_stata_reference.py /path/to/scratch`, open Stata in that folder and run `do corrected_check.do`. This requires Stata only for verification, never for hosted updates.
