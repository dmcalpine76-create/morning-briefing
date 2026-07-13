"""
wrap_password.py  —  Encrypt briefing HTML behind a password  (B5)
------------------------------------------------------------------
Called by GitHub Actions to protect the briefing before publishing
to GitHub Pages.

Usage (unchanged from the previous version — no workflow edits needed):
    python wrap_password.py <input.html> <output.html> <password>

WHAT CHANGED vs the old version
    Previously the page embedded the full briefing as base64 with a
    SHA-256 password *gate* — anyone reading the page source could decode
    the content without the password. Now the briefing is genuinely
    encrypted with AES-256-GCM; the key is derived from the password via
    PBKDF2-SHA256 (310,000 iterations). Without the password the payload
    is unreadable.

RENDERING
    After decrypting, the page is rendered by parsing the HTML and
    replacing the document element directly, then re-executing the
    briefing's inline scripts. It deliberately does NOT use an iframe or
    document.write — both corrupt the briefing's formatting on GitHub
    Pages (learned the hard way).

REMEMBER-ME
    On successful unlock the derived AES key (never the password) is
    stored in localStorage, so each device only prompts once. "Log out"
    by clearing site data.

REQUIREMENTS
    pip install cryptography     (add `cryptography` to requirements.txt)
"""

import sys
import os
import base64
from pathlib import Path

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ITERATIONS = 310_000


def encrypt_html(briefing_html: str, password: str) -> tuple[str, str, str]:
    """Returns (salt_b64, iv_b64, ciphertext_b64)."""
    salt = os.urandom(16)
    iv   = os.urandom(12)
    kdf  = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    key        = kdf.derive(password.encode("utf-8"))
    ciphertext = AESGCM(key).encrypt(iv, briefing_html.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return b64(salt), b64(iv), b64(ciphertext)


def wrap(input_path: str, output_path: str, password: str):
    briefing_html = Path(input_path).read_text(encoding="utf-8")
    salt_b64, iv_b64, ct_b64 = encrypt_html(briefing_html, password)

    wrapper = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Doug's Morning Briefing</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: #1a1a1a;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; padding: 1rem; margin: 0;
        }}
        .lock-box {{
            background: #242424;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 2.5rem 2rem;
            width: 100%;
            max-width: 360px;
            text-align: center;
        }}
        .lock-icon {{ font-size: 2.5rem; margin-bottom: 1rem; }}
        .lock-title {{ color: #eee; font-size: 1.1rem; font-weight: 600; margin-bottom: 0.4rem; }}
        .lock-sub {{ color: #888; font-size: 0.8rem; margin-bottom: 1.5rem; }}
        input[type=password] {{
            width: 100%; padding: 0.7rem 0.9rem;
            background: #1a1a1a; border: 1px solid #444; border-radius: 6px;
            color: #eee; font-size: 1rem; margin-bottom: 0.9rem; outline: none;
        }}
        input[type=password]:focus {{ border-color: #c0392b; }}
        button {{
            width: 100%; padding: 0.7rem;
            background: #c0392b; color: #fff; border: none; border-radius: 6px;
            font-size: 0.95rem; font-weight: 600; cursor: pointer;
        }}
        button:disabled {{ opacity: 0.6; cursor: wait; }}
        .lock-error {{ color: #e74c3c; font-size: 0.8rem; margin-top: 0.8rem; display: none; }}
    </style>
</head>
<body>
    <div class="lock-box" id="lock-box">
        <div class="lock-icon">🔒</div>
        <div class="lock-title">Doug's Morning Briefing</div>
        <div class="lock-sub">Enter password to decrypt</div>
        <input type="password" id="pw" placeholder="Password" autofocus
               autocomplete="current-password">
        <button id="unlock-btn" onclick="unlock()">Unlock</button>
        <div class="lock-error" id="err">Incorrect password — try again.</div>
    </div>

<script>
const SALT_B64 = "{salt_b64}";
const IV_B64   = "{iv_b64}";
const CT_B64   = "{ct_b64}";
const ITERS    = {PBKDF2_ITERATIONS};
const LS_KEY   = "briefing_aes_key_v2";

function b64ToBuf(b64) {{
    const bin = atob(b64);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    return buf;
}}

async function deriveKey(password) {{
    const enc = new TextEncoder();
    const baseKey = await crypto.subtle.importKey(
        "raw", enc.encode(password), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
        {{ name: "PBKDF2", salt: b64ToBuf(SALT_B64), iterations: ITERS, hash: "SHA-256" }},
        baseKey,
        {{ name: "AES-GCM", length: 256 }},
        true,                       // extractable, so it can be remembered
        ["decrypt"]);
}}

async function decryptWith(key) {{
    const plainBuf = await crypto.subtle.decrypt(
        {{ name: "AES-GCM", iv: b64ToBuf(IV_B64) }}, key, b64ToBuf(CT_B64));
    return new TextDecoder().decode(plainBuf);
}}

function render(html) {{
    // Replace the document element directly, then re-execute the briefing's
    // inline scripts. NOT an iframe, NOT document.write — both corrupt the
    // briefing's formatting on GitHub Pages.
    const parsed = new DOMParser().parseFromString(html, "text/html");
    document.replaceChild(
        document.adoptNode(parsed.documentElement),
        document.documentElement);
    // Scripts inserted via DOM replacement don't auto-run — re-create them
    // in order so tab switching, To Do push etc. all work.
    const scripts = Array.from(document.querySelectorAll("script"));
    for (const old of scripts) {{
        const s = document.createElement("script");
        for (const attr of old.attributes) s.setAttribute(attr.name, attr.value);
        s.textContent = old.textContent;
        old.parentNode.replaceChild(s, old);
    }}
}}

async function unlock() {{
    const btn = document.getElementById("unlock-btn");
    const err = document.getElementById("err");
    const pw  = document.getElementById("pw").value;
    if (!pw) return;
    btn.disabled = true; btn.textContent = "Decrypting…"; err.style.display = "none";
    try {{
        const key  = await deriveKey(pw);
        const html = await decryptWith(key);
        try {{
            const raw = await crypto.subtle.exportKey("raw", key);
            localStorage.setItem(LS_KEY,
                btoa(String.fromCharCode(...new Uint8Array(raw))));
        }} catch (e) {{ /* remember-me unavailable — still unlock */ }}
        render(html);
    }} catch (e) {{
        btn.disabled = false; btn.textContent = "Unlock";
        err.style.display = "block";
        document.getElementById("pw").value = "";
        document.getElementById("pw").focus();
    }}
}}

document.getElementById("pw").addEventListener("keydown",
    e => {{ if (e.key === "Enter") unlock(); }});

// Auto-unlock if this device has decrypted before (stored key, not password)
(async function () {{
    if (!window.crypto || !crypto.subtle) {{
        document.querySelector(".lock-sub").textContent =
            "This browser doesn't support WebCrypto — open over HTTPS.";
        return;
    }}
    const stored = localStorage.getItem(LS_KEY);
    if (!stored) return;
    try {{
        const key = await crypto.subtle.importKey(
            "raw", b64ToBuf(stored), {{ name: "AES-GCM" }}, false, ["decrypt"]);
        const html = await decryptWith(key);
        render(html);
    }} catch (e) {{
        localStorage.removeItem(LS_KEY);   // stale key (password changed)
    }}
}})();
</script>
</body>
</html>"""

    Path(output_path).write_text(wrapper, encoding="utf-8")
    kb = len(wrapper) // 1024
    print(f"✓ Encrypted briefing written to {output_path} ({kb} KB, AES-256-GCM)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python wrap_password.py <input.html> <output.html> <password>")
        sys.exit(1)
    wrap(sys.argv[1], sys.argv[2], sys.argv[3])
