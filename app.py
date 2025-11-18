import os
import json
from collections import deque
from typing import Any, Dict, List
import threading 
import re 
import sys 
from requests.exceptions import HTTPError 

from flask import Flask, request, jsonify
import requests
import google.generativeai as genai # Usando a biblioteca padrão compatível

app = Flask(__name__)

# ====== Configurações ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-pro-latest")

# Endpoints (Suas variáveis já configuradas)
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "")
PRODUTO_API_ENDPOINT = os.environ.get("PRODUTO_API_ENDPOINT", "")
OS_API_ENDPOINT = os.environ.get("OS_API_ENDPOINT", "")
ORCAMENTO_API_ENDPOINT = os.environ.get("ORCAMENTO_API_ENDPOINT", "")

# Memória local
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 15

# ====== Utilidades ======
def extract_text(message: Dict[str, Any]) -> str:
    if not isinstance(message, dict): return ""
    if "conversation" in message: return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message: return (message["extendedTextMessage"].get("text") or "").strip()
    for mid in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        if mid in message: return (message[mid].get("caption") or "").strip()
    return ""

def get_auth_headers():
    return {"x-api-key": LOVABLE_API_KEY, "Content-Type": "application/json"}

def normalize_phone(phone: str) -> str:
    """Normaliza número de telefone para busca no banco."""
    if not phone: return ""
    phone = str(phone).replace("@s.whatsapp.net", "").strip()
    if phone.startswith("55"): phone = phone[2:]
    if len(phone) >= 11 and phone[2] == "0": phone = phone[:2] + phone[3:]
    return phone

def log_api_error(e: Exception, function_name: str) -> Dict[str, Any]:
    error_msg = f"Erro em {function_name}: {str(e)}"
    print(f"[API_CALL] {error_msg}", file=sys.stderr)
    return {"status": "erro", "mensagem": "Houve um erro ao consultar o sistema."}

# ====== FUNÇÕES DE API (As Ferramentas) ======

def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    try:
        # Normaliza o telefone antes de enviar
        telefone_norm = normalize_phone(telefone) if telefone else None
        payload = {"name": nome, "phone": telefone_norm, "email": email}
        payload = {k: v for k, v in payload.items() if v}
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "criar_cliente")

def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    try:
        telefone_norm = normalize_phone(telefone)
        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        clientes = response.json()
        if isinstance(clientes, list):
            encontrado = next((c for c in clientes if c.get('phone') == telefone_norm), None)
            if encontrado: return encontrado
        return {"status": "nao_encontrado"}
    except Exception as e: return log_api_error(e, "consultar_cliente_por_telefone")

def call_api_consultar_produtos_todos() -> Dict[str, Any]:
    try:
        response = requests.get(PRODUTO_API_ENDPOINT, headers=get_auth_headers(), timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e: return log_api_error(e, "consultar_produtos_todos")

# ... (Adicione aqui as outras funções de OS e Orçamento se necessário, mas estas são as principais para o teste inicial)

# ====== Menu de Ferramentas ======
TOOLS_MENU = [
    {"name": "criar_cliente", "description": "Cadastra cliente. Requer nome. Telefone é injetado automaticamente.", "parameters": {"type": "OBJECT", "properties": {"nome": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["nome"]}},
    {"name": "consultar_cliente_por_telefone", "description": "Busca cliente pelo telefone.", "parameters": {"type": "OBJECT", "properties": {"telefone": {"type": "STRING"}}, "required": ["telefone"]}},
    {"name": "consultar_produtos_todos", "description": "Lista todos os produtos e serviços disponíveis.", "parameters": {"type": "OBJECT", "properties": {}}},
]

TOOL_ROUTER = {
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone,
    "consultar_produtos_todos": call_api_consultar_produtos_todos
}

# ====== Inicialização do Gemini ======
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(GEMINI_MODEL_NAME, tools=TOOLS_MENU)
        print("[SISTEMA] Gemini carregado com sucesso.", file=sys.stderr)
    except Exception as e:
        print(f"[SISTEMA] Erro ao carregar Gemini: {e}", file=sys.stderr)

# ====== Lógica Principal ======
def answer_with_gemini(user_text, chat_history, initial_context, client_phone):
    if not gemini_model: return "Desculpe, estou reiniciando meu cérebro. Tente em instantes."
    
    system_prompt = (
        "Você é o assistente da Gráfica JB Impressões. Seja cordial.\n"
        "1. Use o CONTEXTO INICIAL. Se o cliente não existe, peça o nome e use `criar_cliente`.\n"
        "2. Se pedir serviços, use `consultar_produtos_todos`.\n"
        "3. Responda em português."
    )
    
    history_str = "\n".join(chat_history)
    full_prompt = f"{system_prompt}\n\n{initial_context}\n\nHistórico:\n{history_str}\n\nCliente: {user_text}\nAtendente:"

    try:
        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        
        # Verificação segura de Tool Call (sem importar classes extras)
        part = candidate.content.parts[0]
        if part.function_call:
            fname = part.function_call.name
            args = dict(part.function_call.args)
            
            # Injeção de Telefone
            if fname == "criar_cliente" and client_phone:
                args["telefone"] = client_phone
            
            if fname in TOOL_ROUTER:
                print(f"[IA] Chamando ferramenta: {fname}", file=sys.stderr)
                result = TOOL_ROUTER[fname](**args)
                
                tool_response = {
                    "function_response": {
                        "name": fname,
                        "response": {"content": json.dumps(result)}
                    }
                }
                final_res = gemini_model.generate_content([full_prompt, part, tool_response])
                return final_res.text.strip()
            
        return part.text.strip()

    except Exception as e:
        print(f"[IA] Erro na geração: {e}", file=sys.stderr)
        return "Tive um problema técnico, desculpe."

# ====== Rotas ======
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

def process_message_thread(data):
    try:
        envelope = data.get("data", data)
        if isinstance(envelope, list): envelope = envelope[0]
        if not isinstance(envelope, dict): return
        
        key = envelope.get("key", {})
        if key.get("fromMe"): return
        
        msg_id = key.get("id")
        if msg_id in PROCESSED_IDS: return
        PROCESSED_IDS.append(msg_id)
        
        jid = key.get("remoteJid", "")
        client_phone = normalize_phone(jid)
        message = envelope.get("message", {})
        text = extract_text(message)
        
        if not text: return

        if jid not in CHAT_SESSIONS: CHAT_SESSIONS[jid] = []
        
        # Contexto Inicial
        initial_context = ""
        cliente = call_api_consultar_cliente_por_telefone(client_phone)
        if cliente.get("status") == "nao_encontrado":
            initial_context = f"AVISO: Cliente novo (Telefone {client_phone}). Peça o nome para cadastrar."
        elif "id" in cliente:
            initial_context = f"Cliente identificado: {cliente.get('name')}."

        reply = answer_with_gemini(text, CHAT_SESSIONS[jid], initial_context, client_phone)
        
        CHAT_SESSIONS[jid].append(f"Cliente: {text}")
        CHAT_SESSIONS[jid].append(f"Atendente: {reply}")
        
        if EVOLUTION_URL_BASE and EVOLUTION_KEY:
            requests.post(
                f"{EVOLUTION_URL_BASE}/message/sendtext/{EVOLUTION_INSTANCE}",
                headers={"apikey": EVOLUTION_KEY},
                json={"number": jid, "text": reply}
            )
            
    except Exception as e:
        print(f"[PROCESSOR] Erro: {e}", file=sys.stderr)

@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    threading.Thread(target=process_message_thread, args=(data,)).start()
    return jsonify({"status": "ack"}), 200
