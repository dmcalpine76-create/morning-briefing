# GitHub Deployment Setup Guide
# Doug's Morning Briefing — Cloud Hosting via GitHub

## Overview

After setup, your briefing will:
- Run automatically at 5:30am AEST every day
- Be accessible at https://YOUR-USERNAME.github.io/morning-briefing
- Be triggerable from your phone via the GitHub mobile app
- Work fully on mobile, tablet and desktop

---

## Step 1 — Create a GitHub account and repository

1. Go to https://github.com and sign up (free)
2. Click **+** → **New repository**
3. Name: `morning-briefing`
4. Visibility: **Private** (keeps your briefing private)
5. Click **Create repository**

---

## Step 2 — Push your code to GitHub

Open Command Prompt in your project folder and run:

```
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/morning-briefing.git
git push -u origin main
```

Replace YOUR-USERNAME with your GitHub username.

If you don't have Git installed: https://git-scm.com/download/win

---

## Step 3 — Set repository to Public and enable GitHub Pages

GitHub Pages is free on public repositories. Your briefing content is
protected by a password (see Step 4a), so making the repo public is safe —
anyone who finds the URL just sees a password prompt.

1. Go to your repository on GitHub
2. **Settings** → scroll to **Danger Zone** → **Change visibility** → **Public**
3. **Settings** → **Pages** (left sidebar)
4. Source: **Deploy from a branch**
5. Branch: **main** / folder: **/docs**
6. Click **Save**

Your briefing will be available at:
`https://YOUR-USERNAME.github.io/morning-briefing`

Bookmark this URL on your phone and all your devices.

---

## Step 4 — Add your secrets to GitHub

Go to: Repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add each of these:

| Secret Name            | Value                                      |
|------------------------|--------------------------------------------|
| ANTHROPIC_API_KEY      | Your sk-ant-... key                        |
| BRIEFING_PASSWORD      | A password you choose (e.g. Morning2026!)  |
| OUTLOOK_CLIENT_ID      | f732d14f-a1a2-45d2-aad9-6179081bebae      |
| OUTLOOK_TENANT_ID      | 5d39e945-1fc1-4277-8055-d07e99d21851      |
| BRIEFING_EMAIL_TO      | Your email address                         |
| OUTLOOK_TOKEN_CACHE    | (see Step 5 below)                         |
| GMAIL_CLIENT_ID        | Your Google OAuth client ID (if using)     |
| GMAIL_CLIENT_SECRET    | Your Google OAuth client secret (if using) |
| GMAIL_TOKEN_CACHE      | (see Step 5 below, if using Gmail)         |

The BRIEFING_PASSWORD is the password you'll enter on your phone/iPad to
unlock the briefing. Choose something you'll remember. It's stored as a
hash in the page — the plain text never appears anywhere public.

Once entered on a device, the password is remembered so you won't need
to type it again on that device.

---

## Step 5 — Upload your Outlook (and Gmail) token

Run this in your project folder:

```
py upload_tokens.py
```

This prints the content of your token files.
Copy each value and paste it into the corresponding GitHub Secret.

You will need to repeat this step every 90 days when Microsoft expires
the Outlook refresh token. The script reminds you what to do.

---

## Step 6 — Test the workflow

1. Go to your repository on GitHub
2. Click the **Actions** tab
3. Click **Generate Morning Briefing** in the left sidebar
4. Click **Run workflow** → **Run workflow**
5. Watch the run complete (takes 5-15 minutes)
6. Visit your GitHub Pages URL to see the briefing

---

## Step 7 — Install GitHub mobile app for phone triggering

1. Install **GitHub** from the App Store or Google Play (free)
2. Sign in with your GitHub account
3. Navigate to your repository
4. Tap **Actions** → **Generate Morning Briefing** → **Run workflow**

That's your one-tap morning trigger from your phone.

---

## Refreshing the Outlook token (every 90 days)

When you see "Outlook token missing or expired" in the GitHub Actions log:

1. On your PC, run: `py outlook_email.py setup`
2. Complete the browser login
3. Run: `py upload_tokens.py`
4. Copy the OUTLOOK_TOKEN_CACHE value
5. Go to GitHub → Settings → Secrets → Update OUTLOOK_TOKEN_CACHE

---

## How the scheduling works

The cron schedule `30 19 * * *` runs at 7:30pm UTC which is:
- 5:30am AEST (UTC+10) — standard time
- 6:30am AEST (UTC+11) — daylight saving

Adjust the cron in .github/workflows/generate.yml if needed:
- For 5:00am AEST standard time: `0 19 * * *`
- For 6:00am AEST standard time: `0 20 * * *`

---

## Troubleshooting

**Actions tab shows workflow failing:**
- Click the failed run to see the logs
- Most common causes: expired Outlook token, low Anthropic API credit

**GitHub Pages not updating:**
- Check the Actions run completed successfully
- Pages can take 2-3 minutes to update after a push

**Briefing URL shows placeholder page:**
- The workflow hasn't run successfully yet
- Trigger a manual run from the Actions tab
