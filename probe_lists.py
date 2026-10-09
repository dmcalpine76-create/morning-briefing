"""Temporary. Prints JSON KEY NAMES and value TYPES only - never values."""
import os, json, anthropic, outlook_email as oe

tok = oe.get_access_token()
emails = oe.fetch_recent_emails(tok)
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# call the model exactly as analyse_emails does, but keep the raw text
import datetime
today = datetime.date.today().strftime("%A, %d %B %Y")
emails_text = "\n".join(oe._fmt(e, i) for i, e in enumerate(emails))
knowledge_block = ""
try:
    knowledge_block = oe._knowledge_block()
except Exception:
    pass
MAX_DIGEST_ITEMS, MAX_ACTIONS, MAX_PEOPLE = oe.MAX_DIGEST_ITEMS, oe.MAX_ACTIONS, oe.MAX_PEOPLE
HOURS_BACK = oe.HOURS_BACK
src = oe.__file__
import re
m = re.search(r'prompt = f"""(.*?)"""', open(src, encoding="utf-8").read(), re.S)
prompt = eval('f"""' + m.group(1) + '"""')
msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=4000,
                             messages=[{"role": "user", "content": prompt}])
raw = msg.content[0].text.strip()
raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
d = json.loads(raw)
acts = d.get("actions", []) or []
print(f"actions: {len(acts)}")
for i, a in enumerate(acts[:3], 1):
    print(f"  action {i} keys: {sorted(a.keys())}")
    for k, v in a.items():
        if k in ("index", "email_index", "source_index", "n"):
            print(f"      {k} = {v!r} (type {type(v).__name__})")
print()
print("prompt mentions 'index'  :", "index (the [n]" in prompt)
print("prompt char length       :", len(prompt))
