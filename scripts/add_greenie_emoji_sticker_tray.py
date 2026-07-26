from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"
VIEWS = [
    ROOT / "web/views/tools_whatsapp_greenie.html",
    ROOT / "web/views/tools_whatsapp.html",
    ROOT / "web/views/tools_whatsapp_v2.html",
]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print("YA_OK:", label)
        return text
    if old not in text:
        raise SystemExit(f"ERROR: no se encontro ancla para {label}")
    print("ACTUALIZADO:", label)
    return text.replace(old, new, 1)


# =============================================================================
# BACKEND
# =============================================================================
backend = BACKEND.read_text(encoding="utf-8")

# Modelo para reenviar un sticker ya recibido/usado.
if "class SendStickerBody(BaseModel):" not in backend:
    media_model = re.search(
        r"class SendMediaBody\(BaseModel\):\n(?:    .*\n)+?(?=\nclass |\n\nclass )",
        backend,
    )
    if not media_model:
        raise SystemExit("ERROR: no se encontro class SendMediaBody")
    insert_at = media_model.end()
    model = '''\n\nclass SendStickerBody(BaseModel):\n    media_id: str = Field(min_length=1, max_length=255)\n    source_message_id: int | None = None\n'''
    backend = backend[:insert_at] + model + backend[insert_at:]
    print("ACTUALIZADO: modelo SendStickerBody")

# image/webp debe enviarse como sticker, nunca como image.
media_func_old = '''def _media_type_for_mime(mime_type: str) -> str:\n    mime = mime_type.lower().split(";", 1)[0].strip()\n    if mime.startswith("image/"):\n'''
media_func_new = '''def _media_type_for_mime(mime_type: str) -> str:\n    mime = mime_type.lower().split(";", 1)[0].strip()\n    if mime == "image/webp":\n        return "sticker"\n    if mime.startswith("image/"):\n'''
backend = replace_once(
    backend,
    media_func_old,
    media_func_new,
    "image/webp como sticker",
)

# Las grabaciones pasan por FFmpeg. Los WebP no deben entrar a esa conversión.
voice_old = '''    filename, mime_type, content, voice = _normalise_voice_audio(\n        body.filename,\n        body.mime_type,\n        content,\n    )\n    media_type = _media_type_for_mime(mime_type)\n'''
voice_new = '''    requested_mime = body.mime_type.lower().split(";", 1)[0].strip()\n    if requested_mime == "image/webp" or body.filename.lower().endswith(".webp"):\n        if len(content) > 100 * 1024:\n            raise HTTPException(\n                status_code=413,\n                detail="El sticker supera 100 KB. Reduce el archivo WebP.",\n            )\n        filename = body.filename if body.filename.lower().endswith(".webp") else body.filename + ".webp"\n        mime_type = "image/webp"\n        voice = False\n    else:\n        filename, mime_type, content, voice = _normalise_voice_audio(\n            body.filename,\n            body.mime_type,\n            content,\n        )\n    media_type = _media_type_for_mime(mime_type)\n'''
backend = replace_once(
    backend,
    voice_old,
    voice_new,
    "preparacion de stickers WebP",
)

# Etiqueta visual del sticker enviado.
if '"sticker": "[Sticker enviado]",' not in backend:
    label_anchor = '        "audio": "[Audio enviado]",\n'
    if label_anchor not in backend:
        raise SystemExit("ERROR: no se encontro mapa de etiquetas multimedia")
    backend = backend.replace(
        label_anchor,
        label_anchor + '        "sticker": "[Sticker enviado]",\n',
        1,
    )
    print("ACTUALIZADO: etiqueta sticker")

# Helpers para recuperar un media_id vencido y volverlo a subir.
helper_marker = "def _greenie_download_media_bytes("
if helper_marker not in backend:
    helper_anchor = "def _media_type_for_mime(mime_type: str) -> str:\n"
    if helper_anchor not in backend:
        raise SystemExit("ERROR: no se encontro ancla de helpers sticker")
    helper_code = r'''def _greenie_download_media_bytes(media_id: str) -> tuple[bytes, str]:
    token = _runtime_env("WHATSAPP_ACCESS_TOKEN")
    version = _runtime_env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")

    metadata_request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{media_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(metadata_request, timeout=45) as response:
            metadata = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el sticker: {detail}") from exc

    media_url = str(metadata.get("url") or "").strip()
    mime_type = str(metadata.get("mime_type") or "image/webp").split(";", 1)[0].strip()
    if not media_url:
        raise HTTPException(status_code=502, detail="Meta no devolvio la URL del sticker")

    file_request = urllib.request.Request(
        media_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(file_request, timeout=60) as response:
            content = response.read()
            mime_type = str(response.headers.get("Content-Type") or mime_type).split(";", 1)[0].strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"No se pudo descargar el sticker: {detail}") from exc

    if not content:
        raise HTTPException(status_code=502, detail="El sticker descargado esta vacio")
    return content, mime_type or "image/webp"


'''
    backend = backend.replace(helper_anchor, helper_code + helper_anchor, 1)
    print("ACTUALIZADO: helper descarga sticker")

# Endpoints: bandeja de stickers recientes y envío por media_id.
endpoint_marker = '@router.get("/conversations/{conversation_id}/stickers")'
if endpoint_marker not in backend:
    endpoint_anchor = '\n\n@router.post("/conversations/{conversation_id}/assign")'
    if endpoint_anchor not in backend:
        raise SystemExit("ERROR: no se encontro ancla de endpoints sticker")
    endpoint_code = r'''

@router.get("/conversations/{conversation_id}/stickers")
def conversation_stickers(
    conversation_id: int,
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    rows = db.execute(text("""
        SELECT *
        FROM (
            SELECT DISTINCT ON (m.media_id)
                m.id AS source_message_id,
                m.media_id,
                COALESCE(NULLIF(m.mime_type, ''), 'image/webp') AS mime_type,
                m.direction,
                m.sent_at
            FROM whatsapp_messages m
            JOIN whatsapp_conversations c
              ON c.id = m.conversation_id
            WHERE c.phone_number_id = :phone_number_id
              AND m.message_type = 'sticker'
              AND NULLIF(m.media_id, '') IS NOT NULL
            ORDER BY m.media_id, m.sent_at DESC, m.id DESC
        ) recent
        ORDER BY sent_at DESC
        LIMIT 60
    """), {
        "phone_number_id": str(conversation["phone_number_id"]),
    }).mappings().all()
    return {"ok": True, "items": [dict(row) for row in rows]}


@router.post("/conversations/{conversation_id}/send-sticker")
def send_existing_sticker(
    conversation_id: int,
    body: SendStickerBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    media_id = body.media_id.strip()

    try:
        result = _meta_send_uploaded_media(
            str(conversation["phone_number_id"]),
            str(conversation["wa_id"]),
            "sticker",
            media_id,
            "sticker.webp",
            None,
        )
    except HTTPException:
        # Los media_id pueden vencer. Se descarga el sticker y se vuelve a subir.
        if not body.source_message_id:
            raise
        source = db.execute(text("""
            SELECT m.media_id
            FROM whatsapp_messages m
            JOIN whatsapp_conversations c
              ON c.id = m.conversation_id
            WHERE m.id = :message_id
              AND c.phone_number_id = :phone_number_id
              AND m.message_type = 'sticker'
            LIMIT 1
        """), {
            "message_id": body.source_message_id,
            "phone_number_id": str(conversation["phone_number_id"]),
        }).mappings().first()
        if not source:
            raise HTTPException(status_code=404, detail="Sticker no encontrado")
        content, mime_type = _greenie_download_media_bytes(str(source["media_id"]))
        if len(content) > 100 * 1024:
            raise HTTPException(status_code=413, detail="El sticker supera 100 KB")
        media_id = _meta_upload_media(
            str(conversation["phone_number_id"]),
            "sticker.webp",
            "image/webp",
            content,
        )
        result = _meta_send_uploaded_media(
            str(conversation["phone_number_id"]),
            str(conversation["wa_id"]),
            "sticker",
            media_id,
            "sticker.webp",
            None,
        )

    messages = result.get("messages") or []
    whatsapp_message_id = (
        messages[0].get("id")
        if messages and isinstance(messages[0], dict)
        else None
    )
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename
        ) VALUES (
            :conversation_id, :whatsapp_message_id, 'outbound',
            'sticker', '[Sticker enviado]', 'accepted', now(),
            CAST(:raw_payload AS JSONB), :media_id, 'image/webp', 'sticker.webp'
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "whatsapp_message_id": whatsapp_message_id,
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "media_id": media_id,
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
        "status": "accepted",
    }
'''
    backend = backend.replace(endpoint_anchor, endpoint_code + endpoint_anchor, 1)
    print("ACTUALIZADO: endpoints bandeja stickers")

BACKEND.write_text(backend, encoding="utf-8")
print("GUARDADO:", BACKEND.relative_to(ROOT))


# =============================================================================
# FRONTEND
# =============================================================================
tray_css = r'''
    /* GREENIE_EMOJI_STICKER_TRAY_V1 */
    #chatWrap{position:relative}
    .chatTray{position:absolute;left:10px;bottom:72px;width:min(390px,calc(100% - 20px));height:360px;background:#fff;border:1px solid var(--bd);border-radius:16px;box-shadow:0 14px 40px rgba(15,23,42,.22);z-index:40;display:none;overflow:hidden}
    .chatTray.open{display:flex;flex-direction:column}
    .trayHead{display:flex;align-items:center;gap:6px;padding:8px;border-bottom:1px solid var(--bd);background:#f8fafc}
    .trayTab{border:0;background:transparent;border-radius:9px;padding:8px 12px;font-weight:800;cursor:pointer;color:var(--muted)}
    .trayTab.active{background:#e2e8f0;color:#0f172a}
    .trayClose{margin-left:auto;border:0;background:transparent;font-size:18px;cursor:pointer;padding:6px}
    .trayPane{display:none;min-height:0;flex:1;overflow:hidden}.trayPane.active{display:flex;flex-direction:column}
    .emojiCats{display:flex;gap:4px;overflow-x:auto;padding:7px;border-bottom:1px solid #edf2f7}
    .emojiCat{border:0;background:#f1f5f9;border-radius:8px;padding:6px 8px;cursor:pointer;font-size:17px}.emojiCat.active{background:#dbeafe}
    .emojiGrid{display:grid;grid-template-columns:repeat(8,1fr);gap:2px;padding:8px;overflow:auto;align-content:start}
    .emojiCell{border:0;background:transparent;border-radius:8px;min-height:38px;font-size:23px;cursor:pointer}.emojiCell:hover{background:#eef2f7;transform:scale(1.08)}
    .stickerTools{display:flex;align-items:center;gap:8px;padding:8px;border-bottom:1px solid #edf2f7}
    .stickerHint{font-size:11px;color:var(--muted);line-height:1.25}
    .stickerGrid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:10px;overflow:auto;align-content:start}
    .stickerCell{border:1px solid transparent;background:#f8fafc;border-radius:12px;aspect-ratio:1;display:grid;place-items:center;cursor:pointer;padding:5px}.stickerCell:hover{border-color:#93c5fd;background:#eff6ff}
    .stickerCell img{max-width:100%;max-height:100%;object-fit:contain}
    .stickerEmpty{grid-column:1/-1;color:var(--muted);text-align:center;padding:30px 12px;font-size:12px}
    .trayLoading{opacity:.55;pointer-events:none}
'''

tray_html = r'''<div class="chatTray" id="chatTray">
        <div class="trayHead">
          <button class="trayTab active" type="button" data-tray-tab="emoji">😀 Emojis</button>
          <button class="trayTab" type="button" data-tray-tab="sticker">▣ Stickers</button>
          <button class="trayClose" id="trayClose" type="button" title="Cerrar">×</button>
        </div>
        <section class="trayPane active" id="emojiPane">
          <div class="emojiCats" id="emojiCats"></div>
          <div class="emojiGrid" id="emojiGrid"></div>
        </section>
        <section class="trayPane" id="stickerPane">
          <div class="stickerTools">
            <button class="btn" id="uploadStickerBtn" type="button">Subir WebP</button>
            <input id="stickerUpload" type="file" accept="image/webp,.webp" hidden>
            <div class="stickerHint">Recientes recibidos o usados. Máximo 100 KB.</div>
          </div>
          <div class="stickerGrid" id="stickerGrid"><div class="stickerEmpty">Abre una conversación para ver stickers.</div></div>
        </section>
      </div>'''

tray_js = r'''
  /* GREENIE_EMOJI_STICKER_TRAY_V1 */
  const __greenieEmojiGroups={
    'Recientes':'😀 😂 🥰 😍 😎 😭 😡 👍 👎 👌 🙏 👏 🎉 ❤️ 💚 💎 ✅ ❌ 🔥',
    'Caras':'😀 😃 😄 😁 😆 😅 😂 🤣 😊 😇 🙂 🙃 😉 😌 😍 🥰 😘 😗 😙 😚 😋 😛 😝 😜 🤪 🤨 🧐 🤓 😎 🥳 😏 😒 😞 😔 😟 😕 🙁 ☹️ 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥 😓 🤗 🤔 🤭 🤫 🤥 😶 😐 😑 😬 🙄 😯 😦 😧 😮 😲 🥱 😴 🤤 😪 😵 🤐 🤢 🤮 🤧 😷 🤒 🤕',
    'Gestos':'👋 🤚 🖐️ ✋ 🖖 👌 🤌 🤏 ✌️ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ 👍 👎 ✊ 👊 🤛 🤜 👏 🙌 👐 🤲 🤝 🙏 ✍️ 💅 🤳 💪 🦾 🖕',
    'Corazones':'❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💔 ❣️ 💕 💞 💓 💗 💖 💘 💝 💟 ♥️ 🫶',
    'Personas':'👶 🧒 👦 👧 🧑 👱 👨 🧔 👩 🧓 👴 👵 🙍 🙎 🙅 🙆 💁 🙋 🧏 🙇 🤦 🤷 👮 👷 💂 🕵️ 👩‍⚕️ 👨‍🍳 👩‍🏫 👨‍💻 👩‍💼',
    'Comida':'🍏 🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍈 🍒 🍑 🥭 🍍 🥥 🥝 🍅 🥑 🍆 🥔 🥕 🌽 🌶️ 🥒 🥬 🥦 🧄 🧅 🍄 🥜 🌰 🍞 🥐 🥖 🥨 🧀 🥚 🍳 🥞 🧇 🥓 🍔 🍟 🍕 🌭 🥪 🌮 🌯 🥗 🍝 🍣 🍤 🍦 🍰 🎂 ☕ 🍺 🍷',
    'Objetos':'🎉 🎊 🎈 🎁 🏆 🥇 ⚽ 🏀 🎵 🎶 📞 📱 💻 ⌚ 📷 💡 🔑 🔒 🔔 📌 📍 ✏️ 📝 📅 📦 💰 💳 🚗 ✈️ 🏠 🏢 🛒 🧾 💎',
    'Símbolos':'✅ ☑️ ✔️ ❌ ✖️ ⚠️ ❗ ❓ ‼️ 💯 🔥 ⭐ 🌟 ✨ 💫 ♻️ ➕ ➖ ➡️ ⬅️ ⬆️ ⬇️ 🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪'
  };
  let __greenieEmojiCategory='Recientes';
  let __greenieStickerUrls=[];

  function __greenieOpenTray(tab){
    const tray=$('#chatTray');
    tray.classList.add('open');
    document.querySelectorAll('[data-tray-tab]').forEach(button=>button.classList.toggle('active',button.dataset.trayTab===tab));
    $('#emojiPane').classList.toggle('active',tab==='emoji');
    $('#stickerPane').classList.toggle('active',tab==='sticker');
    if(tab==='sticker')__greenieLoadStickerTray();
  }

  function __greenieCloseTray(){
    $('#chatTray').classList.remove('open');
  }

  function __greenieInsertEmoji(emoji){
    const input=$('#message');
    const start=input.selectionStart??input.value.length;
    const end=input.selectionEnd??start;
    input.value=input.value.slice(0,start)+emoji+input.value.slice(end);
    const next=start+emoji.length;
    input.focus();
    input.setSelectionRange(next,next);

    const recent=(localStorage.getItem('greenieEmojiRecent')||'').split(' ').filter(Boolean);
    const updated=[emoji,...recent.filter(value=>value!==emoji)].slice(0,24);
    localStorage.setItem('greenieEmojiRecent',updated.join(' '));
    __greenieEmojiGroups.Recientes=updated.join(' ');
    if(__greenieEmojiCategory==='Recientes')__greenieRenderEmojis();
  }

  function __greenieRenderEmojiCategories(){
    const icons={'Recientes':'🕘','Caras':'😀','Gestos':'👋','Corazones':'❤️','Personas':'🧑','Comida':'🍔','Objetos':'🎁','Símbolos':'✅'};
    $('#emojiCats').innerHTML=Object.keys(__greenieEmojiGroups).map(name=>`<button type="button" class="emojiCat ${name===__greenieEmojiCategory?'active':''}" data-emoji-cat="${name}" title="${name}">${icons[name]||'•'}</button>`).join('');
    document.querySelectorAll('[data-emoji-cat]').forEach(button=>button.onclick=()=>{
      __greenieEmojiCategory=button.dataset.emojiCat;
      __greenieRenderEmojiCategories();
      __greenieRenderEmojis();
    });
  }

  function __greenieRenderEmojis(){
    const values=(__greenieEmojiGroups[__greenieEmojiCategory]||'').split(' ').filter(Boolean);
    $('#emojiGrid').innerHTML=values.map(emoji=>`<button type="button" class="emojiCell">${emoji}</button>`).join('');
    document.querySelectorAll('#emojiGrid .emojiCell').forEach((button,index)=>button.onclick=()=>__greenieInsertEmoji(values[index]));
  }

  function __greenieClearStickerUrls(){
    __greenieStickerUrls.forEach(url=>URL.revokeObjectURL(url));
    __greenieStickerUrls=[];
  }

  async function __greenieLoadStickerImage(item,img){
    try{
      const response=await fetch(API_BASE+'/gia/whatsapp/messages/'+item.source_message_id+'/media',{headers:authHeaders({})});
      if(!response.ok)throw new Error('HTTP '+response.status);
      const blob=await response.blob();
      const url=URL.createObjectURL(blob);
      __greenieStickerUrls.push(url);
      img.src=url;
    }catch(error){
      img.alt='Sticker no disponible';
      console.warn('[GREENIE] sticker tray',error);
    }
  }

  async function __greenieLoadStickerTray(){
    const grid=$('#stickerGrid');
    if(!current){grid.innerHTML='<div class="stickerEmpty">Selecciona una conversación.</div>';return}
    grid.classList.add('trayLoading');
    grid.innerHTML='<div class="stickerEmpty">Cargando stickers…</div>';
    __greenieClearStickerUrls();
    try{
      const data=await api('/gia/whatsapp/conversations/'+current.id+'/stickers');
      const stickers=data.items||[];
      if(!stickers.length){
        grid.innerHTML='<div class="stickerEmpty">Todavía no hay stickers recientes.<br>Recibe uno por WhatsApp o sube un archivo WebP.</div>';
        return;
      }
      grid.innerHTML=stickers.map((item,index)=>`<button type="button" class="stickerCell" data-sticker-index="${index}" title="Enviar sticker"><img alt="Sticker"></button>`).join('');
      stickers.forEach((item,index)=>{
        const button=grid.querySelector(`[data-sticker-index="${index}"]`);
        const img=button.querySelector('img');
        __greenieLoadStickerImage(item,img);
        button.onclick=async()=>{
          button.disabled=true;
          try{
            await api('/gia/whatsapp/conversations/'+current.id+'/send-sticker',{method:'POST',body:JSON.stringify({media_id:item.media_id,source_message_id:item.source_message_id})});
            __greenieCloseTray();
            await openConversation(current.id);
          }catch(error){alert(error.message)}finally{button.disabled=false}
        };
      });
    }catch(error){
      grid.innerHTML='<div class="stickerEmpty" style="color:#b91c1c">'+esc(error.message)+'</div>';
    }finally{
      grid.classList.remove('trayLoading');
    }
  }

  const __greenieRecent=localStorage.getItem('greenieEmojiRecent');
  if(__greenieRecent)__greenieEmojiGroups.Recientes=__greenieRecent;
  __greenieRenderEmojiCategories();
  __greenieRenderEmojis();

  $('#emojiBtn').onclick=event=>{event.stopPropagation();__greenieOpenTray('emoji')};
  $('#stickerBtn').onclick=event=>{event.stopPropagation();__greenieOpenTray('sticker')};
  $('#trayClose').onclick=__greenieCloseTray;
  document.querySelectorAll('[data-tray-tab]').forEach(button=>button.onclick=()=>__greenieOpenTray(button.dataset.trayTab));
  $('#uploadStickerBtn').onclick=()=>$('#stickerUpload').click();
  $('#stickerUpload').onchange=async event=>{
    const file=event.target.files?.[0];
    if(!file)return;
    try{
      if(file.type!=='image/webp'&&!file.name.toLowerCase().endsWith('.webp'))throw new Error('El sticker debe ser un archivo WebP');
      if(file.size>100*1024)throw new Error('El sticker supera 100 KB');
      await sendMediaBlob(file,file.name,'image/webp');
      __greenieCloseTray();
      await __greenieLoadStickerTray();
    }catch(error){alert(error.message)}finally{event.target.value=''}
  };
  document.addEventListener('keydown',event=>{if(event.key==='Escape')__greenieCloseTray()});
  document.addEventListener('click',event=>{
    const tray=$('#chatTray');
    if(tray.classList.contains('open')&&!tray.contains(event.target)&&!event.target.closest('#emojiBtn,#stickerBtn'))__greenieCloseTray();
  });
'''

for path in VIEWS:
    if not path.exists():
        print("OMITIDO:", path.relative_to(ROOT), "no existe")
        continue
    view = path.read_text(encoding="utf-8")
    if "mediaFile" not in view or "sendMediaBlob" not in view:
        print("OMITIDO:", path.relative_to(ROOT), "no es vista multimedia")
        continue

    if "GREENIE_EMOJI_STICKER_TRAY_V1" not in view:
        if "</style>" not in view:
            raise SystemExit(f"ERROR: {path} no tiene </style>")
        view = view.replace("</style>", tray_css + "\n  </style>", 1)

        # Amplía la grilla del compositor para 📎 🎙️ 😀 ▣ texto Enviar.
        view = view.replace(
            "grid-template-columns:auto auto 1fr auto",
            "grid-template-columns:auto auto auto auto 1fr auto",
        )

        mic_pattern = re.compile(
            r'(<button[^>]+id="micBtn"[^>]*>.*?</button>)',
            re.DOTALL,
        )
        match = mic_pattern.search(view)
        if not match:
            raise SystemExit(f"ERROR: {path} no tiene micBtn")
        buttons = match.group(1) + '<button class="btn iconBtn" id="emojiBtn" type="button" title="Emojis">😀</button><button class="btn iconBtn" id="stickerBtn" type="button" title="Stickers">▣</button>'
        view = view[:match.start()] + buttons + view[match.end():]

        form_close = view.find("</form>", match.end())
        if form_close < 0:
            raise SystemExit(f"ERROR: {path} no tiene cierre del compositor")
        form_close += len("</form>")
        view = view[:form_close] + tray_html + view[form_close:]

        iife_end = view.rfind("\n})();")
        if iife_end < 0:
            raise SystemExit(f"ERROR: {path} no tiene cierre IIFE")
        view = view[:iife_end] + "\n" + tray_js + view[iife_end:]
        print("ACTUALIZADO:", path.relative_to(ROOT))
    else:
        print("YA_OK:", path.relative_to(ROOT))

    path.write_text(view, encoding="utf-8")

print("GREENIE_EMOJI_STICKER_TRAY_OK")
