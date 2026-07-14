# Royal Road Longitudinal Collector

The repository is ready for the one-time bootstrap installation.

Open **Actions → Bootstrap collector → Run workflow**. The workflow will install and test collector v0.2.1, remove the temporary package/bootstrap files, and leave the hourly collector ready to activate.

After bootstrap, configure the `RR_USER_AGENT` repository secret and run **Royal Road longitudinal snapshot** once manually. Scheduled collection then runs at minute 7 of every hour.
