"""
wrap_password.py  —  Wrap briefing HTML with password protection
----------------------------------------------------------------
Called by GitHub Actions to add a password gate to the briefing
before publishing to GitHub Pages.

Usage:
    python wrap_password.py <input.html> <output.html> <password>

The password is hashed client-side using SHA-256.
The correct hash is embedded in the page.
When the user enters the correct password, the briefing is shown.
The password itself never appears in the HTML source.

On mobile: password is remembered in localStorage so you only
need to enter it once per device.
"""

import sys
import hashlib
from pathlib import Path


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def wrap(input_path: str, output_path: str, password: str):
    briefing_html = Path(input_path).read_text(encoding="utf-8")
    correct_hash  = sha256(password)

    # Escape the briefing HTML for embedding in a JS string
    # We base64-encode it so no escaping issues
    import base64
    encoded = base64.b64encode(briefing_html.encode("utf-8")).decode("ascii")

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
            min-height: 100vh; padding: 1rem;
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
        h1 {{
            font-size: 1.2rem;
            font-weight: 700;
            color: #fff;
            margin-bottom: 0.4rem;
        }}
        .subtitle {{
            font-size: 0.8rem;
            color: rgba(255,255,255,0.4);
            margin-bottom: 1.5rem;
        }}
        input[type="password"] {{
            width: 100%;
            padding: 0.75rem 1rem;
            background: #1a1a1a;
            border: 1px solid #444;
            border-radius: 6px;
            color: #fff;
            font-size: 1rem;
            margin-bottom: 0.75rem;
            outline: none;
            transition: border-color 0.15s;
        }}
        input[type="password"]:focus {{ border-color: #c0392b; }}
        button {{
            width: 100%;
            padding: 0.75rem;
            background: #c0392b;
            color: #fff;
            border: none;
            border-radius: 6px;
            font-size: 0.9rem;
            font-weight: 700;
            cursor: pointer;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            transition: background 0.15s;
        }}
        button:hover {{ background: #a93226; }}
        .error {{
            color: #e74c3c;
            font-size: 0.8rem;
            margin-top: 0.75rem;
            display: none;
        }}
        #briefing-container {{ display: none; }}
    </style>
</head>
<body>

<div id="lock-screen" class="lock-box">
    <div class="lock-icon">🔐</div>
    <h1>Morning Briefing</h1>
    <p class="subtitle">Enter your password to continue</p>
    <input type="password" id="pwd" placeholder="Password"
           autofocus autocomplete="current-password"
           onkeydown="if(event.key==='Enter') unlock()">
    <button onclick="unlock()">Open Briefing</button>
    <p class="error" id="err">Incorrect password</p>
</div>

<!-- briefing loaded into iframe dynamically -->

<script>
const CORRECT_HASH = "{correct_hash}";
const STORAGE_KEY  = "briefing_auth";
const ENCODED      = "{encoded}";

async function sha256(str) {{
    const buf = await crypto.subtle.digest('SHA-256',
        new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf))
        .map(b => b.toString(16).padStart(2,'0')).join('');
}}

function showBriefing() {{
    document.getElementById('lock-screen').style.display = 'none';
    document.body.style.margin = '0';
    document.body.style.padding = '0';
    document.body.style.overflow = 'hidden';
    const bytes = Uint8Array.from(atob(ENCODED), c => c.charCodeAt(0));
    const blob  = new Blob([bytes], {{type: 'text/html; charset=utf-8'}});
    const url   = URL.createObjectURL(blob);
    const frame = document.createElement('iframe');
    frame.src   = url;
    frame.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;border:none;';
    document.body.appendChild(frame);
}}

async function unlock() {{
    const pwd = document.getElementById('pwd').value;
    if (!pwd) return;
    const hash = await sha256(pwd);
    if (hash === CORRECT_HASH) {{
        localStorage.setItem(STORAGE_KEY, hash);
        showBriefing();
    }} else {{
        document.getElementById('err').style.display = 'block';
        document.getElementById('pwd').value = '';
        document.getElementById('pwd').focus();
    }}
}}

// Auto-unlock if password was saved on this device
(async () => {{
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && saved === CORRECT_HASH) {{
        showBriefing();
    }}
}})();
</script>
</body>
</html>"""

    Path(output_path).write_text(wrapper, encoding="utf-8")
    print(f"✅  Password-protected page written to {output_path}")
    print(f"    Password hash: {correct_hash[:16]}...")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python wrap_password.py <input.html> <output.html> <password>")
        sys.exit(1)
    wrap(sys.argv[1], sys.argv[2], sys.argv[3])
