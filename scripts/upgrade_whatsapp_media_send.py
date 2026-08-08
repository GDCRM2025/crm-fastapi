from pathlib import Path

BACKEND = Path("backend/routers/whatsapp_gia.py")
FRONTEND = Path("web/views/tools_whatsapp.html")
SOURCE_VIEW = Path("web/views/tools_whatsapp_v2.html")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"YA_OK: {label}")
        return text
    if old not in text:
        raise SystemExit(f"ERROR: no se encontro ancla para {label}")
    return text.replace(old, new, 1)


backend = BACKEND.read_text(encoding="utf-8")
backend = replace_once(backend, "import json\n", "import base64\nimport json\n", "import base64")
backend = replace_once(backend, "import urllib.request\n", "import urllib.request\nimport uuid\n", "import uuid")

model_anchor = '''class AssignConversationBody(BaseModel):\n    brand_code: str\n'''
model_new = '''class AssignConversationBody(BaseModel):\n    brand_code: str\n\n\nclass SendMediaBody(BaseModel):\n    filename: str = Field(min_length=1, max_length=255)\n    mime_type: str = Field(min_length=3, max_length=120)\n    data_base64: str = Field(min_length=4)\n    caption: str | None = Field(default=None, max_length=1024)\n'''
backend = replace_once(backend, model_anchor, model_new, "modelo envio multimedia")

backfill_anchor = '''        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS location_address TEXT;\n    """))'''
backfill_new = '''        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS location_address TEXT;\n    """))\n    db.execute(text("""\n        UPDATE whatsapp_messages\n        SET media_id = COALESCE(\n                media_id,\n                raw_payload -> message_type ->> 'id'\n            ),\n            mime_type = COALESCE(\n                mime_type,\n                raw_payload -> message_type ->> 'mime_type'\n            ),\n            filename = COALESCE(\n                filename,\n                raw_payload -> message_type ->> 'filename'\n            ),\n            caption = COALESCE(\n                caption,\n                raw_payload -> message_type ->> 'caption'\n            ),\n            latitude = COALESCE(\n                latitude,\n                NULLIF(raw_payload -> 'location' ->> 'latitude', '')::DOUBLE PRECISION\n            ),\n            longitude = COALESCE(\n                longitude,\n                NULLIF(raw_payload -> 'location' ->> 'longitude', '')::DOUBLE PRECISION\n            ),\n            location_name = COALESCE(\n                location_name,\n                raw_payload -> 'location' ->> 'name'\n            ),\n            location_address = COALESCE(\n                location_address,\n                raw_payload -> 'location' ->> 'address'\n            )\n        WHERE raw_payload IS NOT NULL\n          AND message_type IN ('image','audio','video','document','sticker','location')\n    """))'''
backend = replace_once(backend, backfill_anchor, backfill_new, "backfill multimedia historico")

helper_anchor = '''def _meta_send_text(phone_number_id: str, to: str, body: str) -> dict[str, Any]:'''
helper_code = r'''def _multipart_body(fields: dict[str, str], file_field: str, filename: str, mime_type: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----GIA" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), boundary


def _meta_upload_media(phone_number_id: str, filename: str, mime_type: str, content: bytes) -> str:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    body, boundary = _multipart_body(
        {"messaging_product": "whatsapp", "type": mime_type},
        "file",
        filename,
        mime_type,
        content,
    )
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/media",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo la carga: {detail}") from exc
    media_id = str(result.get("id") or "")
    if not media_id:
        raise HTTPException(status_code=502, detail="Meta no devolvio media_id")
    return media_id


def _media_type_for_mime(mime_type: str) -> str:
    mime = mime_type.lower().split(";", 1)[0].strip()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "document"


def _meta_send_uploaded_media(
    phone_number_id: str,
    to: str,
    media_type: str,
    media_id: str,
    filename: str,
    caption: str | None,
) -> dict[str, Any]:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    media_object: dict[str, Any] = {"id": media_id}
    if caption and media_type in {"image", "video", "document"}:
        media_object["caption"] = caption
    if filename and media_type == "document":
        media_object["filename"] = filename
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": media_type,
        media_type: media_object,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el envio: {detail}") from exc


'''
if helper_code.strip() not in backend:
    if helper_anchor not in backend:
        raise SystemExit("ERROR: no se encontro ancla helpers multimedia")
    backend = backend.replace(helper_anchor, helper_code + helper_anchor, 1)
else:
    print("YA_OK: helpers envio multimedia")

endpoint_anchor = '\n\n@router.post("/conversations/{conversation_id}/assign")'
endpoint_code = r'''

@router.post("/conversations/{conversation_id}/send-media")
def send_media(
    conversation_id: int,
    body: SendMediaBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    try:
        content = base64.b64decode(body.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Archivo base64 invalido") from exc
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacio")
    if len(content) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Archivo supera 16 MB para esta etapa de prueba")

    mime_type = body.mime_type.lower().split(";", 1)[0].strip()
    media_type = _media_type_for_mime(mime_type)
    media_id = _meta_upload_media(
        str(conversation["phone_number_id"]),
        body.filename,
        mime_type,
        content,
    )
    result = _meta_send_uploaded_media(
        str(conversation["phone_number_id"]),
        str(conversation["wa_id"]),
        media_type,
        media_id,
        body.filename,
        body.caption,
    )
    messages = result.get("messages") or []
    whatsapp_message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    label = body.caption or {
        "image": "[Imagen enviada]",
        "audio": "[Audio enviado]",
        "video": "[Video enviado]",
        "document": f"[Documento enviado] {body.filename}",
    }.get(media_type, "[Archivo enviado]")
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename, caption, media_size
        ) VALUES (
            :conversation_id, :whatsapp_message_id, 'outbound',
            :message_type, :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :media_id, :mime_type, :filename, :caption, :media_size
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "whatsapp_message_id": whatsapp_message_id,
        "message_type": media_type,
        "body": label,
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "media_id": media_id,
        "mime_type": mime_type,
        "filename": body.filename,
        "caption": body.caption,
        "media_size": len(content),
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {
        "ok": True,
        "id": row["id"],
        "message_id": whatsapp_message_id,
        "media_id": media_id,
        "message_type": media_type,
        "status": "accepted",
    }
'''
if endpoint_code.strip() not in backend:
    if endpoint_anchor not in backend:
        raise SystemExit("ERROR: no se encontro ancla endpoint send-media")
    backend = backend.replace(endpoint_anchor, endpoint_code + endpoint_anchor, 1)
else:
    print("YA_OK: endpoint send-media")

BACKEND.write_text(backend, encoding="utf-8")
print("ACTUALIZADO:", BACKEND)

frontend = FRONTEND.read_text(encoding="utf-8")
frontend = replace_once(
    frontend,
    ".composer{border-top:1px solid var(--bd);padding:10px;display:grid;grid-template-columns:1fr auto;gap:8px}",
    ".composer{border-top:1px solid var(--bd);padding:10px;display:grid;grid-template-columns:auto auto 1fr auto;gap:8px}.iconBtn{min-width:44px;font-size:18px}.iconBtn.recording{background:#fee2e2;color:#b91c1c;border-color:#ef4444}",
    "composer multimedia",
)

old_form = '''<form class="composer" id="sendForm"><textarea id="message" placeholder="Escribe un mensaje"></textarea><button class="btn primary" type="submit">Enviar</button></form>'''
new_form = '''<form class="composer" id="sendForm"><input id="mediaFile" type="file" accept="image/*,audio/*,video/*,.pdf,.doc,.docx,.xls,.xlsx" hidden><button class="btn iconBtn" id="attachBtn" type="button" title="Adjuntar archivo">📎</button><button class="btn iconBtn" id="micBtn" type="button" title="Grabar audio">🎙️</button><textarea id="message" placeholder="Escribe un mensaje o agrega un comentario al archivo"></textarea><button class="btn primary" type="submit">Enviar</button></form>'''
frontend = replace_once(frontend, old_form, new_form, "controles adjunto y microfono")

state_anchor = "let items=[],current=null,currentData=null,status='open',timer=null;"
state_new = "let items=[],current=null,currentData=null,status='open',timer=null,recorder=null,recordChunks=[],recordStream=null;"
frontend = replace_once(frontend, state_anchor, state_new, "estado grabador")

send_anchor = "  $('#sendForm').onsubmit=async e=>{"
media_js = r'''  function blobToBase64(blob){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result||'').split(',',2)[1]||'');r.onerror=()=>reject(r.error||new Error('No se pudo leer el archivo'));r.readAsDataURL(blob)})}
  async function sendMediaBlob(blob,filename,mimeType){if(!current)return;if(blob.size>16*1024*1024)throw new Error('El archivo supera 16 MB');const data_base64=await blobToBase64(blob),caption=$('#message').value.trim()||null;await api('/gia/whatsapp/conversations/'+current.id+'/send-media',{method:'POST',body:JSON.stringify({filename,mime_type:mimeType||'application/octet-stream',data_base64,caption})});$('#message').value='';await openConversation(current.id)}
  $('#attachBtn').onclick=()=>$('#mediaFile').click();
  $('#mediaFile').onchange=async e=>{const file=e.target.files?.[0];if(!file)return;$('#attachBtn').disabled=true;try{await sendMediaBlob(file,file.name,file.type||'application/octet-stream')}catch(err){alert(err.message)}finally{$('#attachBtn').disabled=false;e.target.value=''}};
  $('#micBtn').onclick=async()=>{const btn=$('#micBtn');if(recorder&&recorder.state==='recording'){recorder.stop();return}if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){alert('Este navegador no permite grabar audio');return}try{recordStream=await navigator.mediaDevices.getUserMedia({audio:true});const choices=['audio/mp4','audio/ogg;codecs=opus','audio/webm;codecs=opus','audio/webm'];const mime=choices.find(x=>MediaRecorder.isTypeSupported(x))||'';recorder=new MediaRecorder(recordStream,mime?{mimeType:mime}:undefined);recordChunks=[];recorder.ondataavailable=e=>{if(e.data?.size)recordChunks.push(e.data)};recorder.onstop=async()=>{btn.classList.remove('recording');btn.textContent='🎙️';recordStream?.getTracks().forEach(t=>t.stop());const type=recorder.mimeType||recordChunks[0]?.type||'audio/mp4';const ext=type.includes('mp4')?'m4a':type.includes('ogg')?'ogg':type.includes('mpeg')?'mp3':'webm';const blob=new Blob(recordChunks,{type});btn.disabled=true;try{await sendMediaBlob(blob,'audio_'+Date.now()+'.'+ext,type)}catch(err){alert(err.message)}finally{btn.disabled=false;recorder=null;recordStream=null}};recorder.start();btn.classList.add('recording');btn.textContent='⏹️'}catch(err){recordStream?.getTracks().forEach(t=>t.stop());alert('No se pudo usar el microfono: '+err.message)}};
'''
if media_js.strip() not in frontend:
    if send_anchor not in frontend:
        raise SystemExit("ERROR: no se encontro ancla JS envio")
    frontend = frontend.replace(send_anchor, media_js + send_anchor, 1)
else:
    print("YA_OK: javascript envio multimedia")

FRONTEND.write_text(frontend, encoding="utf-8")
SOURCE_VIEW.write_text(frontend, encoding="utf-8")
print("ACTUALIZADO:", FRONTEND)
print("ACTUALIZADO:", SOURCE_VIEW)
print("WHATSAPP_MEDIA_SEND_UPGRADE_OK")
