# Deploying Resume Tailor (free) with access approval

You'll end up with a private URL like `https://resume-tailor-xxxx.onrender.com`:

- **You** sign in with the admin email and password you choose.
- **Anyone else** can only *request* access. They can't use anything until you approve them on the **Admin** page, and you can turn their access off at any time.
- Each approved user gets a daily cap on AI steps (default 40) so nobody can run up your Anthropic bill. You have no cap.
- Each person sees only their own resumes. Not even the admin page shows other people's resumes.

Setup takes about 20 minutes and uses three free accounts: **Neon** (database), **Render** (hosting) and the **Anthropic Console** (API key).

> Why not Vercel? Vercel runs apps as short-lived functions: they can't keep files or memory between requests, and long requests are cut off. This app saves your work between steps and runs AI passes that can take a few minutes, so it needs a normal always-running server like Render.

---

## 1. AI key (5 min): free Gemini **or** paid Claude

**Free: Google Gemini**
1. Go to **aistudio.google.com**, sign in with a Google account, and click **Get API key** → **Create API key**. Copy it.
2. You'll enter it as `GEMINI_API_KEY` in step 3, along with `LLM_PROVIDER` = `gemini`.

Trade-offs: the free tier has per-minute and per-day request limits (a full tailoring session uses about 6-10 requests), and **on the free tier Google may use what you submit, including resume text, to improve its products**. The app tells every user this on the upload screen. Rewrites are usually a bit less polished than Claude's; the honesty checks work the same either way.

**Paid: Claude (Anthropic)**

1. Go to **console.anthropic.com** → **API Keys** → **Create key** (name it `resume-tailor`). Copy it somewhere safe for step 3. It starts with `sk-ant-`.
2. Add credits under **Billing** (the API is prepaid, separate from any Claude.ai subscription), and **set a monthly spend limit** (for example $20). That's your hard ceiling no matter what happens.

## 2. Free database on Neon (5 min)

The app stores accounts and saved resumes here, so they survive restarts.

1. Go to **neon.tech** → sign up (GitHub login is easiest) → **Create project**, named `resume-tailor`. Pick the region closest to you, and remember it.
2. Open the project's **Connect** panel and copy the **connection string**. It looks like
   `postgresql://user:password@ep-something.region.aws.neon.tech/neondb?sslmode=require`

## 3. Host it on Render (10 min)

1. Go to **render.com** → sign up with GitHub, and allow it to access the `Resume_tailor` repository.
2. Click **New** → **Blueprint** → choose `RudraSharma3/Resume_tailor`, branch `claude/resume-tailor-tool`. Render reads `render.yaml` from the repo.
3. It asks for these values. They're secret: they stay in Render and never go into GitHub.

   | Name | What to enter |
   |---|---|
   | `ANTHROPIC_API_KEY` | your Claude key from step 1, or leave empty if you use Gemini |
   | `GEMINI_API_KEY` | your Gemini key from step 1, or leave empty if you use Claude |
   | `LLM_PROVIDER` | `gemini` for Gemini, or leave empty for Claude |
   | `ADMIN_EMAIL` | your email: this becomes your login |
   | `ADMIN_PASSWORD` | a **new** password, 12+ characters, not one you use anywhere else |
   | `DATABASE_URL` | the Neon connection string from step 2 |

4. Click **Apply**. The first build takes a few minutes. When it says **Live**, open the `.onrender.com` URL at the top of the service page.
5. Sign in with your `ADMIN_EMAIL` and `ADMIN_PASSWORD`. You'll see an **Admin** link in the top bar.

## 4. Letting someone in

1. Send them the URL. They click **Request access** and fill in name, email, password and an optional note.
2. You open **Admin** (the red number shows pending requests) → **Approve** or **Reject**.
3. To cut someone off later: **Admin** → **Turn off access** signs them out everywhere immediately. **Delete** also erases their saved resumes.

**Optional: get a phone notification for new requests.** Install the free **ntfy** app, subscribe to a long, hard-to-guess topic name (e.g. `rt-requests-8f3k2m9q`), then in Render → your service → **Environment**, add `NOTIFY_WEBHOOK_URL` = `https://ntfy.sh/rt-requests-8f3k2m9q`. A Discord or Slack webhook URL works too.

---

## Switching an existing deployment to free Gemini

1. Get a Gemini key (step 1 above).
2. Render → your service → **Environment** → add `GEMINI_API_KEY` = your key and `LLM_PROVIDER` = `gemini` → **Save Changes**.
3. After the restart, the pill in the app's top bar reads **Gemini · gemini-...**. To go back to Claude, set `LLM_PROVIDER` to `anthropic`.

## Good to know

- **The free plan sleeps** after about 15 minutes with no visitors, so the next visit takes about a minute to wake up. Your data is safe in Neon. Render's paid Starter plan keeps it always on.
- **Settings live in Render** → your service → **Environment**:
  - `AI_DAILY_LIMIT`: AI steps per user per day (default `40`, `0` = unlimited). One tailoring iteration = 1 step; the first analysis = 3.
  - `ALLOW_SIGNUP=0`: hides "Request access" so no new requests come in. People you already approved keep working.
  - Changing `ADMIN_PASSWORD` signs out every session you had open.
- **Updates**: when new code is pushed to the branch, Render redeploys automatically.
- **Make the GitHub repo private** if you don't want others reading the code: GitHub → repo **Settings** → **General** → **Danger Zone** → **Change visibility**. Render keeps working.
- **Running on your own computer** still works with no sign-in: `python run.py` (see README).

## What protects what

| Threat | Protection |
|---|---|
| A stranger finds the URL | Every page and API call requires sign-in. New accounts stay "pending" until you approve them. |
| Password guessing | Passwords are hashed with scrypt; 6 wrong tries locks that email for 15 minutes. |
| Someone burns your API credit | Per-user daily AI cap, plus the spend limit you set in the Anthropic Console. |
| One user reading another's resume | Every resume is tied to its owner; anyone else gets "not found". |
| A malicious site acting as a signed-in user (CSRF) | Every write needs a custom header that other sites can't send, and the cookie is `SameSite=Lax`. |
| A stolen session cookie | The cookie is HttpOnly and Secure (HTTPS only). Turning off access or changing your admin password ends sessions instantly. |
| Your API key leaking | It lives only in Render's environment settings, never in the code or the browser. |
