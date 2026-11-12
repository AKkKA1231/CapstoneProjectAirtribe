# Production Deployment: Supabase → Render → Vercel

| Layer | Platform | What runs there |
|---|---|---|
| Database | Supabase Postgres | pgvector + pgcrypto, tables from Alembic migrations, RLS enabled (migration 0002) |
| Gateway API | Render (Docker web service) | `backend/Dockerfile` runtime stage; on boot runs `alembic upgrade head`, `prism-seed`, then uvicorn |
| Operations console | Vercel (static Vite build) | `frontend/`, calls the gateway through `VITE_API_BASE_URL` |

Secrets live only in Render's environment settings and in your local, git-ignored
`.env.production.local`. Nothing secret is committed.

## 1. Supabase (database)

1. Create a new project (pick the region closest to your Render region, e.g. Singapore).
2. **Project Settings → Database → Connection string → Session pooler** and copy the URI.
   It looks like `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   - Use the **session pooler (port 5432)**, not the direct `db.<ref>.supabase.co` host
     (IPv6-only; Render cannot reach it) and not the transaction pooler on 6543
     (it breaks asyncpg prepared statements).
   - The app rewrites `postgresql://` to `postgresql+asyncpg://` automatically.
3. No manual SQL needed: the `vector` and `pgcrypto` extensions and all tables are
   created by the gateway's migrations on its first boot.

## 2. Render (gateway)

1. Render dashboard → **New → Blueprint** → select this GitHub repo. Render reads `render.yaml`.
2. Fill in the prompted secrets:

| Variable | Value |
|---|---|
| `PRISM_DATABASE_URL` | Supabase session-pooler URI from step 1 |
| `PRISM_AUTH_PEPPER` | from `.env.production.local` |
| `PRISM_ADMIN_API_KEY` | from `.env.production.local` (this is the console "Admin key") |
| `PRISM_BOOTSTRAP_TENANT_KEY` | from `.env.production.local` (Bearer key for API clients) |
| `GROQ_API_KEY` | a **new** key from https://console.groq.com/keys |
| `PRISM_CORS_ALLOWED_ORIGINS` | `["https://placeholder.invalid"]` for now; set to the Vercel URL in step 4 |

3. Deploy. When it's healthy, `https://<service>.onrender.com/health/ready` returns 200.

> Free Render services sleep after ~15 minutes idle; the first request after that
> takes ~1 minute. Use a paid instance for the recording if that matters.

## 3. Vercel (operations console)

1. **Add New → Project** → import this repo → **Root Directory: `frontend`**
   (framework and build settings come from `frontend/vercel.json`).
2. Environment variable: `VITE_API_BASE_URL = https://<service>.onrender.com`
   (no trailing slash). It is public by design: it is only the API address.
3. Deploy and note the URL, e.g. `https://prism-console.vercel.app`.

## 4. Connect them

On Render, set `PRISM_CORS_ALLOWED_ORIGINS` to `["https://<your-vercel-url>"]` and
save; Render redeploys.

## 5. Live end-to-end check

```powershell
$api = 'https://<service>.onrender.com'
Invoke-RestMethod "$api/health/ready"

$tenant = @{ Authorization = "Bearer $env:PRISM_BOOTSTRAP_TENANT_KEY" }
Invoke-RestMethod "$api/v1/models" -Headers $tenant

$body = @{ model = 'fast'; max_tokens = 60
  messages = @(@{ role = 'user'; content = 'Say hello from production.' }) } | ConvertTo-Json -Depth 5
Invoke-RestMethod "$api/v1/chat/completions" -Method Post -Headers $tenant -ContentType 'application/json' -Body $body

$admin = @{ Authorization = "Bearer $env:PRISM_ADMIN_API_KEY" }
Invoke-RestMethod "$api/ops/usage" -Headers $admin
```

Load the variables first from the local file (values never leave your machine):

```powershell
Get-Content .env.production.local | Where-Object { $_ -match '^[A-Z_]+=' } |
  ForEach-Object { $k, $v = $_ -split '=', 2; Set-Item "env:$k" $v }
```

Then open the Vercel URL, paste `PRISM_ADMIN_API_KEY` into **Admin key** and click **Connect**.

## Security notes

- The Groq key that was pasted into an earlier chat must be revoked; use a fresh one on Render.
- Rotate `PRISM_ADMIN_API_KEY` / `PRISM_BOOTSTRAP_TENANT_KEY` by changing them on Render
  and redeploying (seeding is idempotent).
- Production startup refuses development secrets, wildcard CORS and public API docs.
