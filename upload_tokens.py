"""
upload_tokens.py  —  Upload Outlook token to GitHub Secrets automatically
-------------------------------------------------------------------------
Reads .outlook_token_cache.bin and uploads it directly to your GitHub
repository as the OUTLOOK_TOKEN_CACHE secret via the GitHub API.

Requires a GitHub Personal Access Token with secrets write permission.
Set it in .env as GITHUB_UPLOAD_TOKEN, or enter it when prompted.

Usage:
    py upload_tokens.py

Run this after refreshing your Outlook token:
    py outlook_email.py setup
    py upload_tokens.py
"""

import os, sys, json, base64, subprocess, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

REPO_OWNER = "dmcalpine76-create"
REPO_NAME  = "morning-briefing"
API_BASE   = "https://api.github.com"
HEADERS    = lambda tok: {
    "Authorization":        f"Bearer {tok}",
    "Accept":               "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Token files to upload → GitHub secret names
SECRETS = [
    (Path(".outlook_token_cache.bin"), "OUTLOOK_TOKEN_CACHE"),
]


def ensure_nacl():
    try:
        import nacl.public  # noqa
    except ImportError:
        print("  Installing PyNaCl (required for secret encryption)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "PyNaCl", "--quiet",
                        "--break-system-packages"], check=False)
        subprocess.run([sys.executable, "-m", "pip", "install", "PyNaCl", "--quiet"], check=False)


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Encrypt value with repo public key using libsodium sealed box."""
    from nacl import encoding, public as nacl_public
    pk_bytes  = base64.b64decode(public_key_b64)
    pk        = nacl_public.PublicKey(pk_bytes)
    box       = nacl_public.SealedBox(pk)
    encrypted = box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def get_public_key(github_token: str) -> tuple[str, str]:
    """Return (key_id, public_key_b64) for the repo."""
    r = requests.get(
        f"{API_BASE}/repos/{REPO_OWNER}/{REPO_NAME}/actions/secrets/public-key",
        headers=HEADERS(github_token), timeout=15)
    if r.status_code == 401:
        raise RuntimeError("GitHub token rejected (401) — check GITHUB_UPLOAD_TOKEN")
    if r.status_code == 404:
        raise RuntimeError(f"Repo {REPO_OWNER}/{REPO_NAME} not found (404)")
    r.raise_for_status()
    d = r.json()
    return d["key_id"], d["key"]


def put_secret(github_token: str, key_id: str, public_key: str,
               secret_name: str, secret_value: str) -> bool:
    """Upload one encrypted secret. Returns True on success."""
    encrypted = encrypt_secret(public_key, secret_value)
    r = requests.put(
        f"{API_BASE}/repos/{REPO_OWNER}/{REPO_NAME}/actions/secrets/{secret_name}",
        headers=HEADERS(github_token),
        json={"encrypted_value": encrypted, "key_id": key_id},
        timeout=15)
    return r.status_code in (201, 204)


def main():
    print("\n  Morning Briefing — Upload Tokens to GitHub")
    print("  " + "─" * 44)

    # ── Get GitHub token ──────────────────────────────────────────────────
    github_token = os.environ.get("GITHUB_UPLOAD_TOKEN", "").strip()
    if not github_token:
        print("\n  GITHUB_UPLOAD_TOKEN not found in .env")
        print("  Create a fine-grained token at:")
        print("    github.com → Settings → Developer settings →")
        print("    Personal access tokens → Fine-grained tokens")
        print("  Required: Repository 'morning-briefing' → Secrets → Read & write\n")
        github_token = input("  Paste GitHub token: ").strip()
        if not github_token:
            print("\n  ❌  No token provided\n"); sys.exit(1)
        # Save for next time
        env = Path(".env")
        text = env.read_text(encoding="utf-8") if env.exists() else ""
        if "GITHUB_UPLOAD_TOKEN" not in text:
            with open(env, "a", encoding="utf-8") as f:
                f.write(f"\nGITHUB_UPLOAD_TOKEN={github_token}\n")
            print("  Saved to .env for future use")

    # ── Ensure PyNaCl installed ───────────────────────────────────────────
    ensure_nacl()

    # ── Connect to GitHub ─────────────────────────────────────────────────
    print(f"\n  Connecting to github.com/{REPO_OWNER}/{REPO_NAME}…")
    try:
        key_id, public_key = get_public_key(github_token)
        print(f"  ✓ Connected")
    except RuntimeError as e:
        print(f"\n  ❌  {e}\n"); sys.exit(1)
    except Exception as e:
        print(f"\n  ❌  GitHub connection failed: {e}\n"); sys.exit(1)

    # ── Upload each secret ────────────────────────────────────────────────
    uploaded = 0
    for file_path, secret_name in SECRETS:
        if not file_path.exists():
            print(f"\n  ⚠️  {file_path.name} not found — run py outlook_email.py setup first")
            continue
        value = file_path.read_text(encoding="utf-8").strip()
        if not value:
            print(f"\n  ⚠️  {file_path.name} is empty — run py outlook_email.py setup first")
            continue
        print(f"\n  Uploading {secret_name} ({len(value)} chars)…")
        try:
            ok = put_secret(github_token, key_id, public_key, secret_name, value)
            if ok:
                print(f"  ✓ {secret_name} updated in GitHub")
                uploaded += 1
            else:
                print(f"  ❌  Upload failed for {secret_name}")
        except Exception as e:
            print(f"  ❌  Error: {e}")

    # ── Token setup date (for the briefing's expiry countdown) ───────────
    # outlook_email.py setup writes .outlook_token_setup; mirror it to a
    # GitHub secret so the CI-generated briefing can count down the ~90 days.
    import datetime
    setup_file = Path(".outlook_token_setup")
    setup_date = (setup_file.read_text(encoding="utf-8").strip()
                  if setup_file.exists() else datetime.date.today().isoformat())
    print(f"\n  Uploading OUTLOOK_TOKEN_SETUP_DATE = {setup_date}…")
    try:
        if put_secret(github_token, key_id, public_key,
                      "OUTLOOK_TOKEN_SETUP_DATE", setup_date):
            print("  ✓ OUTLOOK_TOKEN_SETUP_DATE updated in GitHub")
            uploaded += 1
        else:
            print("  ❌  Upload failed for OUTLOOK_TOKEN_SETUP_DATE")
    except Exception as e:
        print(f"  ❌  Error: {e}")

    # ── Done ──────────────────────────────────────────────────────────────
    print()
    if uploaded:
        print(f"  ✅  {uploaded} secret(s) updated successfully!")
        print(f"\n  Trigger a test run at:")
        print(f"  https://github.com/{REPO_OWNER}/{REPO_NAME}/actions\n")
    else:
        print(f"  ⚠️  Nothing uploaded — check errors above\n")


if __name__ == "__main__":
    main()
