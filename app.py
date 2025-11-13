import os
from collections import deque
from typing import Any, Dict

from flask import Flask, request, jsonify
import requests

# ====== Config (via variáveis de ambiente) ======
# OBS: agora todas leem pelo NOME da env var (sem valores hardcoded!)
EVOLUTION_KEY       = os.environ.get("87DBDCBDE4D8-414B-BFDD-7FFE386B673B", "")
EVOLUTION_URL_BASE  = os.environ.get("https://evorv.conectarioverde.com.br", "")
EVOLUTION_INSTANCE  = os.environ.get("VINNAXBEAUTY", "")

GEMINI_API_KEY      = os.environ.get("AIzaSyAQCVOeLLso3TanMAgzVMeadIcbS__5x0o", "")
# você pode trocar o modelo apenas ajustando a env var no Render
GEMINI_MODEL        = os.environ.get("GEMINI_MODEL", "models/gemini-2.5-flash")

app = Flask(__name__)

# Anti-loop / deduplicação simples
PROCESSED_IDS = deque(maxlen=500)

# ====== Gemini (carregamento opcional) ======
gemini_model = None
if GEMINI_API_KEY:
    try:
        from google import generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)

        # tenta o modelo definido; se falhar, tenta alguns fallbacks seguros
        candidates = [GEMINI_MODEL, "models/gemini-1.5-flash", "models/gemini-1.5-pro"]
        for m in candidates:
            if not m:
                continue
            try:
                gemini_model = genai.GenerativeModel(m)
                app.logger.info(f"[GEMINI] Modelo carregado: {m}")
                break
            except Exception as e:
                app.logger.warning(f"[GEMINI] Falha ao carregar {m}: {e}")
        if not gemini_model:
            app.logger.error("[GEMINI] Nenhum modelo pôde ser carregado.")
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada; usando respostas simples.")


def answer_with_gemini(user_text: str) -> str:
    """
    Usa o Gemini se disponível; caso contrário, retorna uma resposta simples.
    """
    if not gemini_model:
        # Fallback seguro (sem depender de LLM)
        return f"Olá! Recebi sua mensagem: {user_text}"

    try:
        system_prompt = (
            "Você é um assistente da Vinnax Beauty. "
            "Cumprimente de forma simpática e pergunte o nome do cliente. "
            "Responda em português, de forma breve e profissional."
        )
        full_prompt = f"{system_prompt}\n\nCliente: {user_text}\nAtendente:"
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


@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    raw = request.get_json(silent=True) or {}

    # Alguns provedores mandam {"event":"...","data":{...}}; outros mandam direto
    envelope = raw.get("data", raw)

    # Se vier em lista, pega o primeiro item
    if isinstance(envelope, list) and envelope:
        envelope = envelope[0]
    if not isinstance(envelope, dict):
        return jsonify({"status": "bad_payload"}), 200

    key = envelope.get("key", {}) or {}

    # Ignora mensagens enviadas pelo próprio bot (ack/estado)
    if key.get("fromMe") is True:
        return jsonify({"status": "own_message_ignored"}), 200

    # Ignora eventos sem 'message' (acks/entregas etc.)
    message = envelope.get("message", {}) or {}
    if not message:
        return jsonify({"status": "no_message_ignored"}), 200

    # Deduplicação
    msg_id = key.get("id") or envelope.get("idMessage") or ""
    if msg_id:
        if msg_id in PROCESSED_IDS:
            return jsonify({"status": "duplicate_ignored"}), 200
        PROCESSED_IDS.append(msg_id)

    # Descobre número
    jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
    if not jid.endswith("@s.whatsapp.net"):
        return jsonify({"status": "non_user_ignored"}), 200
    number = jid.replace("@s.whatsapp.net", "")

    # Texto do cliente
    text = extract_text(message).strip()
    if not text:
        return jsonify({"status": "no_text_ignored"}), 200

    # Gera resposta (Gemini se disponível; senão, fallback)
    reply = answer_with_gemini(text)

    # Envio via Evolution API (somente se todas as envs estiverem setadas)
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
