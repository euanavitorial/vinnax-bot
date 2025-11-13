import os
from collections import deque
from typing import Any, Dict, List

from flask import Flask, request, jsonify
import requests

# ====== Config (via variáveis de ambiente) ======
# As variáveis abaixo são lidas do painel "Environment" do Render.
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-flash-latest")

app = Flask(__name__)

# ====== Memória e Deduplicação ======
# Anti-loop / deduplicação simples
PROCESSED_IDS = deque(maxlen=500)

# NOVA ADIÇÃO: Memória de Chat
# Guarda o histórico de conversa para cada número de telefone
# Formato: {"numero_telefone": ["Cliente: Oi", "Atendente: Olá!"]}
CHAT_SESSIONS: Dict[str, List[str]] = {}
# Define o quão "longa" a memória deve ser (últimas 10 mensagens)
CHAT_HISTORY_LENGTH = 10

# ====== Gemini (carregamento opcional) ======
gemini_model = None
if GEMINI_API_KEY:
    try:
        from google import generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        
        # Tenta carregar o modelo definido
        try:
            gemini_model = genai.GenerativeModel(GEMINI_MODEL)
            app.logger.info(f"[GEMINI] Modelo carregado: {GEMINI_MODEL}")
        except Exception as e:
            app.logger.warning(f"[GEMINI] Falha ao carregar {GEMINI_MODEL}, tentando fallback: {e}")
            gemini_model = genai.GenerativeModel("models/gemini-1.5-flash")
            app.logger.info("[GEMINI] Modelo carregado: models/gemini-1.5-flash")

    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada; usando respostas simples.")


# MODIFICADO: Agora aceita um 'chat_history'
def answer_with_gemini(user_text: str, chat_history: List[str]) -> str:
    """
    Usa o Gemini se disponível; caso contrário, retorna uma resposta simples.
    Agora inclui o histórico da conversa.
    """
    if not gemini_model:
        return f"Olá! Recebi sua mensagem: {user_text}"

    try:
        system_prompt = (
            "Você é um assistente da Vinnax Beauty. "
            "Seu objetivo é ser simpático, profissional e ajudar o cliente. "
            "Se for o início da conversa, cumprimente e pergunte o nome do cliente. "
            "Se o cliente disser o nome, continue a conversa perguntando como pode ajudá-lo. "
            "Responda em português, de forma breve."
        )
        
        # Transforma o histórico em uma string formatada
        history_string = "\n".join(chat_history)

        # NOVO PROMPT: Inclui o histórico antes da nova mensagem
        full_prompt = (
            f"{system_prompt}\n\n"
            f"=== Histórico da Conversa ===\n"
            f"{history_string}\n"
            f"=== Nova Mensagem ===\n"
            f"Cliente: {user_text}\n"
            f"Atendente:"
        )

        resp = gemini_model.generate_content(full_prompt)
        txt = (getattr(resp, "text", "") or "").strip()
        
        if not txt:
            return "Poderia repetir, por favor?"
        return txt
        
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao gerar resposta: {e}")
        return "Desculpe, não consegui responder agora."


# ====== Utilidades WhatsApp ======
def extract_text(message: Dict[str, Any]) -> str:
    """Extrai o texto de diversos tipos de mensagem do WhatsApp."""
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


# ====== Rotas ======
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "vinnax-bot"}), 200


# MODIFICADO: Agora usa e salva o CHAT_SESSIONS
@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    raw = request.get_json(silent=True) or {}
    envelope = raw.get("data", raw)

    if isinstance(envelope, list) and envelope:
        envelope = envelope[0]
    if not isinstance(envelope, dict):
        return jsonify({"status": "bad_payload"}), 200

    key = envelope.get("key", {}) or {}

    if key.get("fromMe") is True:
        return jsonify({"status": "own_message_ignored"}), 200

    message = envelope.get("message", {}) or {}
    if not message:
        return jsonify({"status": "no_message_ignored"}), 200

    msg_id = key.get("id") or envelope.get("idMessage") or ""
    if msg_id:
        if msg_id in PROCESSED_IDS:
            return jsonify({"status": "duplicate_ignored"}), 200
        PROCESSED_IDS.append(msg_id)

    jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
    if not jid.endswith("@s.whatsapp.net"):
        return jsonify({"status": "non_user_ignored"}), 200
    number = jid.replace("@s.whatsapp.net", "")

    text = extract_text(message).strip()
    if not text:
        return jsonify({"status": "no_text_ignored"}), 200

    # === LÓGICA DA MEMÓRIA (INÍCIO) ===
    
    # 1. Pega o histórico antigo deste número ou cria uma lista vazia
    if number not in CHAT_SESSIONS:
        CHAT_SESSIONS[number] = []
    
    current_history = CHAT_SESSIONS[number]
    
    # === LÓGICA DA MEMÓRIA (FIM) ===

    # 2. Gera resposta (AGORA PASSANDO O HISTÓRICO)
    reply = answer_with_gemini(text, current_history)

    # === LÓGICA DA MEMÓRIA (SALVANDO) ===
    
    # 3. Salva a interação atual no histórico
    CHAT_SESSIONS[number].append(f"Cliente: {text}")
    CHAT_SESSIONS[number].append(f"Atendente: {reply}")
    
    # 4. Mantém o histórico com um tamanho máximo (para não usar muita memória)
    while len(CHAT_SESSIONS[number]) > CHAT_HISTORY_LENGTH:
        CHAT_SESSIONS[number].pop(0) # Remove a mensagem mais antiga

    # === LÓGICA DA MEMÓRIA (COMPLETA) ===

    # 5. Envio via Evolution API
    if not (EVOLUTION_KEY and EVOLUTION_URL_BASE and EVOLUTION_INSTANCE):
        app.logger.warning("[EVOLUTION] Variáveis de ambiente ausentes.")
        return jsonify({"status": "missing_env"}), 200

    try:
        url_send = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
        payload = {"number": number, "text": reply}
        res = requests.post(url_send, json=payload, headers=headers, timeout=20)
        app.logger.info(f"[EVOLUTION] {res.status_code} -> {res.text}")
    except Exception as e:
        app.logger.exception(f"[EVOLUTION] Erro ao enviar: {e}")

    return jsonify({"status": "ok"}), 200

# (sem bloco if __name__ == "__main__": — produção usa gunicorn: `gunicorn app:app`)
