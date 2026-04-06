"""
upload_tokens.py  —  Prepare token files for GitHub Secrets
-------------------------------------------------------------
Run this locally after completing outlook_email.py setup or gmail_email.py setup.
It prints the token values you need to paste into GitHub Secrets.

Usage:
    py upload_tokens.py

Then copy each value into GitHub:
  Repository → Settings → Secrets and variables → Actions → New repository secret
"""

import json
from pathlib import Path


def read_token(path: Path, name: str):
    if not path.exists():
        print(f"  ⚠️  {name}: file not found at {path}")
        print(f"       Run the setup command first, then re-run this script.\n")
        return

    content = path.read_text(encoding="utf-8").strip()
    if not content:
        print(f"  ⚠️  {name}: file is empty\n")
        return

    print(f"\n{'─' * 60}")
    print(f"  GitHub Secret name:  {name}")
    print(f"  GitHub Secret value: (copy everything between the lines)")
    print(f"{'─' * 60}")
    print(content)
    print(f"{'─' * 60}\n")


print("\n🔑  Token Export for GitHub Secrets")
print("=" * 60)
print()
print("Copy each value below into GitHub:")
print("  Repo → Settings → Secrets and variables → Actions → New repository secret")
print()

read_token(Path(".outlook_token_cache.bin"), "OUTLOOK_TOKEN_CACHE")
read_token(Path(".gmail_token_cache.json"),  "GMAIL_TOKEN_CACHE")

print("\nOther secrets you need to add to GitHub:")
print("""
  Secret name              Value
  ─────────────────────────────────────────────────────
  ANTHROPIC_API_KEY        your sk-ant-... key
  OUTLOOK_CLIENT_ID        f732d14f-a1a2-45d2-aad9-6179081bebae
  OUTLOOK_TENANT_ID        5d39e945-1fc1-4277-8055-d07e99d21851
  BRIEFING_EMAIL_TO        your work email address
  GMAIL_CLIENT_ID          your Google OAuth client ID (if using Gmail)
  GMAIL_CLIENT_SECRET      your Google OAuth client secret (if using Gmail)
""")
print("After adding all secrets, push your code to GitHub and the workflow will run automatically.\n")
