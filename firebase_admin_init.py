import os
import json
import firebase_admin
from firebase_admin import credentials

def init_firebase():
    # 1) Preferencial: Secret File no Render
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    # 2) Fallback: JSON completo em env (menos recomendado, mas funciona)
    cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

    if firebase_admin._apps:
        return

    if cred_path and os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {
            "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", "").strip() or None
        })
        return

    if cred_json:
        data = json.loads(cred_json)
        cred = credentials.Certificate(data)
        firebase_admin.initialize_app(cred, {
            "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", "").strip() or None
        })
        return

    raise RuntimeError(
        "Firebase credenciais não configuradas. "
        "Defina GOOGLE_APPLICATION_CREDENTIALS (secret file) "
        "ou FIREBASE_SERVICE_ACCOUNT_JSON."
    )
