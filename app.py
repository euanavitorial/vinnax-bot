import os
import json
from collections import deque
from typing import Any, Dict, List, Callable

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO PADRÃO DO GEMINI ---
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
# -----------------------------------

# ====== VARIÁVEIS DE AMBIENTE ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Modelo compatível com google-generativeai==0.8.5
GEMINI_MODEL_NAME = "models/gemini-1.5-flash"

# --- SUPABASE CONFIG ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Monta a URL da API de clientes
if SUPABASE_URL:
    base_url = SUPABASE_URL.rstrip("/")
    CLIENTE_API_ENDPOINT = f"{base_url}/functions/v1/api-clients"
else:
    CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "")

LANCAMENTO_API_ENDPOINT = os.environ.get("LANCAMENTO_API_ENDPOINT", "https://api.seusistema.com/v1/lancamentos")

app = Flask(__name__)

# ====== Memória ======
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10

# ====== Utilidades ======
def extract_text(message: Dict[str, Any]) -> str:
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

# ======================================================================
# FERRAMENTAS (TOOLS)
# ======================================================================
TOOLS_MENU = [
    {
        "name": "criar_cliente",
        "description": "Cadastra um novo cliente no sistema.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "nome": {"type": "STRING", "description": "Nome do cliente."},
                "telefone": {"type": "STRING", "description": "Telefone (opcional)."},
                "email": {"type": "STRING", "description": "Email (opcional)."}
            },
            "required": ["nome"]
        }
    },
    {
        "name": "consultar_cliente_por_id",
        "description": "Busca cliente por ID.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "ID numérico do cliente."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "consultar_cliente_por_telefone",
        "description": "Busca cliente por telefone.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "telefone": {"type": "STRING", "description": "Número completo com DDI."}
            },
            "required": ["telefone"]
        }
    },
    {
        "name": "atualizar_cliente",
        "description": "Atualiza dados de um cliente existente.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER"},
                "nome": {"type": "STRING"},
                "telefone": {"type": "STRING"},
                "email": {"type": "STRING"}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "excluir_cliente",
        "description": "Exclui um cliente do sistema.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER"}
            },
            "required": ["id_cliente"]
        }
    }
]

# ======================================================================
# FUNÇÕES DA API SUPABASE
# ======================================================================
def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None):
    if not (SUPABASE_SERVICE_ROLE_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API não configurada."}
    try:
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        resp = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

def call_api_consultar_cliente_por_id(id_cliente: int):
    try:
        resp = requests.get(f"{CLIENTE_API_ENDPOINT}/{id_cliente}", headers=get_auth_headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

def call_api_consultar_cliente_por_telefone(telefone: str):
    try:
        resp = requests.post(CLIENTE_API_ENDPOINT, json={"phone": telefone}, headers=get_auth_headers(), timeout=20)
        resp.raise_for_status()
        dados = resp.json()
        if isinstance(dados, list) and not dados:
            return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente com {telefone}"}
        return dados[0] if isinstance(dados, list) else dados
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

def call_api_atualizar_cliente(id_cliente: int, nome=None, telefone=None, email=None):
    try:
        payload = {k: v for k, v in {"name": nome, "phone": telefone, "email": email}.items() if v}
        resp = requests.put(f"{CLIENTE_API_ENDPOINT}/{id_cliente}", json=payload, headers=get_auth_headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

def call_api_excluir_cliente(id_cliente: int):
    try:
        resp = requests.delete(f"{CLIENTE_API_ENDPOINT}/{id_cliente}", headers=get_auth_headers(), timeout=20)
        return {"status": "sucesso" if resp.status_code == 204 else "erro", "mensagem": resp.text}
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

TOOL_ROUTER = {
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone,
    "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
}

# ======================================================================
# GEMINI CONFIGURAÇÃO
# ======================================================================
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
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
        app.logger.exception(f"[GEMINI] Erro ao iniciar: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada.")

# ======================================================================
# GERAÇÃO DE RESPOSTA
# ======================================================================
def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "", client_phone: str = None):
    if not gemini_model:
        return f"Olá! Recebi sua mensagem: {user_text}"
    try:
        system_prompt = (
            "Você é um assistente da Vinnax Beauty. "
            "Responda com simpatia e profissionalismo. "
            "Use ferramentas apenas se o cliente pedir algo do sistema."
        )
        history_string = "\n".join(chat_history)
        prompt = f"{system_prompt}\n{initial_context}\nHistórico:\n{history_string}\nCliente: {user_text}\nAtendente:"
        resp = gemini_model.generate_content(prompt)
        return resp.candidates[0].content.parts[0].text.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro: {e}")
        return "Desculpe, ocorreu um erro interno."

# ======================================================================
# ROTAS DO FLASK
# ======================================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "vinnax-bot"}), 200

@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    raw = request.get_json(silent=True) or {}
    envelope = raw.get("data", raw)
    if isinstance(envelope, list) and envelope:
        envelope = envelope[0]

    key = envelope.get("key", {}) or {}
    if key.get("fromMe") is True:
        return jsonify({"status": "own_message_ignored"}), 200

    message = envelope.get("message", {}) or {}
    msg_id = key.get("id") or envelope.get("idMessage") or ""
    if msg_id in PROCESSED_IDS:
        return jsonify({"status": "duplicate_ignored"}), 200
    PROCESSED_IDS.append(msg_id)

    jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
    if not jid.endswith("@s.whatsapp.net"):
        return jsonify({"status": "ignored"}), 200
    client_phone = jid.replace("@s.whatsapp.net", "")

    text = extract_text(message).strip()
    if not text:
        return jsonify({"status": "no_text"}), 200

    if client_phone not in CHAT_SESSIONS:
        CHAT_SESSIONS[client_phone] = []
    current_history = CHAT_SESSIONS[client_phone]

    reply = answer_with_gemini(text, current_history, "", client_phone)

    CHAT_SESSIONS[client_phone].append(f"Cliente: {text}")
    CHAT_SESSIONS[client_phone].append(f"Atendente: {reply}")

    if EVOLUTION_KEY and EVOLUTION_URL_BASE and EVOLUTION_INSTANCE:
        try:
            url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
            headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
            payload = {"number": client_phone, "text": reply}
            requests.post(url_send, json=payload, headers=headers, timeout=20)
        except Exception as e:
            app.logger.exception(f"[EVOLUTION] Falha ao enviar: {e}")

    return jsonify({"status": "ok"}), 200

# ======================================================================
# EXECUÇÃO LOCAL (para testes)
# ======================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
