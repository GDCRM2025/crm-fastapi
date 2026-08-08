from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"
VIEWS = [
    ROOT / "web/views/tools_whatsapp_greenie.html",
    ROOT / "web/views/tools_whatsapp.html",
    ROOT / "web/views/tools_whatsapp_v2.html",
]


# -----------------------------------------------------------------------------
# Backend: procesar reacciones como emoji real. Los stickers ya usan media_id,
# mime_type y el endpoint multimedia existente.
# -----------------------------------------------------------------------------
backend = BACKEND.read_text(encoding="utf-8")
reaction_block = '''                        if message_type == "reaction":
                            reaction = message.get("reaction") or {}
                            emoji = str(reaction.get("emoji") or "").strip()
                            target_id = str(reaction.get("message_id") or "").strip()
                            body = emoji or "Reacción eliminada"
                            if target_id:
                                body = f"{body}"
                        elif message_type == "text":
'''

if 'if message_type == "reaction":' not in backend:
    anchor = '                        if message_type == "text":\n'
    if anchor not in backend:
        raise SystemExit("ERROR: no se encontro el bloque message_type del backend")
    backend = backend.replace(anchor, reaction_block, 1)
    print("BACKEND: reacciones habilitadas")
else:
    print("BACKEND: reacciones ya habilitadas")

BACKEND.write_text(backend, encoding="utf-8")


# -----------------------------------------------------------------------------
# Frontend: polling rápido de lista + conversación abierta, sin parpadeos.
# También muestra reacciones y stickers WebP, incluidos stickers animados.
# -----------------------------------------------------------------------------
css = r'''
    /* GREENIE_REALTIME_V1 */
    .stickerMedia{display:block;max-width:180px;max-height:180px;object-fit:contain;border-radius:10px;background:transparent}
    .reactionEmoji{font-size:28px;line-height:1.15;display:inline-block;min-width:34px;text-align:center}
    .reactionLabel{font-size:10px;color:var(--muted);margin-left:6px;vertical-align:middle}
'''

js = r'''
  /* GREENIE_REALTIME_V1 */
  let __greeniePollBusy=false;
  let __greenieLiveSignature='';
  let __greenieLiveConversationId=null;

  async function __greenieLoadSticker(messageId,img){
    try{
      const response=await fetch(
        API_BASE+'/gia/whatsapp/messages/'+messageId+'/media',
        {headers:authHeaders({})}
      );
      if(!response.ok)throw new Error('HTTP '+response.status);
      const blob=await response.blob();
      const url=URL.createObjectURL(blob);
      img.onload=()=>setTimeout(()=>URL.revokeObjectURL(url),1500);
      img.src=url;
    }catch(error){
      img.alt='No se pudo cargar el sticker';
      console.warn('[GREENIE] sticker',error);
    }
  }

  const __greenieBaseRenderMessages=renderMessages;
  renderMessages=function(rows){
    __greenieBaseRenderMessages(rows);
    const bubbles=[...document.querySelectorAll('#messages .bubble')];
    rows.forEach((message,index)=>{
      const bubble=bubbles[index];
      if(!bubble)return;
      const content=bubble.firstElementChild;
      if(!content)return;

      if(message.message_type==='reaction'){
        content.innerHTML='';
        const emoji=document.createElement('span');
        emoji.className='reactionEmoji';
        emoji.textContent=message.body||'Reacción eliminada';
        const label=document.createElement('span');
        label.className='reactionLabel';
        label.textContent='reacción';
        content.append(emoji,label);
      }

      if(message.message_type==='sticker'&&message.media_id){
        content.innerHTML='';
        const img=document.createElement('img');
        img.className='stickerMedia';
        img.alt='Sticker';
        content.appendChild(img);
        __greenieLoadSticker(message.id,img);
      }
    });
  };

  function __greenieRowsSignature(rows){
    return JSON.stringify((rows||[]).map(message=>[
      message.id,
      message.whatsapp_message_id,
      message.message_type,
      message.body,
      message.status,
      message.media_id,
      message.mime_type,
      message.caption,
      message.sent_at
    ]));
  }

  async function __greenieRefreshLive(){
    if(__greeniePollBusy||document.hidden)return;
    __greeniePollBusy=true;
    try{
      await load();
      if(!current)return;

      const conversationId=current.id;
      const data=await api('/gia/whatsapp/conversations/'+conversationId+'/messages');
      if(!current||current.id!==conversationId)return;

      const rows=data.items||[];
      const signature=__greenieRowsSignature(rows);
      if(
        __greenieLiveConversationId!==conversationId||
        __greenieLiveSignature!==signature
      ){
        const messagesEl=$('#messages');
        const oldHeight=messagesEl.scrollHeight;
        const oldBottom=oldHeight-messagesEl.scrollTop;
        const wasNearBottom=oldBottom<=messagesEl.clientHeight+140;

        current=data.conversation;
        currentData=data;
        $('#chatName').textContent=current.profile_name||current.wa_id;
        $('#chatSub').textContent='+'+current.wa_id+' · '+(current.brand_code||'SIN ASIGNAR')+' · '+(current.executive_name||'Sin ejecutivo');
        renderMessages(rows);

        if(!wasNearBottom){
          messagesEl.scrollTop=Math.max(0,messagesEl.scrollHeight-oldBottom);
        }
        __greenieLiveConversationId=conversationId;
        __greenieLiveSignature=signature;
      }
    }catch(error){
      console.warn('[GREENIE] actualización en vivo',error);
    }finally{
      __greeniePollBusy=false;
    }
  }

  const __greenieLiveTimer=setInterval(__greenieRefreshLive,2000);
  document.addEventListener('visibilitychange',()=>{
    if(!document.hidden)__greenieRefreshLive();
  });
  window.addEventListener('focus',__greenieRefreshLive);
  window.addEventListener('online',__greenieRefreshLive);
'''

for path in VIEWS:
    if not path.exists():
        print("OMITIDO:", path.relative_to(ROOT), "no existe")
        continue

    view = path.read_text(encoding="utf-8")
    if "mediaFile" not in view or "renderMessages" not in view:
        print("OMITIDO:", path.relative_to(ROOT), "no es la vista multimedia")
        continue

    if "GREENIE_REALTIME_V1" not in view:
        if "</style>" not in view:
            raise SystemExit(f"ERROR: {path} no tiene </style>")
        view = view.replace("</style>", css + "\n  </style>", 1)

        # Elimina el polling antiguo de 15 segundos. El nuevo refresca cada 2 s
        # tanto la lista como la conversación abierta.
        view = re.sub(
            r"setInterval\(\(\)=>load\(\)\.catch\(\(\)=>\{\}\),15000\);?",
            "",
            view,
        )

        marker = "\n})();"
        position = view.rfind(marker)
        if position < 0:
            raise SystemExit(f"ERROR: {path} no tiene cierre del IIFE")
        view = view[:position] + "\n" + js + view[position:]
        print("ACTUALIZADO:", path.relative_to(ROOT))
    else:
        print("YA_OK:", path.relative_to(ROOT))

    path.write_text(view, encoding="utf-8")

print("GREENIE_REALTIME_REACTIONS_OK")
