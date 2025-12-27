import os
import time
from enum import Enum

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from dotenv import load_dotenv
load_dotenv()


# IMPORTA O CÓDIGO OFICIAL DA AGORA
# -> copie a pasta DynamicKey/AgoraDynamicKey/python3/src
#    para dds_token_server/app/agora_src
from agora_src.RtcTokenBuilder2 import (
    RtcTokenBuilder,
    Role_Publisher,
    Role_Subscriber,
)


# RTM Token Builder (python3/src)
from agora_src.RtmTokenBuilder import RtmTokenBuilder, Role_Rtm_User


# ==========================================================
# Configurações de ambiente
# ==========================================================

APP_ID = os.getenv("AGORA_APP_ID")
APP_CERTIFICATE = os.getenv("AGORA_APP_CERTIFICATE")

if not APP_ID or not APP_CERTIFICATE:
    raise RuntimeError(
        "AGORA_APP_ID e AGORA_APP_CERTIFICATE devem estar definidos nas variáveis de ambiente."
    )

# Opcional: chave simples para proteger o endpoint
API_KEY = os.getenv("TOKEN_SERVER_API_KEY")  # se None, não valida


# ==========================================================
# Modelos de entrada/saída
# ==========================================================

class ClientRole(str, Enum):
    host = "host"
    cohost = "cohost"
    participant = "participant"


class TokenRequest(BaseModel):
    channel: str = Field(..., min_length=1, description="Nome do canal (string)")
    uid: int = Field(..., ge=0, description="UID inteiro único no canal")
    role: ClientRole = Field(..., description="host | cohost | participant")
    expire_seconds: int = Field(
        3600,
        ge=60,
        le=24 * 60 * 60,
        description="Tempo de expiração em segundos (min 60, máx 86400)"
    )
    user_account: str | None = Field(
        None,
        min_length=1,
        description="(Opcional) Identidade RTM (string). Se omitido, usa uid como string."
    )
    # opcional: header lógico de quem chama
    api_key: str | None = Field(
        None,
        description="API key opcional; se configurada no servidor, deve bater com TOKEN_SERVER_API_KEY"
    )


class TokenResponse(BaseModel):
    token: str
    expire_at: int  # epoch em segundos
    now: int        # epoch em segundos
    channel: str
    uid: int
    role: ClientRole

class RtmTokenResponse(BaseModel):
    token: str
    expire_at: int
    now: int
    uid: int    

class CombinedTokenResponse(BaseModel):
    rtc_token: str
    rtm_token: str
    expire_at: int
    now: int
    channel: str
    uid: int
    role: ClientRole
    user_account: str

# ==========================================================
# Inicialização FastAPI
# ==========================================================

app = FastAPI(title="DDS Agora RTC Token Server", version="1.0.0")

# CORS – ajuste para os domínios reais do seu app em produção
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # em produção: ["https://seu-dominio.com", "https://ddsapp.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# Endpoint: geração COMBINADA (RTC + RTM) – recomendado pro app
# ==========================================================

@app.post("/token", response_model=CombinedTokenResponse, tags=["rtc", "rtm"])
def generate_tokens(payload: TokenRequest):
    """
    Gera tokens de RTC + RTM em uma única chamada:
    - RTC: entrar no canal e publicar/assistir (conforme role)
    - RTM/Signaling: presença confiável e eventos (JOIN/LEAVE/HEARTBEAT etc.)
    """
    validate_api_key(payload.api_key)

    channel = payload.channel.strip()
    if not channel:
        raise HTTPException(status_code=400, detail="Channel name cannot be empty.")

    uid = payload.uid
    if uid < 0:
        raise HTTPException(status_code=400, detail="UID must be >= 0.")

    agora_role = map_role(payload.role)

    now_ts = int(time.time())
    expire_ts = now_ts + payload.expire_seconds

    user_account = (payload.user_account or str(uid)).strip()
    if not user_account:
        raise HTTPException(status_code=400, detail="user_account cannot be empty when provided.")

    try:
        rtc_token = RtcTokenBuilder.build_token_with_uid(
            APP_ID,
            APP_CERTIFICATE,
            channel,
            uid,
            agora_role,
            token_expire=expire_ts,
            privilege_expire=expire_ts,
        )

        rtm_token = RtmTokenBuilder.build_token(
            APP_ID,
            APP_CERTIFICATE,
            user_account,
            Role_Rtm_User,
            expire_ts
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to generate tokens: {e}")

    return CombinedTokenResponse(
        rtc_token=rtc_token,
        rtm_token=rtm_token,
        expire_at=expire_ts,
        now=now_ts,
        channel=channel,
        uid=uid,
        role=payload.role,
        user_account=user_account,
    )
# ==========================================================
# Funções auxiliares
# ==========================================================

def map_role(client_role: ClientRole):
    """
    Mapeia host / cohost / participant para roles de RTC da Agora.
    A Agora só distingue Publisher / Subscriber; a diferença entre host/cohost
    você trata na sua lógica de aplicação.
    """
    if client_role in (ClientRole.host, ClientRole.cohost):
        return Role_Publisher  # pode publicar áudio/vídeo
    return Role_Subscriber      # só assiste


def validate_api_key(api_key: str | None):
    if API_KEY and api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ==========================================================
# Healthcheck simples
# ==========================================================

@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok", "time": int(time.time())}


# ==========================================================
# Endpoint principal: geração de token RTC
# ==========================================================

@app.post("/rtc/token", response_model=TokenResponse, tags=["rtc"])
def generate_rtc_token(payload: TokenRequest):
    # 1. Autorização simples (opcional)
    validate_api_key(payload.api_key)

    # 2. Validações básicas
    channel = payload.channel.strip()
    if not channel:
        raise HTTPException(status_code=400, detail="Channel name cannot be empty.")

    uid = payload.uid
    if uid < 0:
        raise HTTPException(status_code=400, detail="UID must be >= 0.")

    agora_role = map_role(payload.role)

    # 3. Calcula expiração (epoch seconds)
    now_ts = int(time.time())
    expire_ts = now_ts + payload.expire_seconds

    # 4. Gera token usando RtcTokenBuilder2 oficial
    try:
        # A API oficial em Python aceita token_expire / privilege_expire em epoch seconds
        # conforme exemplos da própria Agora. :contentReference[oaicite:2]{index=2}
        token = RtcTokenBuilder.build_token_with_uid(
            APP_ID,
            APP_CERTIFICATE,
            channel,
            uid,
            agora_role,
            token_expire=expire_ts,
            privilege_expire=expire_ts,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to generate token: {e}")

    return TokenResponse(
        token=token,
        expire_at=expire_ts,
        now=now_ts,
        channel=channel,
        uid=uid,
        role=payload.role,
    )


# ==========================================================
# Endpoint: geração de token RTM (Signaling) – ENTERPRISE
# ==========================================================

@app.post("/rtm/token", response_model=RtmTokenResponse, tags=["rtm"])
def generate_rtm_token(payload: TokenRequest):
    """
    Gera token RTM (Signaling) para:
    - login RTM
    - presença confiável
    - atributos persistentes (ex.: teamName)
    """
    # 1. Autorização simples (opcional)
    validate_api_key(payload.api_key)

    # 2. Validações básicas
    now_ts = int(time.time())
    expire_ts = now_ts + payload.expire_seconds

    try:
        # RTM usa user_account (string). Preferir payload.user_account quando enviado.
        user_account = (payload.user_account or str(payload.uid)).strip()
        if not user_account:
            raise ValueError("user_account cannot be empty.")
        rtm_token = RtmTokenBuilder.build_token(
            APP_ID,
            APP_CERTIFICATE,
            user_account,
            Role_Rtm_User,
            expire_ts
        )

    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to generate RTM token: {e}")

    return RtmTokenResponse(
        token=rtm_token,
        expire_at=expire_ts,
        now=now_ts,
        uid=int(payload.uid),
    )