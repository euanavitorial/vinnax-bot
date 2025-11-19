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
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "")
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
# FUNÇÃO DE RESPOSTA NATURAL COM GEMINI
# ============================================================

def answer_with_gemini(user_text: str, chat_history: List[str]) -> str:
    if not gemini_model:
        return "IA não configurada corretamente."
    try:
        history = "\n".join(chat_history)
        prompt = (
            "Você é o assistente da Vinnax Beauty. "
            "Fale de forma simpática e natural. "
            "Analise o texto e, se for uma solicitação de ação administrativa "
            "(como criar cliente, gerar orçamento, buscar produtos, etc.), "
            "resuma a intenção em um formato JSON no final da resposta no campo 'action', "
            "apenas se for necessário executar algo.\n\n"
            f"Histórico:\n{history}\nUsuário: {user_text}\nAssistente:"
        )
        response = gemini_model.generate_content(prompt)
        return response.candidates[0].content.parts[0].text.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro: {e}")
        return f"Erro interno: {e}"

# ============================================================
# INTEGRAÇÃO COM LOVABLE VIA EXTERNAL-AI-PROXY
# ============================================================

def call_lovable_proxy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Encaminha a intenção para o proxy seguro do Supabase."""
    try:
        headers = {
            "x-api-key": LOVABLE_API_KEY,
            "Content-Type": "application/json"
        }
        r = requests.post(EXTERNAL_AI_PROXY, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        app.logger.exception(f"[LOVABLE] Erro ao chamar external-ai-proxy: {e}")
        return {"status": "erro", "mensagem": str(e)}

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
# NOVO ENDPOINT: /api/ai (INTEGRAÇÃO SUPABASE)
# ============================================================

@app.route("/api/ai", methods=["POST"])
def api_ai():
    """Recebe requisições do Supabase, processa via Gemini e responde com texto natural."""
    try:
        # Valida API key
        api_key = request.headers.get("x-api-key")
        if api_key != LOVABLE_API_KEY:
            return jsonify({"error": "unauthorized"}), 401

        # Lê JSON corretamente
        if not request.is_json:
            return jsonify({"error": "expected JSON body"}), 400

        data = request.get_json(force=True)

        # Aceita múltiplos formatos de campo
        user_message = (
            data.get("messageText") or
            data.get("message") or
            data.get("text") or
            ""
        )
        phone = (
            data.get("phoneNumber") or
            data.get("phone") or
            data.get("number") or
            "default"
        )
        contact_name = data.get("contactName") or data.get("name") or "Cliente"
        instance = data.get("instance") or ""
        conversation_id = data.get("conversationId") or data.get("client_id")
        photo_url = data.get("photoUrl")

        if not user_message:
            return jsonify({"error": "Campo 'messageText' é obrigatório."}), 400

        # Recupera histórico
        history = CHAT_SESSIONS.get(phone, [])

        # Gera resposta com Gemini
        reply_text = answer_with_gemini(user_message, history)

        # Atualiza histórico local
        history.append(f"{contact_name}: {user_message}")
        history.append(f"Assistente: {reply_text}")
        CHAT_SESSIONS[phone] = history[-CHAT_HISTORY_LENGTH:]

        # Monta resposta
        response_json = {
            "ok": True,
            "conversationId": conversation_id,
            "phoneNumber": phone,
            "aiMessage": reply_text,
            "conversation": reply_text,
            "response": reply_text,
            "text": reply_text,
            "contactName": contact_name,
            "instance": instance,
            "photoUrl": photo_url,
        }

        return jsonify(response_json), 200

    except Exception as e:
        app.logger.exception(f"[API AI] Erro geral: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
