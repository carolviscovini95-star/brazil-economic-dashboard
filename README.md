# Brazil Economic Performance Dashboard

The dashboard tracks six official indicators to explore how price stability and real growth evolve alongside labor conditions, the exchange rate and long-run educational attainment. Plotly charts support hover and zoom. The sidebar filters histories, exports selected or complete observations, resets the date range, and refreshes the data. KPI cards always show the latest published figures in the active snapshot.

## Run locally

Use Python 3.12 from the project directory.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. The bundle contains a validated snapshot, which the app reads on launch. To retrieve a new snapshot and independently cross-check one observation per indicator, run:

```bash
python data_fetcher.py
python verify_data.py
```

After a successful refresh, open the local URL printed by Streamlit. Keep the sidebar open so the refresh status and timestamp appear in the image. Use the browser’s screenshot tool at a desktop viewport to capture the overview and comparison chart. Use full-page capture for all charts.

## Deploy and schedule

1. Commit the project and its included evidence to a GitHub repository. Keep the workflow on the default branch, enable Actions, and ensure the repository allows the workflow’s `GITHUB_TOKEN` to push data commits to that branch. Check branch protection or rulesets.
2. In Streamlit Community Cloud, create an app with that repository, default branch and `app.py` as the entry point. Select Python 3.12 in advanced settings. The repository includes the dependencies and the initial snapshot. No data API key is required.
3. Run **Refresh official Brazil economic data** once using **Run workflow** on GitHub’s Actions tab. Confirm the commit contains the updated `data/state.json` and the corresponding refresh log. Verify that the deployed app reflects the repository update after Streamlit syncs the branch.
4. The workflow is scheduled for 11:00 UTC, corresponding to 08:00 in Brasília under current UTC−03:00 rules. It runs while the app is closed. GitHub may delay runs and disables schedules in inactive public repositories after 60 days. Review the schedule if Brasília’s time-zone rules change.

A dashboard-triggered refresh writes the data and evidence on the running Streamlit instance. Its filesystem can be reset on reboot or redeploy. GitHub Actions is the durable publishing path. The bundled workflow was prepared for deployment and has not run on GitHub.

## Data, verification and maintenance

- [Data dictionary](DATA_DICTIONARY.md) records the definitions, filters, date conventions and units. Read it before changing series selectors.
- `data/state.json` holds the pointer to the active content-addressed CSV in `data/snapshots/`, the last successful refresh and the most recent attempt. The pointer is switched atomically after all six series pass validation. The refresh log records Brasília timestamps, source URLs, counts, dates and validation outcomes. An API or validation failure records an error, retains the active CSV and timestamp, and marks the Actions run as failed. Successful refreshes advance the timestamp even when observations are unchanged.
- Coverage checks reject duplicates, non-finite or implausible values, missing monthly and quarterly periods, unexpectedly stale or gapped daily data, and disappearance of previously accepted dates. Investigate a legitimate source withdrawal before resetting any baseline. Production validation checks source structure and data integrity. It does not independently validate every number.
- [Verification results](verification/verification.csv) cross-check one stored observation per indicator. IBGE references use its separate Aggregates API with date, unit, territory and classification checks. Selic uses the official SGS CSV endpoint, and the exchange rate uses BCB’s PTAX OData service. The IBGE and SGS checks compare publication interfaces backed by the same official statistics. The app reads the already published cumulative IPCA and real GDP growth series directly. There is no dashboard-side derivation of those rates.
- The annual education series starts in 2016 and leaves unavailable years blank. It measures long-run development. The chart comparing the Selic target and inflation is descriptive. Different release schedules and overlapping unemployment windows matter when reading chart movements.
- For historical reproducibility, Git retains prior snapshot files. For long-running repositories, periodically archive older snapshot files and logs outside the live checkout while retaining the active snapshot and the file referenced by the verification table.
