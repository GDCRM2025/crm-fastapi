from pathlib import Path

BACKEND = Path("backend/routers/whatsapp_gia.py")
FRONTEND = Path("web/views/tools_whatsapp_v2.html")
PUBLIC_VIEW = Path("web/views/tools_whatsapp.html")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"YA_OK: {label}")
        return text
    if old not in text:
        raise SystemExit(f"ERROR: no se encontro ancla para {label}")
    return text.replace(old, new, 1)


backend = BACKEND.read_text(encoding="utf-8")

backend = replace_once(
    backend,
    "from fastapi import APIRouter, Depends, HTTPException, Query\n",
    "from fastapi import APIRouter, Depends, HTTPException, Query, Response\n",
    "import Response",
)

backend = replace_once(
    backend,
    '''    db.execute(text("""\n        CREATE TABLE IF NOT EXISTS whatsapp_conversation_leads (''',
    '''    db.execute(text("""\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS media_id TEXT;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS mime_type TEXT;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS filename TEXT;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS caption TEXT;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS media_size BIGINT;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS location_name TEXT;\n        ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS location_address TEXT;\n    """))\n    db.execute(text("""\n        CREATE TABLE IF NOT EXISTS whatsapp_conversation_leads (''',
    "columnas multimedia",
)

old_parse = '''                        if message_type == "text":\n                            body = ((message.get("text") or {}).get("body"))\n                        elif message_type == "button":\n                            body = ((message.get("button") or {}).get("text"))\n                        elif message_type == "interactive":\n                            body = json.dumps(message.get("interactive") or {}, ensure_ascii=False)\n                        else:\n                            body = f"[{message_type}]"\n                        sent_at = _ts(message.get("timestamp"))'''

new_parse = '''                        media_id = None\n                        mime_type = None\n                        filename = None\n                        caption = None\n                        latitude = None\n                        longitude = None\n                        location_name = None\n                        location_address = None\n\n                        if message_type == "text":\n                            body = ((message.get("text") or {}).get("body"))\n                        elif message_type == "button":\n                            body = ((message.get("button") or {}).get("text"))\n                        elif message_type == "interactive":\n                            body = json.dumps(message.get("interactive") or {}, ensure_ascii=False)\n                        elif message_type in {"image", "audio", "video", "document", "sticker"}:\n                            media = message.get(message_type) or {}\n                            media_id = str(media.get("id") or "") or None\n                            mime_type = str(media.get("mime_type") or "") or None\n                            filename = str(media.get("filename") or "") or None\n                            caption = str(media.get("caption") or "") or None\n                            labels = {\n                                "image": "[Imagen]",\n                                "audio": "[Audio]",\n                                "video": "[Video]",\n                                "document": f"[Documento] {filename or ''}".strip(),\n                                "sticker": "[Sticker]",\n                            }\n                            body = caption or labels.get(message_type, f"[{message_type}]")\n                        elif message_type == "location":\n                            location = message.get("location") or {}\n                            latitude = location.get("latitude")\n                            longitude = location.get("longitude")\n                            location_name = str(location.get("name") or "") or None\n                            location_address = str(location.get("address") or "") or None\n                            body = location_name or location_address or "[Ubicacion]"\n                        else:\n                            body = f"[{message_type}]"\n                        sent_at = _ts(message.get("timestamp"))'''
backend = replace_once(backend, old_parse, new_parse, "procesamiento multimedia")

old_insert = '''                            INSERT INTO whatsapp_messages(\n                                conversation_id, whatsapp_message_id, direction,\n                                message_type, body, status, sent_at, raw_payload\n                            ) VALUES (\n                                :conversation_id, :message_id, 'inbound',\n                                :message_type, :body, 'received', :sent_at,\n                                CAST(:raw_payload AS JSONB)\n                            )'''
new_insert = '''                            INSERT INTO whatsapp_messages(\n                                conversation_id, whatsapp_message_id, direction,\n                                message_type, body, status, sent_at, raw_payload,\n                                media_id, mime_type, filename, caption,\n                                latitude, longitude, location_name, location_address\n                            ) VALUES (\n                                :conversation_id, :message_id, 'inbound',\n                                :message_type, :body, 'received', :sent_at,\n                                CAST(:raw_payload AS JSONB),\n                                :media_id, :mime_type, :filename, :caption,\n                                :latitude, :longitude, :location_name, :location_address\n                            )'''
backend = replace_once(backend, old_insert, new_insert, "insert multimedia")

old_params = '''                            "body": body,\n                            "sent_at": sent_at,\n                            "raw_payload": json.dumps(message, ensure_ascii=False),'''
new_params = '''                            "body": body,\n                            "sent_at": sent_at,\n                            "raw_payload": json.dumps(message, ensure_ascii=False),\n                            "media_id": media_id,\n                            "mime_type": mime_type,\n                            "filename": filename,\n                            "caption": caption,\n                            "latitude": latitude,\n                            "longitude": longitude,\n                            "location_name": location_name,\n                            "location_address": location_address,'''
backend = replace_once(backend, old_params, new_params, "parametros multimedia")

old_select = '''        SELECT id, whatsapp_message_id, direction, message_type, body, status, sent_at\n        FROM whatsapp_messages'''
new_select = '''        SELECT id, whatsapp_message_id, direction, message_type, body, status, sent_at,\n               media_id, mime_type, filename, caption, media_size,\n               latitude, longitude, location_name, location_address\n        FROM whatsapp_messages'''
backend = replace_once(backend, old_select, new_select, "select multimedia")

media_endpoint = r'''

@router.get("/messages/{message_id}/media")
def get_message_media(
    message_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    row = db.execute(text("""
        SELECT media_id, mime_type, filename
        FROM whatsapp_messages
        WHERE id = :id
    """), {"id": message_id}).mappings().first()
    if not row or not row["media_id"]:
        raise HTTPException(status_code=404, detail="Archivo multimedia no encontrado")

    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")

    try:
        metadata_request = urllib.request.Request(
            f"https://graph.facebook.com/{version}/{row['media_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(metadata_request, timeout=30) as metadata_response:
            metadata = json.loads(metadata_response.read().decode("utf-8"))

        media_url = str(metadata.get("url") or "")
        if not media_url:
            raise HTTPException(status_code=502, detail="Meta no entrego URL del archivo")

        file_request = urllib.request.Request(
            media_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(file_request, timeout=60) as file_response:
            content = file_response.read()
            content_type = (
                str(row.get("mime_type") or "").strip()
                or file_response.headers.get_content_type()
                or "application/octet-stream"
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el archivo: {detail}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo descargar el archivo: {exc}") from exc

    db.execute(text("""
        UPDATE whatsapp_messages
        SET media_size = :media_size,
            mime_type = COALESCE(mime_type, :mime_type)
        WHERE id = :id
    """), {"id": message_id, "media_size": len(content), "mime_type": content_type})
    db.commit()

    safe_name = str(row.get("filename") or f"whatsapp_{message_id}").replace('"', "")
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Cache-Control": "private, max-age=300",
        },
    )
'''

anchor = '\n\n@router.post("/conversations/{conversation_id}/send")'
if media_endpoint.strip() not in backend:
    if anchor not in backend:
        raise SystemExit("ERROR: no se encontro ancla endpoint media")
    backend = backend.replace(anchor, media_endpoint + anchor, 1)
else:
    print("YA_OK: endpoint media")

BACKEND.write_text(backend, encoding="utf-8")
print("ACTUALIZADO:", BACKEND)

frontend = FRONTEND.read_text(encoding="utf-8")
frontend = replace_once(
    frontend,
    ".leadId{font-weight:900;color:var(--blue)}",
    ".leadId{font-weight:900;color:var(--blue)}.chatMedia{display:block;max-width:320px;max-height:320px;border-radius:10px;object-fit:contain;background:#f8fafc}.chatAudio{max-width:320px}.fileCard{display:flex;align-items:center;gap:8px;text-decoration:none;color:#0f172a;font-weight:800;padding:10px;border:1px solid var(--bd);border-radius:10px;background:#f8fafc}.sticker{display:block;max-width:160px;max-height:160px}.mediaError{font-size:12px;color:#b91c1c}.mapLink{display:inline-block;margin-top:6px;font-weight:800;color:var(--blue)}",
    "css multimedia",
)

old_render = '''  function renderMessages(rows){const el=$('#messages');el.innerHTML=rows.map(m=>`<div class="bubble ${m.direction==='outbound'?'out':'in'}"><div>${esc(m.body||'')}</div><div class="msgMeta">${esc(fmt(m.sent_at))}${m.direction==='outbound'?' · '+esc(m.status||''):''}</div></div>`).join('');el.scrollTop=el.scrollHeight}'''
new_render = '''  function mediaPlaceholder(m){const id=`media-${m.id}`;if(m.message_type==='image'||m.message_type==='sticker')return `<div id="${id}" class="hint">Cargando ${m.message_type==='sticker'?'sticker':'imagen'}...</div>`;if(m.message_type==='audio')return `<div id="${id}" class="hint">Cargando audio...</div>`;if(m.message_type==='video')return `<div id="${id}" class="hint">Cargando video...</div>`;if(m.message_type==='document')return `<button class="fileCard" type="button" data-media-open="${m.id}">📄 ${esc(m.filename||'Abrir documento')}</button>`;if(m.message_type==='location'&&m.latitude!=null&&m.longitude!=null){const map=`https://www.google.com/maps?q=${encodeURIComponent(m.latitude+','+m.longitude)}`;return `<div><strong>${esc(m.location_name||'Ubicacion')}</strong><div>${esc(m.location_address||'')}</div><a class="mapLink" href="${map}" target="_blank" rel="noopener">Abrir en Google Maps</a></div>`}return `<div>${esc(m.body||'')}</div>`}
  function renderMessages(rows){const el=$('#messages');el.innerHTML=rows.map(m=>`<div class="bubble ${m.direction==='outbound'?'out':'in'}">${m.media_id||m.message_type==='location'?mediaPlaceholder(m):`<div>${esc(m.body||'')}</div>`}${m.caption?`<div style="margin-top:6px">${esc(m.caption)}</div>`:''}<div class="msgMeta">${esc(fmt(m.sent_at))}${m.direction==='outbound'?' · '+esc(m.status||''):''}</div></div>`).join('');el.querySelectorAll('[data-media-open]').forEach(b=>b.onclick=()=>openMedia(Number(b.dataset.mediaOpen),b.textContent.trim()));rows.filter(m=>m.media_id&&['image','sticker','audio','video'].includes(m.message_type)).forEach(loadInlineMedia);el.scrollTop=el.scrollHeight}
  async function fetchMediaBlob(id){const r=await fetch(API_BASE+'/gia/whatsapp/messages/'+id+'/media',{headers:authHeaders({})});if(!r.ok){let detail='HTTP '+r.status;try{const j=await r.json();detail=j.detail||detail}catch(_){}throw new Error(detail)}return await r.blob()}
  async function loadInlineMedia(m){const host=document.getElementById('media-'+m.id);if(!host)return;try{const blob=await fetchMediaBlob(m.id),url=URL.createObjectURL(blob);if(m.message_type==='image'||m.message_type==='sticker'){host.outerHTML=`<img class="${m.message_type==='sticker'?'sticker':'chatMedia'}" src="${url}" alt="${m.message_type==='sticker'?'Sticker':'Imagen recibida'}">`}else if(m.message_type==='audio'){host.outerHTML=`<audio class="chatAudio" controls preload="metadata" src="${url}"></audio>`}else if(m.message_type==='video'){host.outerHTML=`<video class="chatMedia" controls preload="metadata" src="${url}"></video>`}}catch(err){host.className='mediaError';host.textContent=err.message}}
  async function openMedia(id,name){try{const blob=await fetchMediaBlob(id),url=URL.createObjectURL(blob),w=window.open(url,'_blank','noopener');if(!w)throw new Error('El navegador bloqueo la apertura del archivo');setTimeout(()=>URL.revokeObjectURL(url),60000)}catch(err){alert('No se pudo abrir '+name+': '+err.message)}}'''
frontend = replace_once(frontend, old_render, new_render, "render multimedia")

FRONTEND.write_text(frontend, encoding="utf-8")
PUBLIC_VIEW.write_text(frontend, encoding="utf-8")
print("ACTUALIZADO:", FRONTEND)
print("ACTUALIZADO:", PUBLIC_VIEW)
print("WHATSAPP_MEDIA_UPGRADE_OK")
