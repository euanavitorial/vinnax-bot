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
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")

EXTERNAL_AI_PROXY = f"{SUPABASE_URL}/functions/v1/external-ai-proxy"
GEMINI_MODEL_NAME = "models/gemini-2.5-flash"

app = Flask(__name__)

# ============================================================
# MEMÓRIA DE CONVERSA
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
            "Fale de forma simpática e natural. "
            "Se for uma solicitação administrativa (ex: criar cliente, orçamento, buscar produtos), "
            "resuma a intenção em um formato JSON no final da resposta no campo 'action'.\n\n"
            f"Histórico:\n{history}\nUsuário: {user_text}\nAssistente:"
        )
        response = gemini_model.generate_content(prompt)
        return response.candidates[0].content.parts[0].text.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro: {e}")
        return f"Erro interno: {e}"

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
# ENDPOINT PRINCIPAL — SEM AUTENTICAÇÃO PARA TESTE
# ============================================================

@app.route("/api/ai", methods=["POST"])
def api_ai():
    """Recebe requisições do Supabase, Evolution ou testes manuais via ReqBin"""
    try:
        data = request.get_json(force=True)
        app.logger.info(f"🔹 Requisição recebida: {data}")

        user_message = (
            data.get("messageText")
            or data.get("message")
            or data.get("text")
            or ""
        )
        phone = data.get("phoneNumber") or "desconhecido"
        contact_name = data.get("contactName") or "Cliente"

        if not user_message:
            return jsonify({"error": "Campo 'messageText' ou 'text' é obrigatório."}), 400

        history = CHAT_SESSIONS.get(phone, [])
        reply_text = answer_with_gemini(user_message, history)

        history.append(f"{contact_name}: {user_message}")
        history.append(f"IA: {reply_text}")
        CHAT_SESSIONS[phone] = history[-CHAT_HISTORY_LENGTH:]

        return jsonify({
            "ok": True,
            "aiMessage": reply_text,
            "conversationId": data.get("conversationId", ""),
            "phoneNumber": phone,
            "contactName": contact_name,
            "instance": data.get("instance", ""),
        }), 200

    except Exception as e:
        app.logger.exception(f"[API AI] Erro geral: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
