from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend/routers/whatsapp_gia.py"
VIEW_CANDIDATES = [
    ROOT / "web/views/tools_whatsapp.html",
    ROOT / "web/views/tools_whatsapp_v2.html",
    ROOT / "web/views/tools_whatsapp_greenie.html",
]


def add_import(text: str, anchor: str, new_import: str) -> str:
    if new_import.strip() in text:
        return text
    if anchor not in text:
        raise SystemExit(f"No se encontro ancla de import: {anchor!r}")
    return text.replace(anchor, anchor + new_import, 1)


backend = BACKEND.read_text(encoding="utf-8")
backend = add_import(backend, "import os\n", "import shutil\nimport subprocess\nimport tempfile\n")

helper_anchor = "def _media_type_for_mime(mime_type: str) -> str:\n"
helper_code = r'''def _find_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg  # type: ignore
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if executable and Path(executable).exists():
            return executable
    except Exception:
        pass
    return None


def _normalise_voice_audio(
    filename: str,
    mime_type: str,
    content: bytes,
) -> tuple[str, str, bytes, bool]:
    """
    Los navegadores normalmente graban WebM/Opus o MP4.
    WhatsApp no admite audio/webm. Para notas grabadas en el CRM se convierte
    siempre a OGG con codec Opus y se envia con audio.voice=true.
    """
    clean_mime = mime_type.lower().split(";", 1)[0].strip()
    lower_name = filename.lower()
    is_recording = lower_name.startswith(("audio_", "voice_", "nota_voz_"))
    needs_conversion = is_recording or clean_mime in {
        "audio/webm",
        "video/webm",
        "application/webm",
    } or lower_name.endswith(".webm")

    if not needs_conversion:
        return filename, clean_mime, content, False

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise HTTPException(
            status_code=503,
            detail=(
                "El servidor no tiene FFmpeg para convertir la nota de voz a "
                "OGG/Opus. Instala imageio-ffmpeg en el entorno virtual."
            ),
        )

    input_suffix = ".webm"
    if clean_mime == "audio/mp4" or lower_name.endswith((".m4a", ".mp4")):
        input_suffix = ".m4a"
    elif clean_mime == "audio/ogg" or lower_name.endswith(".ogg"):
        input_suffix = ".ogg"

    with tempfile.TemporaryDirectory(prefix="greenie_voice_") as tmp:
        source = Path(tmp) / f"input{input_suffix}"
        output = Path(tmp) / "voice.ogg"
        source.write_bytes(content)
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", str(source),
                "-vn",
                "-ac", "1",
                "-ar", "48000",
                "-c:a", "libopus",
                "-b:a", "32k",
                "-application", "voip",
                str(output),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if process.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            error = process.stderr.decode("utf-8", errors="replace")[-1200:]
            raise HTTPException(
                status_code=422,
                detail=f"No se pudo convertir la nota de voz a OGG/Opus: {error}",
            )
        converted = output.read_bytes()

    stem = Path(filename).stem or "nota_voz"
    return f"{stem}.ogg", "audio/ogg", converted, True


'''
if "def _normalise_voice_audio(" not in backend:
    if helper_anchor not in backend:
        raise SystemExit("No se encontro _media_type_for_mime")
    backend = backend.replace(helper_anchor, helper_code + helper_anchor, 1)

# Agregar parametro voice a la funcion que envia media.
old_signature = '''def _meta_send_uploaded_media(
    phone_number_id: str,
    to: str,
    media_type: str,
    media_id: str,
    filename: str,
    caption: str | None,
) -> dict[str, Any]:'''
new_signature = '''def _meta_send_uploaded_media(
    phone_number_id: str,
    to: str,
    media_type: str,
    media_id: str,
    filename: str,
    caption: str | None,
    voice: bool = False,
) -> dict[str, Any]:'''
if old_signature in backend:
    backend = backend.replace(old_signature, new_signature, 1)
elif new_signature not in backend:
    raise SystemExit("No se encontro firma _meta_send_uploaded_media")

voice_anchor = '''    media_object: dict[str, Any] = {"id": media_id}
'''
voice_block = '''    media_object: dict[str, Any] = {"id": media_id}
    if media_type == "audio" and voice:
        media_object["voice"] = True
'''
if voice_block not in backend:
    if voice_anchor not in backend:
        raise SystemExit("No se encontro media_object")
    backend = backend.replace(voice_anchor, voice_block, 1)

old_send_block = '''    mime_type = body.mime_type.lower().split(";", 1)[0].strip()
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
    )'''
new_send_block = '''    filename, mime_type, content, voice = _normalise_voice_audio(
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
    result = _meta_send_uploaded_media(
        str(conversation["phone_number_id"]),
        str(conversation["wa_id"]),
        media_type,
        media_id,
        filename,
        body.caption,
        voice=voice,
    )'''
if old_send_block in backend:
    backend = backend.replace(old_send_block, new_send_block, 1)
elif new_send_block not in backend:
    raise SystemExit("No se encontro bloque send_media")

# Persistir nombre y tamano convertidos, no los originales.
backend = backend.replace('"filename": body.filename,', '"filename": filename,')
backend = backend.replace('"media_size": len(content),', '"media_size": len(content),')
backend = backend.replace('"document": f"[Documento enviado] {body.filename}",', '"document": f"[Documento enviado] {filename}",')

BACKEND.write_text(backend, encoding="utf-8")
print("ACTUALIZADO:", BACKEND.relative_to(ROOT))

for path in VIEW_CANDIDATES:
    if not path.exists():
        continue
    view = path.read_text(encoding="utf-8")
    original = view
    view = view.replace(
        "const choices=['audio/mp4','audio/ogg;codecs=opus','audio/webm;codecs=opus','audio/webm'];",
        "const choices=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm'];",
    )
    view = view.replace(
        "sendMediaBlob(blob,'audio_'+Date.now()+'.'+ext,type)",
        "sendMediaBlob(blob,'voice_'+Date.now()+'.'+ext,type)",
    )
    if view != original:
        path.write_text(view, encoding="utf-8")
        print("ACTUALIZADO:", path.relative_to(ROOT))
    else:
        print("SIN_CAMBIOS:", path.relative_to(ROOT))

print("GREENIE_VOICE_AUDIO_FIX_OK")
