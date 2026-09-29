# Free private remote access: Netlify + ngrok

The warehouse app and SQLite database keep running on this Windows PC. ngrok
provides an HTTPS tunnel; Netlify provides a private website and signs every
proxied request. This uses no Render service or paid disk. It is available only
while this PC, the warehouse process, and ngrok are running.

The app has no individual user login. On Netlify Free, a private project is
accessible only to its Team Owner. Do not make the project public. Do not use
the ngrok address to access the app directly.

## 1. Create an ngrok Free account

1. Sign up at https://dashboard.ngrok.com/signup and choose Free. Copy the
   assigned HTTPS dev domain from the Domains page, such as
   `example.ngrok-free.app`. Use the domain assigned to **your** account.
2. On this PC, install ngrok from https://ngrok.com/download/windows. The
   documented WinGet command is `winget install ngrok -s msstore`.
3. In the ngrok dashboard, copy the authtoken. In PowerShell, run
   `ngrok config add-authtoken "YOUR_AUTHTOKEN"`. Keep the token private and
   do not put it in this repository or a chat.

## 2. Configure Netlify's target

1. Open `netlify.toml`. Replace only
   `replace-with-ngrok-host.invalid` with the assigned ngrok **hostname**.
   Keep `https://` and `/:splat`, and keep the `signed` and `headers` lines.
2. Commit and push this hostname change to GitHub `main`. The hostname is not
   a secret; the signing secret must never be committed.
3. Create a Netlify project from
   `https://github.com/edjay17-afk/Storehub` in the **Ludger Cherish** account.
   Choose branch `main`, leave the build command empty, and set the publish
   directory to `netlify-public`. If Netlify detects `netlify.toml`, use its
   settings. Wait for the first production deploy.
4. In **Project configuration > General > Visitor access > Project
   visibility**, select **Private** for production and previews. Confirm the
   project is owned by the intended Netlify account.

## 3. Connect Netlify to this PC

1. In Netlify, open **Project configuration > Environment variables**. Add a
   variable named `NETLIFY_PROXY_SECRET` with a long random value (at least
   32 random bytes). Generate one in PowerShell with
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Scope the
   Netlify variable to **Runtime** and production. Keep the value private.
   Redeploy the project if Netlify asks.
2. In Netlify, copy the project's **Site ID** from **Project configuration >
   General > Project details**. This is a UUID, not the site name.
3. On this PC, open PowerShell in the repository and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start-private.ps1 -SiteId YOUR_NETLIFY_SITE_ID
   ```

   When prompted, paste the exact Netlify proxy secret from step 1. The
   launcher enables signed-request checks, loads local StoreHub credentials,
   starts the scheduled sync, and binds to `127.0.0.1:5077`. Keep this window
   open. Stop any existing local app using port 5077 first.

4. Open a second PowerShell window and run:

   ```powershell
   ngrok http 5077 --url https://YOUR_ASSIGNED_NGROK_DOMAIN
   ```

   Keep this window open too. The ngrok domain must match `netlify.toml`.
   The `ngrok-skip-browser-warning` request header in the Netlify proxy rule
   keeps ngrok's Free interstitial out of the proxied responses.

5. Sign in to Netlify as the project owner and open the Netlify site URL.
   Check **Products**, a document page, and a read-only StoreHub page. On a
   different browser that is not signed in to the owner account, confirm the
   Netlify URL blocks access. The direct ngrok root should return HTTP 403;
   ngrok may show its warning first in a normal browser.

## Operation and limits

- Start the warehouse process before ngrok after each PC restart. Stop ngrok
  whenever the app is not needed. No router port forwarding is needed.
- The SQLite database, source CSVs, and API credentials remain in local
  `data/`; they are never uploaded to Netlify or GitHub.
- Back up `data/warehouse.db` regularly with the app stopped or SQLite's
  backup API. The PC is the sole live copy of the warehouse data.
- ngrok Free currently caps traffic at 1 GB and 20,000 HTTP requests. Netlify
  Free has its own usage limits. A busy catalog or large photo uploads can
  exhaust these limits; check both dashboards.
- Netlify Free private project access is for the Team Owner only. If staff
  need separate logins, this access model needs to change.
- `render.yaml` is a separate **paid** Render option. Do not deploy that
  Blueprint when you want a $0 setup.
