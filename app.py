import os
import json
from collections import deque
from typing import Any, Dict, List
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

GEMINI_MODEL_NAME = "models/gemini-2.5-flash"

app = Flask(__name__)

# ============================================================
# MEMÓRIA DE CONVERSA E DEDUPLICAÇÃO
# ============================================================
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10

# ============================================================
# INICIALIZAÇÃO DO GEMINI
# ============================================================
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
        )
        app.logger.info(f"[GEMINI] Modelo carregado: {GEMINI_MODEL_NAME}")
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada.")

# ============================================================
# FUNÇÃO DE RESPOSTA COM GEMINI
# ============================================================

def answer_with_gemini(user_text: str, chat_history: List[str]) -> str:
    if not gemini_model:
        return "IA não configurada corretamente."
    try:
        history = "\n".join(chat_history)
        prompt = (
            "Você é o assistente da Vinnax Beauty. "
            "Responda de forma simpática, natural e breve. "
            "Se o cliente fizer uma pergunta sobre produtos, agendamentos ou orçamentos, responda com naturalidade. "
            "Se for algo administrativo, apenas resuma a intenção no final como JSON em 'action'.\n\n"
            f"Histórico:\n{history}\nUsuário: {user_text}\nAssistente:"
        )
        response = gemini_model.generate_content(prompt)
        return response.candidates[0].content.parts[0].text.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro: {e}")
        return f"Erro interno: {e}"

# ============================================================
# FUNÇÃO PARA ENVIAR MENSAGEM VIA EVOLUTION API
# ============================================================

def send_message_to_evolution(number: str, text: str) -> bool:
    try:
        url = f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}"
        headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
        body = {"number": number, "text": text}
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        if resp.ok:
            app.logger.info(f"[EVOLUTION] Mensagem enviada com sucesso para {number}")
            return True
        else:
            app.logger.error(f"[EVOLUTION] Falha ao enviar mensagem ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        app.logger.exception(f"[EVOLUTION] Erro ao enviar mensagem: {e}")
        return False

# ============================================================
# ROTAS BÁSICAS
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

# ============================================================
# ROTA PRINCIPAL - /api/ai (com deduplicação + envio via Evolution)
# ============================================================

@app.route("/api/ai", methods=["POST"])
def api_ai():
    try:
        data = request.get_json(force=True)
        app.logger.info(f"📩 Webhook recebido: {data}")

        # Extrai campos principais
        message_id = str(data.get("id") or data.get("messageId") or f"{data.get('phoneNumber')}-{data.get('timestamp', '')}")
        phone = data.get("phoneNumber")
        user_message = data.get("messageText") or data.get("text") or data.get("message") or ""
        contact_name = data.get("contactName", "Cliente")

        # Deduplicação
        if message_id in PROCESSED_IDS:
            app.logger.info(f"⚠️ Ignorando mensagem duplicada: {message_id}")
            return jsonify({"ok": True, "duplicate": True}), 200
        PROCESSED_IDS.append(message_id)

        # Validação básica
        if not phone or not user_message:
            return jsonify({"error": "Campos obrigatórios ausentes."}), 400

        # Recupera histórico
        history = CHAT_SESSIONS.get(phone, [])

        # Gera resposta com Gemini
        reply_text = answer_with_gemini(user_message, history)

        # Atualiza histórico
        history.append(f"{contact_name}: {user_message}")
        history.append(f"IA: {reply_text}")
        CHAT_SESSIONS[phone] = history[-CHAT_HISTORY_LENGTH:]

        # Envia mensagem pro cliente via Evolution API
        send_message_to_evolution(phone, reply_text)

        # Retorna log interno
        return jsonify({
            "ok": True,
            "messageId": message_id,
            "sent_to": phone,
            "reply": reply_text
        }), 200

    except Exception as e:
        app.logger.exception(f"[API AI] Erro geral: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
