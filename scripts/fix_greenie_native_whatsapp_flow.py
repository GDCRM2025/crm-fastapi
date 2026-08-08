from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"
VIEWS = [
    ROOT / "web/views/tools_whatsapp_greenie.html",
    ROOT / "web/views/tools_whatsapp.html",
    ROOT / "web/views/tools_whatsapp_v2.html",
]


def insert_before(text: str, anchor: str, block: str, label: str) -> str:
    if block.strip() in text:
        print("YA_OK:", label)
        return text
    if anchor not in text:
        raise SystemExit(f"ERROR: no se encontro ancla para {label}")
    print("ACTUALIZADO:", label)
    return text.replace(anchor, block + anchor, 1)


# =============================================================================
# BACKEND
# =============================================================================
backend = BACKEND.read_text(encoding="utf-8")

models = '''class SendTextV2Body(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    reply_to_message_id: int | None = None


class SendMediaV2Body(SendMediaBody):
    reply_to_message_id: int | None = None


class SendReactionBody(BaseModel):
    target_message_id: int
    emoji: str = Field(default="", max_length=16)


'''
backend = insert_before(
    backend,
    "class AssignConversationBody(BaseModel):",
    models,
    "modelos respuesta y reaccion",
)

schema = '''    db.execute(text("""
        ALTER TABLE whatsapp_messages
        ADD COLUMN IF NOT EXISTS reply_to_whatsapp_message_id TEXT
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS whatsapp_message_reactions (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
            target_whatsapp_message_id TEXT NOT NULL,
            reactor_key TEXT NOT NULL,
            direction TEXT NOT NULL,
            emoji TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(target_whatsapp_message_id, reactor_key)
        )
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_reactions_target
        ON whatsapp_message_reactions(target_whatsapp_message_id)
    """))

'''
backend = insert_before(
    backend,
    "    # Mapeo definitivo de ejecutivos.",
    schema,
    "tablas de respuesta y reaccion",
)

helpers = r'''def _greenie_env(name: str, default: str = "") -> str:
    runtime = globals().get("_runtime_env")
    if callable(runtime):
        return str(runtime(name, default) or default).strip()
    return str(os.getenv(name, default) or default).strip()


def _greenie_meta_post_message(phone_number_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = _greenie_env("WHATSAPP_ACCESS_TOKEN")
    version = _greenie_env("WHATSAPP_GRAPH_API_VERSION", "v25.0")
    if not token:
        raise HTTPException(status_code=503, detail="WHATSAPP_ACCESS_TOKEN no configurado")
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_number_id}/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"Meta rechazo el mensaje: {detail}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar con Meta: {exc}") from exc


def _greenie_target_message(db: Session, conversation_id: int, local_message_id: int | None):
    if not local_message_id:
        return None
    row = db.execute(text("""
        SELECT id, whatsapp_message_id, body, message_type, direction, sent_at
        FROM whatsapp_messages
        WHERE id = :message_id
          AND conversation_id = :conversation_id
        LIMIT 1
    """), {
        "message_id": local_message_id,
        "conversation_id": conversation_id,
    }).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="El mensaje seleccionado ya no existe")
    if not str(row.get("whatsapp_message_id") or "").strip():
        raise HTTPException(status_code=400, detail="Este mensaje no tiene un identificador valido de WhatsApp")
    return row


def _normalize_reaction_messages(db: Session, conversation_id: int | None = None) -> None:
    params: dict[str, Any] = {}
    condition = ""
    if conversation_id is not None:
        condition = "AND m.conversation_id = :conversation_id"
        params["conversation_id"] = conversation_id

    rows = db.execute(text(f"""
        SELECT m.id, m.conversation_id, m.direction, m.raw_payload
        FROM whatsapp_messages m
        WHERE m.message_type = 'reaction'
          {condition}
        ORDER BY m.id
        LIMIT 500
    """), params).mappings().all()

    for row in rows:
        payload = row.get("raw_payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        reaction = payload.get("reaction") or {}
        target_id = str(reaction.get("message_id") or "").strip()
        emoji = str(reaction.get("emoji") or "").strip()
        reactor = str(payload.get("from") or "business").strip() or "business"
        direction = str(row.get("direction") or "inbound")
        reactor_key = f"{direction}:{reactor}"

        if target_id:
            if emoji:
                db.execute(text("""
                    INSERT INTO whatsapp_message_reactions(
                        conversation_id, target_whatsapp_message_id,
                        reactor_key, direction, emoji
                    ) VALUES (
                        :conversation_id, :target_id,
                        :reactor_key, :direction, :emoji
                    )
                    ON CONFLICT (target_whatsapp_message_id, reactor_key)
                    DO UPDATE SET
                        emoji = EXCLUDED.emoji,
                        direction = EXCLUDED.direction,
                        updated_at = now()
                """), {
                    "conversation_id": row["conversation_id"],
                    "target_id": target_id,
                    "reactor_key": reactor_key,
                    "direction": direction,
                    "emoji": emoji,
                })
            else:
                db.execute(text("""
                    DELETE FROM whatsapp_message_reactions
                    WHERE target_whatsapp_message_id = :target_id
                      AND reactor_key = :reactor_key
                """), {
                    "target_id": target_id,
                    "reactor_key": reactor_key,
                })

        db.execute(text("DELETE FROM whatsapp_messages WHERE id = :id"), {"id": row["id"]})
        if direction == "inbound":
            db.execute(text("""
                UPDATE whatsapp_conversations
                SET unread_count = GREATEST(unread_count - 1, 0)
                WHERE id = :conversation_id
            """), {"conversation_id": row["conversation_id"]})
    db.commit()


def _greenie_comunas(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(text("""
        SELECT id_comuna,
               COALESCE(NULLIF(TRIM(nombre), ''), NULLIF(TRIM(comuna), '')) AS nombre
        FROM public.comunas
        WHERE COALESCE(NULLIF(TRIM(nombre), ''), NULLIF(TRIM(comuna), '')) IS NOT NULL
        ORDER BY COALESCE(NULLIF(TRIM(nombre), ''), NULLIF(TRIM(comuna), ''))
    """)).mappings().all()
    return [dict(row) for row in rows]


'''
backend = insert_before(
    backend,
    "def _conversation_or_404(db: Session, conversation_id: int):",
    helpers,
    "helpers flujo WhatsApp nativo",
)

endpoints = r'''

@router.get("/metadata/comunas")
def whatsapp_comunas(db: Session = Depends(get_db)):
    _ensure_tables(db)
    return {"ok": True, "items": _greenie_comunas(db)}


@router.get("/conversations/{conversation_id}/messages-v2")
def conversation_messages_v2(conversation_id: int, db: Session = Depends(get_db)):
    _sync_webhook_events(db)
    _normalize_reaction_messages(db, conversation_id)
    conversation = _conversation_or_404(db, conversation_id)
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET unread_count = 0, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id})

    rows = db.execute(text("""
        SELECT
            m.id,
            m.whatsapp_message_id,
            m.direction,
            m.message_type,
            m.body,
            m.status,
            m.sent_at,
            m.media_id,
            m.mime_type,
            m.filename,
            m.caption,
            m.media_size,
            COALESCE(
                NULLIF(m.reply_to_whatsapp_message_id, ''),
                NULLIF(m.raw_payload -> 'context' ->> 'id', '')
            ) AS reply_to_whatsapp_message_id,
            parent.id AS reply_to_id,
            parent.direction AS reply_to_direction,
            parent.message_type AS reply_to_type,
            parent.body AS reply_to_body,
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'emoji', r.emoji,
                        'direction', r.direction,
                        'reactor_key', r.reactor_key
                    )
                    ORDER BY r.updated_at, r.id
                )
                FROM whatsapp_message_reactions r
                WHERE r.target_whatsapp_message_id = m.whatsapp_message_id
            ), '[]'::jsonb) AS reactions
        FROM whatsapp_messages m
        LEFT JOIN whatsapp_messages parent
          ON parent.whatsapp_message_id = COALESCE(
              NULLIF(m.reply_to_whatsapp_message_id, ''),
              NULLIF(m.raw_payload -> 'context' ->> 'id', '')
          )
        WHERE m.conversation_id = :id
          AND m.message_type <> 'reaction'
        ORDER BY m.sent_at, m.id
        LIMIT 1000
    """), {"id": conversation_id}).mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        reply_id = item.pop("reply_to_id", None)
        reply_direction = item.pop("reply_to_direction", None)
        reply_type = item.pop("reply_to_type", None)
        reply_body = item.pop("reply_to_body", None)
        if item.get("reply_to_whatsapp_message_id"):
            item["reply_to"] = {
                "id": reply_id,
                "direction": reply_direction,
                "message_type": reply_type,
                "body": reply_body or "Mensaje no disponible",
            }
        else:
            item["reply_to"] = None
        if isinstance(item.get("reactions"), str):
            try:
                item["reactions"] = json.loads(item["reactions"])
            except Exception:
                item["reactions"] = []
        items.append(item)

    db.commit()
    return {
        "ok": True,
        "conversation": dict(conversation),
        "items": items,
    }


@router.post("/conversations/{conversation_id}/send-v2")
def send_message_v2(
    conversation_id: int,
    body: SendTextV2Body,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    target = _greenie_target_message(db, conversation_id, body.reply_to_message_id)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(conversation["wa_id"]),
        "type": "text",
        "text": {"preview_url": False, "body": body.text.strip()},
    }
    if target:
        payload["context"] = {"message_id": str(target["whatsapp_message_id"])}
    result = _greenie_meta_post_message(str(conversation["phone_number_id"]), payload)
    messages = result.get("messages") or []
    message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            reply_to_whatsapp_message_id
        ) VALUES (
            :conversation_id, :message_id, 'outbound',
            'text', :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :reply_to_whatsapp_message_id
        )
        RETURNING id, sent_at
    """), {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "body": body.text.strip(),
        "raw_payload": json.dumps(result, ensure_ascii=False),
        "reply_to_whatsapp_message_id": str(target["whatsapp_message_id"]) if target else None,
    }).mappings().one()
    db.execute(text("""
        UPDATE whatsapp_conversations
        SET last_message_at = :sent_at, updated_at = now()
        WHERE id = :id
    """), {"id": conversation_id, "sent_at": row["sent_at"]})
    db.commit()
    return {"ok": True, "message_id": message_id, "id": row["id"], "status": "accepted"}


@router.post("/conversations/{conversation_id}/send-reaction")
def send_reaction(
    conversation_id: int,
    body: SendReactionBody,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    target = _greenie_target_message(db, conversation_id, body.target_message_id)
    emoji = body.emoji.strip()
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(conversation["wa_id"]),
        "type": "reaction",
        "reaction": {
            "message_id": str(target["whatsapp_message_id"]),
            "emoji": emoji,
        },
    }
    result = _greenie_meta_post_message(str(conversation["phone_number_id"]), payload)
    if emoji:
        db.execute(text("""
            INSERT INTO whatsapp_message_reactions(
                conversation_id, target_whatsapp_message_id,
                reactor_key, direction, emoji
            ) VALUES (
                :conversation_id, :target_id,
                'outbound:business', 'outbound', :emoji
            )
            ON CONFLICT (target_whatsapp_message_id, reactor_key)
            DO UPDATE SET emoji = EXCLUDED.emoji, updated_at = now()
        """), {
            "conversation_id": conversation_id,
            "target_id": str(target["whatsapp_message_id"]),
            "emoji": emoji,
        })
    else:
        db.execute(text("""
            DELETE FROM whatsapp_message_reactions
            WHERE target_whatsapp_message_id = :target_id
              AND reactor_key = 'outbound:business'
        """), {"target_id": str(target["whatsapp_message_id"])})
    db.commit()
    return {"ok": True, "emoji": emoji, "meta": result}


@router.post("/conversations/{conversation_id}/send-media-v2")
def send_media_v2(
    conversation_id: int,
    body: SendMediaV2Body,
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
        raise HTTPException(status_code=413, detail="Archivo supera 16 MB")

    requested_mime = body.mime_type.lower().split(";", 1)[0].strip()
    if requested_mime == "image/webp" or body.filename.lower().endswith(".webp"):
        if len(content) > 100 * 1024:
            raise HTTPException(status_code=413, detail="El sticker supera 100 KB")
        filename = body.filename if body.filename.lower().endswith(".webp") else body.filename + ".webp"
        mime_type = "image/webp"
        voice = False
    else:
        filename, mime_type, content, voice = _normalise_voice_audio(
            body.filename,
            body.mime_type,
            content,
        )
    media_type = _media_type_for_mime(mime_type)
    media_id = _meta_upload_media(
        str(conversation["phone_number_id"]),
        filename,
        mime_type,
        content,
    )
    target = _greenie_target_message(db, conversation_id, body.reply_to_message_id)
    media_object: dict[str, Any] = {"id": media_id}
    if media_type == "audio" and voice:
        media_object["voice"] = True
    if body.caption and media_type in {"image", "video", "document"}:
        media_object["caption"] = body.caption
    if filename and media_type == "document":
        media_object["filename"] = filename
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(conversation["wa_id"]),
        "type": media_type,
        media_type: media_object,
    }
    if target:
        payload["context"] = {"message_id": str(target["whatsapp_message_id"])}
    result = _greenie_meta_post_message(str(conversation["phone_number_id"]), payload)
    messages = result.get("messages") or []
    whatsapp_message_id = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    label = body.caption or {
        "image": "[Imagen enviada]",
        "audio": "[Audio enviado]",
        "video": "[Video enviado]",
        "document": f"[Documento enviado] {filename}",
        "sticker": "[Sticker enviado]",
    }.get(media_type, "[Archivo enviado]")
    row = db.execute(text("""
        INSERT INTO whatsapp_messages(
            conversation_id, whatsapp_message_id, direction,
            message_type, body, status, sent_at, raw_payload,
            media_id, mime_type, filename, caption, media_size,
            reply_to_whatsapp_message_id
        ) VALUES (
            :conversation_id, :whatsapp_message_id, 'outbound',
            :message_type, :body, 'accepted', now(), CAST(:raw_payload AS JSONB),
            :media_id, :mime_type, :filename, :caption, :media_size,
            :reply_to_whatsapp_message_id
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
        "filename": filename,
        "caption": body.caption,
        "media_size": len(content),
        "reply_to_whatsapp_message_id": str(target["whatsapp_message_id"]) if target else None,
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
backend = insert_before(
    backend,
    '\n\n@router.post("/conversations/{conversation_id}/assign")',
    endpoints,
    "endpoints WhatsApp nativo",
)

# Fecha futura obligatoria, incluso si alguien intenta saltarse el navegador.
validation_anchor = '''    _ensure_tables(db)
    conversation = _conversation_or_404(db, conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
'''
validation_new = '''    _ensure_tables(db)
    if body.fecha_evento < date.today():
        raise HTTPException(
            status_code=400,
            detail="La fecha del evento no puede ser anterior a hoy",
        )
    conversation = _conversation_or_404(db, conversation_id)
    brand_code = str(conversation.get("brand_code") or "").strip().upper()
'''
if validation_new not in backend:
    if validation_anchor not in backend:
        raise SystemExit("ERROR: no se encontro validacion create lead")
    backend = backend.replace(validation_anchor, validation_new, 1)
    print("ACTUALIZADO: validacion fecha lead")

commune_anchor = '''        id_comuna = db.execute(text("""
            SELECT id_comuna
            FROM public.comunas
            WHERE UPPER(COALESCE(nombre, comuna)) = UPPER(:comuna)
               OR UPPER(COALESCE(nombre, comuna)) LIKE UPPER(:prefix)
            ORDER BY id_comuna
            LIMIT 1
        """), {"comuna": comuna, "prefix": comuna + "%"}).scalar()
'''
commune_new = commune_anchor + '''        if not id_comuna:
            raise HTTPException(status_code=400, detail="Selecciona una comuna valida de la lista")
'''
if "Selecciona una comuna valida de la lista" not in backend:
    if commune_anchor not in backend:
        raise SystemExit("ERROR: no se encontro validacion comuna lead")
    backend = backend.replace(commune_anchor, commune_new, 1)
    print("ACTUALIZADO: validacion comuna lead")

BACKEND.write_text(backend, encoding="utf-8")
print("GUARDADO:", BACKEND.relative_to(ROOT))


# =============================================================================
# FRONTEND
# =============================================================================
css = r'''
    /* GREENIE_NATIVE_FLOW_V1 */
    .replyComposer{display:none;align-items:center;gap:10px;padding:8px 12px;background:#eef6ff;border-top:1px solid #bfdbfe;border-left:4px solid #2563eb;font-size:12px}
    .replyComposer.open{display:flex}.replyComposerText{min-width:0;flex:1}.replyComposerTitle{font-weight:900;color:#1d4ed8}.replyComposerBody{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#475569}.replyComposerClose{border:0;background:transparent;font-size:20px;cursor:pointer}
    .messageQuote{border-left:4px solid #3b82f6;background:rgba(255,255,255,.58);border-radius:7px;padding:6px 8px;margin-bottom:6px;font-size:11px;color:#475569;max-width:100%;overflow:hidden}.messageQuote strong{display:block;color:#1e3a8a}.messageQuote span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .messageActions{display:flex;gap:3px;align-items:center;margin-top:5px;opacity:0;transition:opacity .12s}.bubble:hover .messageActions,.bubble.actionOpen .messageActions{opacity:1}.messageAction{border:0;background:rgba(255,255,255,.8);border-radius:999px;padding:3px 7px;cursor:pointer;font-size:13px}.messageAction:hover{background:#fff;transform:scale(1.08)}
    .messageReactions{display:flex;gap:4px;flex-wrap:wrap;margin-top:5px}.reactionChip{border:1px solid #cbd5e1;background:#fff;border-radius:999px;padding:2px 7px;font-size:14px;line-height:1.2}
    .leadDateHint{font-size:11px;color:var(--muted);margin-top:-4px}.leadSimpleTitle{font-weight:900;margin-bottom:4px}
'''

reply_html = r'''<div class="replyComposer" id="replyComposer">
        <div class="replyComposerText"><div class="replyComposerTitle">Responder mensaje</div><div class="replyComposerBody" id="replyComposerBody"></div></div>
        <button class="replyComposerClose" id="replyComposerClose" type="button" title="Cancelar respuesta">×</button>
      </div>'''

js = r'''
  /* GREENIE_NATIVE_FLOW_V1 */
  let __greenieReplyTarget=null;
  let __greenieComunasCache=null;

  function __greenieTodayChile(){
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Santiago',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const data=Object.fromEntries(parts.map(part=>[part.type,part.value]));
    return `${data.year}-${data.month}-${data.day}`;
  }

  function __greenieSetReply(message){
    if(!message?.id)return;
    __greenieReplyTarget=message;
    $('#replyComposerBody').textContent=message.body||message.caption||'Archivo';
    $('#replyComposer').classList.add('open');
    $('#message').focus();
  }

  function __greenieClearReply(){
    __greenieReplyTarget=null;
    $('#replyComposer').classList.remove('open');
    $('#replyComposerBody').textContent='';
  }

  $('#replyComposerClose').onclick=__greenieClearReply;

  async function __greenieSendReaction(message,emoji){
    if(!current||!message?.id)return;
    try{
      await api('/gia/whatsapp/conversations/'+current.id+'/send-reaction',{method:'POST',body:JSON.stringify({target_message_id:message.id,emoji})});
      await __greenieRefreshLive();
    }catch(error){alert(error.message)}
  }

  const __greenieNativeRender=renderMessages;
  renderMessages=function(rows){
    __greenieNativeRender(rows);
    const bubbles=[...document.querySelectorAll('#messages .bubble')];
    rows.forEach((message,index)=>{
      const bubble=bubbles[index];
      if(!bubble)return;
      bubble.dataset.messageId=message.id;
      const content=bubble.firstElementChild;
      if(!content)return;

      if(message.reply_to){
        const quote=document.createElement('div');
        quote.className='messageQuote';
        const title=document.createElement('strong');
        title.textContent=message.reply_to.direction==='outbound'?'Tú':'Cliente';
        const body=document.createElement('span');
        body.textContent=message.reply_to.body||'Mensaje no disponible';
        quote.append(title,body);
        content.prepend(quote);
      }

      const reactions=Array.isArray(message.reactions)?message.reactions:[];
      if(reactions.length){
        const row=document.createElement('div');
        row.className='messageReactions';
        reactions.forEach(reaction=>{
          const chip=document.createElement('span');
          chip.className='reactionChip';
          chip.textContent=reaction.emoji||'';
          chip.title=reaction.direction==='outbound'?'Tu reacción':'Reacción del cliente';
          row.appendChild(chip);
        });
        bubble.appendChild(row);
      }

      const actions=document.createElement('div');
      actions.className='messageActions';
      const reply=document.createElement('button');
      reply.type='button';reply.className='messageAction';reply.textContent='↩ Responder';
      reply.onclick=()=>__greenieSetReply(message);
      actions.appendChild(reply);
      ['👍','❤️','😂','😮','😢','🙏'].forEach(emoji=>{
        const button=document.createElement('button');
        button.type='button';button.className='messageAction';button.textContent=emoji;
        button.onclick=()=>__greenieSendReaction(message,emoji);
        actions.appendChild(button);
      });
      bubble.appendChild(actions);
      bubble.onclick=event=>{if(!event.target.closest('button,a,audio,video'))bubble.classList.toggle('actionOpen')};
    });
  };

  async function __greenieLoadComunas(){
    if(__greenieComunasCache)return __greenieComunasCache;
    const data=await api('/gia/whatsapp/metadata/comunas');
    __greenieComunasCache=data.items||[];
    return __greenieComunasCache;
  }

  renderSide=async function(){
    if(!current)return;
    $('#side').innerHTML=`
      <div class="card stack">
        <div><div class="label">Cliente WhatsApp</div><div class="value">${esc(current.profile_name||'Sin nombre')}</div></div>
        <div><div class="label">Teléfono</div><div class="value">+${esc(current.wa_id)}</div></div>
      </div>
      <form class="card stack" id="leadForm">
        <div class="leadSimpleTitle">Crear lead</div>
        <input class="field" id="leadName" placeholder="Nombre del cliente" maxlength="180" value="${esc(current.profile_name||'')}" required>
        <input class="field" id="leadDate" type="date" lang="es-CL" required>
        <div class="leadDateHint">Fecha del evento: DD/MM/AAAA. No admite fechas anteriores.</div>
        <select class="field" id="leadComuna"><option value="">Selecciona comuna</option></select>
        <button class="btn primary" id="leadSubmit" type="submit">Crear lead</button>
        <div id="leadResult"></div>
      </form>`;
    const date=$('#leadDate');
    date.min=__greenieTodayChile();
    date.value=date.min;
    $('#leadForm').onsubmit=createLead;
    try{
      const comunas=await __greenieLoadComunas();
      $('#leadComuna').innerHTML='<option value="">Selecciona comuna</option>'+comunas.map(item=>`<option value="${esc(item.nombre)}">${esc(item.nombre)}</option>`).join('');
    }catch(error){
      $('#leadComuna').innerHTML='<option value="">No se pudieron cargar las comunas</option>';
      console.warn('[GREENIE] comunas',error);
    }
  };

  createLead=async function(event){
    event.preventDefault();
    if(!current)return;
    const button=$('#leadSubmit'),result=$('#leadResult');
    const fecha=$('#leadDate').value;
    const today=__greenieTodayChile();
    if(!fecha||fecha<today){result.innerHTML='<div class="hint" style="color:#b91c1c">Selecciona una fecha igual o posterior a hoy.</div>';return}
    button.disabled=true;result.innerHTML='';
    try{
      const payload={nombre:$('#leadName').value.trim(),fecha_evento:fecha,comuna:$('#leadComuna').value||null};
      const response=await api('/gia/whatsapp/conversations/'+current.id+'/create-lead',{method:'POST',body:JSON.stringify(payload)});
      result.innerHTML=`<div class="ok">Lead #${esc(response.id_lead)} creado correctamente.</div>`;
    }catch(error){result.innerHTML=`<div class="hint" style="color:#b91c1c">${esc(error.message)}</div>`}finally{button.disabled=false}
  };

  $('#sendForm').onsubmit=async event=>{
    event.preventDefault();
    const text=$('#message').value.trim();
    if(!text||!current)return;
    $('#message').value='';
    try{
      await api('/gia/whatsapp/conversations/'+current.id+'/send-v2',{method:'POST',body:JSON.stringify({text,reply_to_message_id:__greenieReplyTarget?.id||null})});
      __greenieClearReply();
      await openConversation(current.id);
    }catch(error){alert(error.message)}
  };

  sendMediaBlob=async function(blob,filename,mimeType){
    if(!current)return;
    if(blob.size>16*1024*1024)throw new Error('El archivo supera 16 MB');
    const data_base64=await blobToBase64(blob),caption=$('#message').value.trim()||null;
    await api('/gia/whatsapp/conversations/'+current.id+'/send-media-v2',{method:'POST',body:JSON.stringify({filename,mime_type:mimeType||'application/octet-stream',data_base64,caption,reply_to_message_id:__greenieReplyTarget?.id||null})});
    $('#message').value='';
    __greenieClearReply();
    await openConversation(current.id);
  };
'''

for path in VIEWS:
    if not path.exists():
        print("OMITIDO:", path.relative_to(ROOT), "no existe")
        continue
    view = path.read_text(encoding="utf-8")
    if "mediaFile" not in view or "renderMessages" not in view:
        print("OMITIDO:", path.relative_to(ROOT), "no es vista multimedia")
        continue

    if "GREENIE_NATIVE_FLOW_V1" not in view:
        if "</style>" not in view:
            raise SystemExit(f"ERROR: {path} no tiene </style>")
        view = view.replace("</style>", css + "\n  </style>", 1)

        composer_match = re.search(r'<form class="composer" id="sendForm">', view)
        if not composer_match:
            raise SystemExit(f"ERROR: {path} no tiene compositor")
        view = view[:composer_match.start()] + reply_html + "\n      " + view[composer_match.start():]

        # Conversación normal y actualización en vivo usan la respuesta enriquecida.
        view = view.replace("+'/messages'", "+'/messages-v2'")

        iife_end = view.rfind("\n})();")
        if iife_end < 0:
            raise SystemExit(f"ERROR: {path} no tiene cierre IIFE")
        view = view[:iife_end] + "\n" + js + view[iife_end:]
        print("ACTUALIZADO:", path.relative_to(ROOT))
    else:
        print("YA_OK:", path.relative_to(ROOT))
    path.write_text(view, encoding="utf-8")

print("GREENIE_NATIVE_WHATSAPP_FLOW_OK")
