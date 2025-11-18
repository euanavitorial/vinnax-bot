import os
import json
from collections import deque
from typing import Any, Dict, List, Callable
from flask import Flask, request, jsonify
import requests
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# ============================================================
# CONFIGURAÇÕES GERAIS
# ============================================================

EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Modelo estável e compatível com a versão atual da API
GEMINI_MODEL_NAME = "models/gemini-2.5-flash"

# Configuração Supabase (opcional)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if SUPABASE_URL:
    base_url = SUPABASE_URL.rstrip("/")
    CLIENTE_API_ENDPOINT = f"{base_url}/functions/v1/api-clients"
else:
    CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "")

app = Flask(__name__)

# ============================================================
# MEMÓRIA DE CONVERSA
# ============================================================
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def extract_text(message: Dict[str, Any]) -> str:
    """Extrai texto de mensagens do WhatsApp"""
    if not isinstance(message, dict):
        return ""
    if "conversation" in message:
        return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message:
        return (message["extendedTextMessage"].get("text") or "").strip()
    for mid in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        if mid in message:
            return (message[mid].get("caption") or "").strip()
    return ""


def get_auth_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json"
    }


# ============================================================
# SUPABASE TOOL (exemplo)
# ============================================================

def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None):
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API não configurada."}
    try:
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v}
        r = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}


# ============================================================
# TOOLS (GEMINI)
# ============================================================

TOOLS_MENU = [
    {
        "name": "criar_cliente",
        "description": "Cadastra um novo cliente no sistema.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "nome": {"type": "STRING"},
                "telefone": {"type": "STRING"},
                "email": {"type": "STRING"}
            },
            "required": ["nome"]
        }
    }
]

TOOL_ROUTER = {
    "criar_cliente": call_api_criar_cliente
}


# ============================================================
# GEMINI CONFIG
# ============================================================

gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)

        app.logger.info("=== MODELOS DISPONÍVEIS ===")
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                app.logger.info(f"✅ {m.name}")
        app.logger.info("============================")

        gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            },
            tools=TOOLS_MENU
        )

        app.logger.info(f"[GEMINI] Modelo carregado: {GEMINI_MODEL_NAME}")
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada.")


# ============================================================
# FUNÇÃO DE RESPOSTA
# ============================================================

def answer_with_gemini(user_text: str, chat_history: List[str]):
    if not gemini_model:
        return "Olá! Sistema iniciado com sucesso."
    try:
        history_str = "\n".join(chat_history)
        prompt = (
            "Você é um assistente da Vinnax Beauty. "
            "Responda de forma simpática e natural.\n"
            f"Histórico:\n{history_str}\n"
            f"Cliente: {user_text}\nAtendente:"
        )
        response = gemini_model.generate_content(prompt)
        return response.candidates[0].content.parts[0].text.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral: {e}")
        return f"Erro interno: {e}"


# ============================================================
# ROTAS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({"service": "vinnax-bot", "status": "ok"}), 200


@app.route("/test-ai", methods=["POST"])
def test_ai():
    data = request.get_json() or {}
    question = data.get("question", "")
    if not question:
        return jsonify({"error": "Envie o campo 'question' no body JSON"}), 400
    reply = answer_with_gemini(question, [])
    return jsonify({"reply": reply}), 200


@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    raw = request.get_json(silent=True) or {}
    envelope = raw.get("data", raw)

    if isinstance(envelope, list) and envelope:
        envelope = envelope[0]
    if not isinstance(envelope, dict):
        return jsonify({"status": "bad_payload"}), 200

    key = envelope.get("key", {}) or {}
    if key.get("fromMe"):
        return jsonify({"status": "own_message_ignored"}), 200

    message = envelope.get("message", {}) or {}
    if not message:
        return jsonify({"status": "no_message_ignored"}), 200

    jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
    if not jid.endswith("@s.whatsapp.net"):
        return jsonify({"status": "non_user_ignored"}), 200
    client_phone = jid.replace("@s.whatsapp.net", "")

    text = extract_text(message).strip()
    if not text:
        return jsonify({"status": "no_text_ignored"}), 200

    if client_phone not in CHAT_SESSIONS:
        CHAT_SESSIONS[client_phone] = []
    history = CHAT_SESSIONS[client_phone]

    reply = answer_with_gemini(text, history)

    history.append(f"Cliente: {text}")
    history.append(f"Atendente: {reply}")
    while len(history) > CHAT_HISTORY_LENGTH:
        history.pop(0)

    if EVOLUTION_KEY and EVOLUTION_URL_BASE and EVOLUTION_INSTANCE:
        try:
            url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
            headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
            payload = {"number": client_phone, "text": reply}
            requests.post(url_send, json=payload, headers=headers, timeout=20)
        except Exception as e:
            app.logger.exception(f"[EVOLUTION] Erro ao enviar mensagem: {e}")

    return jsonify({"status": "ok"}), 200


# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
