# Private Netlify deployment

The live warehouse app runs on a Render Python web service with a persistent
disk. Netlify provides the private URL and proxies every request to Render.
The Render backend rejects direct requests unless Netlify signs the proxy
request. Do not deploy the repository as a static site without the backend.

## Before provisioning

1. Keep `data/` out of Git. It contains the merchant database, CSV exports,
   scanner state and credentials.
2. Confirm the Render Starter service and 2 GB persistent disk price in the
   account before creating them. As of September 2026, Render lists the web
   service at $7/month and disk storage at $0.25/GB-month ($0.50/month for
   2 GB), plus any usage charges. The Blueprint in `render.yaml` requests both.
3. Sign in to Netlify and Render. Create a **private** Netlify project from
   this repository, keeping production and previews private. The checked-in
   `netlify.toml` restricts the publish directory and targets an invalid
   backend hostname until Render is ready; it does not publish repository files.
   On Netlify Free
   and Personal plans, only the team owner can view private projects; staff
   access requires a suitable plan and invited users.
4. Once Render has a service URL, replace the `.invalid` hostname in
   `netlify.toml` with its exact HTTPS hostname and redeploy Netlify. Never
   put a secret value in this file.

## Secrets and backend

Create a long random `NETLIFY_PROXY_SECRET` and set the **same** value on
Render and Netlify. On Netlify, its scope must include Runtime so signed
proxy redirects can use it. Set `NETLIFY_SITE_ID` on Render to the Netlify
project's site ID. Set `STOREHUB_USERNAME` and `STOREHUB_API_TOKEN` on Render
using its secret environment variables; do not commit them. Render's web
service must use one Gunicorn worker so the scheduled StoreHub sync runs once.

Render mounts its persistent disk at `/opt/render/project/src/data`. The
service can start before the data is copied, but it returns 503 for warehouse
pages until the database is restored. `/healthz` reports process health and
whether the database file exists.

## Data transfer

Back up the live local database consistently with SQLite's backup API, then
transfer the backup to Render's disk as `warehouse.db`. Also transfer the
original `Products.csv`, `Stock_Transfer_09-28-2026.csv`, and
`Purchase_Orders_09-28-2026.csv` from the local `data/` directory. Render
documents SCP transfer after SSH is configured. Restart the Render service
after transferring these files, then verify catalog counts, recent documents,
photos and CSV exports before using Netlify for live writes. Keep the local
database as a recovery copy.

Do not make the Netlify project public: the app has no per-user application
login. Do not allow direct public access to the Render backend by disabling
`REQUIRE_NETLIFY_PROXY`.
