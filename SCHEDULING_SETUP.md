# Daily Scheduling & Email Setup Guide

## Step 1 — Add two new lines to your .env file

Open your .env file in Notepad and add these two lines:

    OUTLOOK_TENANT_ID=5d39e945-1fc1-4277-8055-d07e99d21851
    BRIEFING_EMAIL_TO=your.email@yourcompany.com

Replace the email address with whichever address you want to receive the briefing.
Your .env should now have four lines total:

    ANTHROPIC_API_KEY=sk-ant-...
    OUTLOOK_CLIENT_ID=f732d14f-a1a2-45d2-aad9-6179081bebae
    OUTLOOK_TENANT_ID=5d39e945-1fc1-4277-8055-d07e99d21851
    BRIEFING_EMAIL_TO=your.email@yourcompany.com


## Step 2 — Add Mail.Send permission in Azure

The app needs permission to send email on your behalf.

1. Go to portal.azure.com
2. Microsoft Entra ID → App registrations → Daily Briefing
3. API permissions → Add a permission → Microsoft Graph → Delegated
4. Search for Mail.Send → tick it → Add permissions
5. Click "Grant admin consent for [your org]" → Yes


## Step 3 — Re-run auth to pick up the new permission

Because we added a new scope (Mail.Send), you need to re-authorise once:

    py outlook_email.py setup

Go to microsoft.com/devicelogin, enter the code, sign in.
This saves a new token with the Mail.Send permission included.


## Step 4 — Test the email send

    py briefing.py

If BRIEFING_EMAIL_TO is set, it will send the briefing to you at the end.
Check your inbox — it arrives as a fully rendered HTML email.


## Step 5 — Schedule with Windows Task Scheduler

1. Copy run_briefing.bat into your project folder
2. Open Task Scheduler (search in Start menu)
3. Click "Create Basic Task" in the right panel
4. Name: Morning Briefing
5. Trigger: Daily
6. Start time: 05:30:00 AM  (or whatever time suits you)
7. Action: Start a program
8. Program/script: Browse to run_briefing.bat in your project folder
9. Start in: paste your full project folder path, e.g.
   C:\Users\dmcal\OneDrive - State Gas\Documents\Current document editing\new AI projects\morning briefing system
10. Finish

To verify it works, right-click the task → Run.
Check the output\ folder for a new timestamped subfolder and a log file.


## How it works end-to-end

5:30 AM  →  Task Scheduler runs run_briefing.bat
             ↓
             py briefing.py runs, fetches news + emails + market data
             ↓
             Saves briefing.html, email.html, topics.html to output\2026-03-18_05-30\
             ↓
             Sends the briefing.html to your inbox via Graph API
             ↓
6:00 AM  →  Email arrives in your inbox
             Open it on your phone, tablet or PC
             All links to email.html and topics.html work if you open the
             local file in a browser (or serve with py -m http.server)


## Troubleshooting

- Briefing not arriving? Check output\briefing_log_*.txt for error messages
- Token expired? Run: py outlook_email.py setup
- Task not running? Open Task Scheduler, right-click task → Properties →
  make sure "Run whether user is logged on or not" is NOT checked
  (simpler to keep it as "Run only when user is logged on")
