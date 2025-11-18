import os
import json
from collections import deque
from typing import Any, Dict, List, Callable

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO PADRÃO E SEGURA ---
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
# ----------------------------------

# ====== Config (via variáveis de ambiente) ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-pro-latest")

LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-clients")
LANCAMENTO_API_ENDPOINT = os.environ.get("LANCAMENTO_API_ENDPOINT", "https://api.seusistema.com/v1/lancamentos")

app = Flask(__name__)

# ====== Memória e Deduplicação ======
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10


# ====== Utilidades WhatsApp ======
def extract_text(message: Dict[str, Any]) -> str:
    """Extrai o texto de diversos tipos de mensagem do WhatsApp."""
    if not isinstance(message, dict): return ""
    if "conversation" in message: return (message.get("conversation") or "").strip()
    if "extendedTextMessage" in message: return (message["extendedTextMessage"].get("text") or "").strip()
    for mid in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
        if mid in message: return (message[mid].get("caption") or "").strip()
    return ""

def get_auth_headers():
    """Retorna o cabeçalho de autenticação para as APIs do Lovable."""
    return {
        "x-api-key": LOVABLE_API_KEY,
        "Content-Type": "application/json"
    }


# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI
# ======================================================================
TOOLS_MENU = [
    {
        "name": "criar_cliente",
        "description": "Cadastra um novo cliente no sistema. Requer nome e pelo menos telefone ou email.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "nome": {"type": "STRING", "description": "Nome completo do cliente."},
                "telefone": {"type": "STRING", "description": "Número de telefone do cliente (opcional)."},
                "email": {"type": "STRING", "description": "Endereço de email do cliente (opcional)."}
            },
            "required": ["nome"]
        }
    },
    {
        "name": "consultar_cliente_por_id",
        "description": "Busca os detalhes de um cliente (telefone, email, etc.) usando o ID do cliente.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID (identificador) numérico do cliente."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "consultar_cliente_por_telefone",
        "description": "Busca os detalhes de um cliente usando o número de telefone. Use para verificar se um novo cliente já está cadastrado.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "telefone": {"type": "STRING", "description": "O número de telefone do cliente, incluindo o código do país (ex: 5511999999999)."}
            },
            "required": ["telefone"]
        }
    },
    {
        "name": "atualizar_cliente",
        "description": "Modifica informações de um cliente existente usando o ID do cliente. Use apenas os campos a serem alterados.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID (identificador) numérico do cliente a ser atualizado."},
                "nome": {"type": "STRING", "description": "Novo nome do cliente (opcional)."},
                "telefone": {"type": "STRING", "description": "Novo telefone do cliente (opcional)."},
                "email": {"type": "STRING", "description": "Novo email do cliente (opcional)."}
            },
            "required": ["id_cliente"]
        }
    },
    {
        "name": "excluir_cliente",
        "description": "Deleta permanentemente um cliente do sistema usando o ID do cliente.",
        "parameters": {
            "type_": "OBJECT",
            "properties": {
                "id_cliente": {"type": "INTEGER", "description": "O ID (identificador) numérico do cliente a ser excluído."}
            },
            "required": ["id_cliente"]
        }
    }
]


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API (ACIMA DO ROUTER)
# ======================================================================

# --- FUNÇÕES DE CLIENTE ---
def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None} 
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao criar cliente: {e}"}

def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar cliente: {e}"}

def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        clientes = response.json() 
        cliente_encontrado = [c for c in clientes if c.get('phone') == telefone]
        if cliente_encontrado:
            return cliente_encontrado[0] 
        else:
            return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente encontrado com o telefone {telefone}."}
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar cliente por telefone: {e}"}

def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao atualizar cliente: {e}"}

def call_api_excluir_cliente(id_cliente: int) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204: return {"status": "sucesso", "mensagem": f"Cliente ID {id_cliente} excluído com sucesso."}
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao excluir cliente: {e}"}


# ======================================================================
# PASSO 3: O "ROTEADOR" (MAPA DE FERRAMENTAS)
# ======================================================================
TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone,
    "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
}

# ====== Inicialização do Gemini (CORRIGIDA AQUI) ======
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        
        # CORREÇÃO: Definindo explicitamente as 4 categorias principais
        # para evitar erros com categorias novas ou não suportadas (Erro 400).
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        gemini_model = genai.GenerativeModel(
            GEMINI_MODEL_NAME,
            safety_settings=safety_settings,
            tools=TOOLS_MENU
        )
        app.logger.info(f"[GEMINI] Modelo carregado com {len(TOOLS_MENU)} ferramentas.")
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro ao inicializar SDK: {e}")
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY não configurada.")


# ======================================================================
# LÓGICA DE RESPOSTA DO BOT
# ======================================================================
def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "", client_phone: str = None) -> str:
    if not gemini_model:
        return f"Olá! Recebi sua mensagem: {user_text}"

    try:
        system_prompt = (
            "Você é um assistente da Vinnax Beauty. Seu objetivo é ser simpático, "
            "profissional e ajudar o cliente. Se for o início da conversa, "
            "cumprimente, se apresente e comece a conversa. Se o cliente já foi identificado no "
            "CONTEXTO INICIAL, use o nome dele. Responda em português, de forma breve. "
            "**FERRAMENTAS:** Use as ferramentas disponíveis (`tools`) sempre que o cliente solicitar uma ação (criar, consultar, excluir). "
            "Sempre confirme os dados críticos antes de executar uma ação."
        )
        
        history_string = "\n".join(chat_history)
        full_prompt = (
            f"{system_prompt}\n\n"
            f"{initial_context}\n"
            f"=== Histórico da Conversa ===\n{history_string}\n"
            f"=== Nova Mensagem ===\nCliente: {user_text}\nAtendente:"
        )

        # 1. Primeira chamada ao Gemini
        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        
        # 2. Verificar se o Gemini pediu para usar uma ferramenta
        part = candidate.content.parts[0]
        if hasattr(part, 'function_call') and part.function_call:
            app.logger.info("[GEMINI] Pedido de 'Tool Use' detectado.")
            
            function_call = part.function_call
            tool_name = function_call.name
            tool_args = dict(function_call.args)
            
            # 3. Usar o ROTEADOR
            if tool_name in TOOL_ROUTER:
                app.logger.info(f"[ROUTER] Roteando para a função: '{tool_name}'")
                
                function_to_call = TOOL_ROUTER[tool_name]
                api_result = function_to_call(**tool_args)
                
                # 4. Segunda chamada ao Gemini (com o resultado da API)
                tool_response_part = {
                    "function_response": {
                        "name": tool_name,
                        "response": {"content": json.dumps(api_result)}
                    }
                }

                response_final = gemini_model.generate_content(
                    [full_prompt, candidate.content, tool_response_part]
                )
                txt = response_final.candidates[0].content.parts[0].text
            
            else:
                app.logger.warning(f"[ROUTER] Ferramenta '{tool_name}' não encontrada no roteador.")
                txt = "Desculpe, tentei usar uma ferramenta que não conheço."
        
        else:
            txt = part.text

        if not txt:
            return "Poderia repetir, por favor?"
        return txt.strip()
        
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral ao gerar resposta: {e}")
        return "Desculpe, tive um problema para processar sua solicitação."

# ====== Rotas ======
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "vinnax-bot"}), 200

@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    raw = request.get_json(silent=True) or {}
    envelope = raw.get("data", raw)
    if isinstance(envelope, list) and envelope: envelope = envelope[0]
    if not isinstance(envelope, dict): return jsonify({"status": "bad_payload"}), 200
    key = envelope.get("key", {}) or {}
    if key.get("fromMe") is True: return jsonify({"status": "own_message_ignored"}), 200
    message = envelope.get("message", {}) or {}
    if not message: return jsonify({"status": "no_message_ignored"}), 200
    msg_id = key.get("id") or envelope.get("idMessage") or ""
    if msg_id:
        if msg_id in PROCESSED_IDS: return jsonify({"status": "duplicate_ignored"}), 200
        PROCESSED_IDS.append(msg_id)
    
    # 1. Extração do Telefone (JID)
    jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
    if not jid.endswith("@s.whatsapp.net"): return jsonify({"status": "non_user_ignored"}), 200
    client_phone = jid.replace("@s.whatsapp.net", "") 
    
    number = client_phone 
    text = extract_text(message).strip()
    if not text: return jsonify({"status": "no_text_ignored"}), 200
    if number not in CHAT_SESSIONS: CHAT_SESSIONS[number] = []
    current_history = CHAT_SESSIONS[number]
    
    # 2. Busca Automática de Cliente (Contexto Inicial)
    initial_context = ""
    if not current_history: 
        search_result = call_api_consultar_cliente_por_telefone(client_phone)
        
        if search_result.get("status") == "nao_encontrado":
            initial_context = f"AVISO: O sistema não encontrou nenhum cliente associado ao telefone {client_phone}. Peça o nome para cadastrar."
        elif search_result.get("status") == "erro":
             initial_context = f"AVISO: O sistema não pôde buscar clientes devido a um erro na API."
        else:
            client_name = search_result.get("name") or "Cliente" 
            initial_context = f"CONTEXTO INICIAL: O número de telefone {client_phone} pertence ao cliente '{client_name}' (ID {search_result.get('id')}). O bot DEVE usar o nome do cliente na resposta e NÃO DEVE perguntar o telefone novamente."
    
    # 3. Geração da Resposta
    reply = answer_with_gemini(text, current_history, initial_context, client_phone) 
    
    # 4. Salvamento da Memória
    CHAT_SESSIONS[number].append(f"Cliente: {text}")
    CHAT_SESSIONS[number].append(f"Atendente: {reply}")
    while len(CHAT_SESSIONS[number]) > CHAT_HISTORY_LENGTH:
        CHAT_SESSIONS[number].pop(0)

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
