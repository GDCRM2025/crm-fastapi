from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
TAG = '<script src="../js/agenda_wizard_v2.js?v=20260730-2"></script>'

text = LEADS.read_text(encoding="utf-8")

# Remove previous copies/versions of the same wizard include.
lines = [line for line in text.splitlines() if "agenda_wizard_v2.js" not in line]
text = "\n".join(lines) + "\n"

if "</body>" not in text:
    raise SystemExit("ERROR: no se encontro </body> en web/views/leads.html")

text = text.replace("</body>", f"  {TAG}\n</body>", 1)
LEADS.write_text(text, encoding="utf-8")

print("AGENDA_WIZARD_V2_INSTALLED")
print(TAG)
