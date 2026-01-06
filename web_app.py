import os
import re
import datetime as dt
from typing import Optional

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from firebase_admin import firestore, storage
from firebase_admin_init import init_firebase  # seu arquivo :contentReference[oaicite:5]{index=5}

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Firebase init
init_firebase()
db = firestore.client()
bucket = storage.bucket()

TOKENS_COLLECTION = os.getenv("ONLINE_FORM_TOKENS_COLLECTION", "OnlineFormTokens")
SESSIONS_COLLECTION = os.getenv("ONLINE_SESSIONS_COLLECTION", "DDS_Sessions")


def _norm_team(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).upper()


def _slugify(s: str) -> str:
    s = (s or "").strip().upper()
    s = re.sub(r"[^A-Z0-9]+", "-", s).strip("-").lower()
    return s or "reuniao"


def _make_session_id(date_iso: str, time_hhmm: str, assunto: str) -> str:
    ymd = date_iso.replace("-", "")
    hhmm = time_hhmm.replace(":", "")
    return f"dds-{ymd}-{hhmm}-{_slugify(assunto)[:40]}"


def _token_doc(token: str):
    return db.collection(TOKENS_COLLECTION).document(token)


def _validate_token(token: str) -> dict:
    if not token or len(token) < 12:
        raise HTTPException(status_code=400, detail="Token inválido")

    snap = _token_doc(token).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Token não encontrado")

    data = snap.to_dict() or {}
    if data.get("status") == "used":
        raise HTTPException(status_code=410, detail="Token já utilizado")

    exp = data.get("expireAt")
    if exp is not None:
        try:
            exp_dt = exp.to_datetime()
        except Exception:
            exp_dt = dt.datetime.fromisoformat(str(exp))
        if dt.datetime.utcnow() > exp_dt.replace(tzinfo=None):
            raise HTTPException(status_code=410, detail="Token expirado")

    return data


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/online/agendar", response_class=HTMLResponse)
def get_form(request: Request, token: str):
    t = _validate_token(token)
    return templates.TemplateResponse(
        "agendar.html",
        {
            "request": request,
            "token": token,
            "pref_subject": t.get("prefSubject", ""),
            "pref_date": t.get("prefDate", ""),
            "pref_time": t.get("prefTime", ""),
        },
    )


@app.post("/online/agendar", response_class=HTMLResponse)
async def post_form(
    request: Request,
    token: str = Form(...),
    data: str = Form(...),   # YYYY-MM-DD
    hora: str = Form(...),   # HH:MM
    assunto: str = Form(...),
    host: str = Form(...),
    cohost: Optional[str] = Form(None),
    capa: UploadFile = File(...),
):
    token_data = _validate_token(token)

    host_n = _norm_team(host)
    if not host_n:
        raise HTTPException(status_code=400, detail="Host é obrigatório")

    cohost_n = _norm_team(cohost) if cohost else ""

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", data):
        raise HTTPException(status_code=400, detail="Data inválida (use YYYY-MM-DD)")
    if not re.match(r"^\d{2}:\d{2}$", hora):
        raise HTTPException(status_code=400, detail="Hora inválida (use HH:MM)")
    if not assunto.strip():
        raise HTTPException(status_code=400, detail="Assunto é obrigatório")
    if not capa.filename:
        raise HTTPException(status_code=400, detail="Arquivo de capa inválido")

    session_id = _make_session_id(data, hora, assunto)
    channel_name = session_id

    # 1) Salva sessão no Firestore
    session_payload = {
        "type": "online",
        "date": data,
        "time": hora,
        "timezone": "America/Sao_Paulo",
        "subject": assunto.strip(),
        "status": "scheduled",
        "createdAt": firestore.SERVER_TIMESTAMP,
        "createdByEmail": token_data.get("senderEmail", ""),
        "roles": {
            "hostTeams": [host_n],
            "cohostTeams": ([cohost_n] if cohost_n else []),
            "participant": ["*"],
        },
        "channelName": channel_name,
        "formToken": token,
    }
    db.collection(SESSIONS_COLLECTION).document(session_id).set(session_payload, merge=True)

    # 2) Upload CAPA no Storage
    blob_path = f"DDSv2/ONLINE/{session_id}/CAPA/{capa.filename}"
    blob = bucket.blob(blob_path)
    blob.upload_from_file(
        capa.file,
        content_type=capa.content_type or "application/octet-stream",
    )

    # 3) Atualiza Firestore com coverFiles
    db.collection(SESSIONS_COLLECTION).document(session_id).set(
        {"coverFiles": [blob_path], "coverUpdatedAt": firestore.SERVER_TIMESTAMP},
        merge=True,
    )

    # 4) Marca token como usado
    _token_doc(token).set(
        {"status": "used", "usedAt": firestore.SERVER_TIMESTAMP, "sessionId": session_id},
        merge=True,
    )

    return templates.TemplateResponse(
        "sucesso.html",
        {
            "request": request,
            "session_id": session_id,
            "date": data,
            "time": hora,
            "subject": assunto,
            "host": host_n,
            "cohost": cohost_n,
        },
    )
