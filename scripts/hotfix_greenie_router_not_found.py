from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"

text = BACKEND.read_text(encoding="utf-8")

# El hotfix anterior podía declarar SendMediaV2Body antes de SendMediaBody.
# Python compila el archivo, pero falla al importarlo y FastAPI deja todas las
# rutas /gia/whatsapp fuera del router, lo que se ve en la UI como "Not Found".
class_names = [
    "SendTextV2Body",
    "SendMediaV2Body",
    "SendReactionBody",
]

blocks: dict[str, str] = {}
for name in class_names:
    pattern = re.compile(
        rf"^class {name}\b.*?(?=^class \w+|^[A-Z][A-Z0-9_]*\s*:|^def \w+|^@router\.|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        blocks[name] = match.group(0).rstrip() + "\n\n"
        text = text[:match.start()] + text[match.end():]

required = {
    "SendTextV2Body": '''class SendTextV2Body(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    reply_to_message_id: int | None = None

''',
    "SendMediaV2Body": '''class SendMediaV2Body(SendMediaBody):
    reply_to_message_id: int | None = None

''',
    "SendReactionBody": '''class SendReactionBody(BaseModel):
    target_message_id: int
    emoji: str = Field(default="", max_length=16)

''',
}
for name, default in required.items():
    blocks.setdefault(name, default)

media_pattern = re.compile(
    r"^class SendMediaBody\(BaseModel\):.*?(?=^class \w+|^[A-Z][A-Z0-9_]*\s*:|^def \w+|^@router\.|\Z)",
    re.MULTILINE | re.DOTALL,
)
media_match = media_pattern.search(text)
if not media_match:
    raise SystemExit("ERROR: no se encontro class SendMediaBody; no se aplico ningun cambio")

insert = (
    "\n\n"
    + blocks["SendTextV2Body"].strip()
    + "\n\n"
    + blocks["SendMediaV2Body"].strip()
    + "\n\n"
    + blocks["SendReactionBody"].strip()
    + "\n\n"
)
text = text[:media_match.end()] + insert + text[media_match.end():]

# Evita tags antiguos en el nombre del router.
text = text.replace('tags=["WhatsApp GIA"]', 'tags=["WhatsApp Greenie"]')

BACKEND.write_text(text, encoding="utf-8")
print("GREENIE_ROUTER_ORDER_FIXED")
print("Archivo:", BACKEND)
