import os
import json
from collections import deque
from typing import Any, Dict, List, Callable

from flask import Flask, request, jsonify
import requests
from google.generativeai.types import HarmCategory, HarmBlockThreshold
# --- CORREÇÃO DO BUG AQUI ---
from google.genai.types import FunctionCall
# --- FIM DA CORREÇÃO ---

# ... (O resto das configurações de Evolution e Gemini permanecem as mesmas)

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

# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI (AGORA COM BUSCA POR TELEFONE)
# ======================================================================
TOOLS_MENU = [
    # --- CLIENTE (5 FUNÇÕES AGORA) ---
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
        "description": "Busca os detalhes de um cliente usando o número de telefone fornecido pelo WhatsApp.",
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
    # ADICIONE AQUI AS FERRAMENTAS DE LANÇAMENTO FINANCEIRO, ESTOQUE, etc.
]


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API (AGORA COM BUSCA POR TELEFONE)
# ======================================================================
def get_auth_headers():
    """Retorna o cabeçalho de autenticação para as APIs do Lovable."""
    return {
        "x-api-key": LOVABLE_API_KEY,
        "Content-Type": "application/json"
    }

# --- FUNÇÕES DE CLIENTE ---

def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    """Ferramenta: Chama a API para criar um novo cliente (POST)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None} 
        
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao criar cliente: {e}"}

def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    """Ferramenta: Chama a API para buscar cliente por ID (GET)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.get(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao consultar cliente: {e}"}

def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    """Ferramenta: Chama a API para buscar cliente pelo número de telefone (GET)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        # A sua API LISTAR TODOS OS CLIENTES está em CLIENTE_API_ENDPOINT (sem /id)
        # Vamos buscar toda a lista e filtrar localmente, já que a API não oferece um filtro.
        # SE SUA API SUPORTAR FILTRO POR /api-clients?phone=... , USE O FILTRO DIRETO.
        
        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        
        # Assume-se que a API retorna uma lista de clientes
        clientes = response.json() 
        
        # Filtra a lista pelo telefone
        cliente_encontrado = [c for c in clientes if c.get('phone') == telefone]
        
        if cliente_encontrado:
            # Retorna apenas o primeiro cliente encontrado
            return cliente_encontrado[0] 
        else:
            return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente encontrado com o telefone {telefone}."}

    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao consultar cliente por telefone: {e}"}

def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    """Ferramenta: Chama a API para atualizar um cliente (PUT)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        payload = {"name": nome, "phone": telefone, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao atualizar cliente: {e}"}

def call_api_excluir_cliente(id_cliente: int) -> Dict[str, Any]:
    """Ferramenta: Chama a API para deletar um cliente (DELETE)."""
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT):
        return {"status": "erro", "mensagem": "API de Cliente não configurada."}

    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        response = requests.delete(url, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        if response.status_code == 204:
            return {"status": "sucesso", "mensagem": f"Cliente ID {id_cliente} excluído com sucesso."}
        return response.json()
    except Exception as e:
        return {"status": "erro", "mensagem": f"Erro ao excluir cliente: {e}"}


# ======================================================================
# PASSO 3: O "ROTEADOR" (MAPA DE FERRAMENTAS)
# ======================================================================
TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    # Cliente
    "criar_cliente": call_api_criar_cliente,
    "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone, # NOVO!
    "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
    # Lançamento Financeiro (Adicionar aqui)
    # Estoque (Adicionar aqui)
}

# ====== RESTO DO CÓDIGO (APENAS UM AJUSTE NO PROMPT) ======

# ... (código do Gemini e inicialização)

# MODIFICAÇÃO NO WEBHOOK: Pegar o telefone do JID para a busca automática
@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    # ... (código de extração de JID, número, etc.) ...
    
    # ... (código de extração de JID, número, etc.)
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
    
    # === AQUI PEGAMOS O TELEFONE DO REMETENTE ===
    jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
    if not jid.endswith("@s.whatsapp.net"): return jsonify({"status": "non_user_ignored"}), 200
    # O telefone do cliente (com código do país)
    client_phone = jid.replace("@s.whatsapp.net", "") 
    # ============================================
    
    number = client_phone # Usamos o client_phone como chave de sessão
    text = extract_text(message).strip()
    if not text: return jsonify({"status": "no_text_ignored"}), 200
    if number not in CHAT_SESSIONS: CHAT_SESSIONS[number] = []
    current_history = CHAT_SESSIONS[number]
    
    # === ADIÇÃO: BUSCA AUTOMÁTICA E INJEÇÃO DE INFORMAÇÃO NO PROMPT ===
    # O bot tenta encontrar o cliente APENAS se for o início da conversa
    # Ele não precisa usar a ferramenta, nós vamos injetar a informação
    
    initial_context = ""
    # Se o cliente não tem histórico de chat (é a primeira mensagem)
    if not current_history: 
        app.logger.info(f"Nova sessão. Buscando cliente por telefone: {client_phone}")
        # Chamamos a função de busca
        search_result = call_api_consultar_cliente_por_telefone(client_phone)
        
        if search_result.get("status") == "nao_encontrado":
            initial_context = f"AVISO: O sistema não encontrou nenhum cliente associado ao telefone {client_phone}."
        elif search_result.get("status") == "erro":
             initial_context = f"AVISO: O sistema não pôde buscar clientes devido a um erro na API."
        else:
            # Se o cliente foi encontrado, pegamos o nome para o bot
            client_name = search_result.get("name") or "Cliente" 
            initial_context = f"CONTEXTO INICIAL: O número de telefone {client_phone} pertence ao cliente '{client_name}'. O bot DEVE usar o nome do cliente na resposta e NÃO DEVE perguntar o telefone novamente."
    
    # A resposta do Gemini AGORA passa o contexto inicial
    reply = answer_with_gemini(text, current_history, initial_context)
    
    # ... (O restante da lógica de salvar o histórico e enviar a mensagem permanece a mesma)
    
    CHAT_SESSIONS[number].append(f"Cliente: {text}")
    CHAT_sessions[number].append(f"Atendente: {reply}")
    while len(CHAT_SESSIONS[number]) > CHAT_HISTORY_LENGTH:
        CHAT_SESSIONS[number].pop(0)

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

# MODIFICAÇÃO FINAL: A função answer_with_gemini precisa aceitar o novo contexto

def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "") -> str:
    # ... (Código da função, apenas o full_prompt muda)
    if not gemini_model: return f"Olá! Recebi sua mensagem: {user_text}"
    try:
        system_prompt = ("Você é um assistente da Vinnax Beauty. ... (O resto do prompt)")
        history_string = "\n".join(chat_history)
        
        # INCLUI O CONTEXTO INICIAL AQUI
        full_prompt = (
            f"{system_prompt}\n\n"
            f"{initial_context}\n" # <-- NOVO
            f"=== Histórico da Conversa ===\n{history_string}\n"
            f"=== Nova Mensagem ===\nCliente: {user_text}\nAtendente:"
        )
        # ... (Restante da lógica)
        
        # O restante do código da função é o mesmo, mas precisa ser copiado da versão anterior
        # para garantir que você tenha a versão completa no seu arquivo.
        
        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        # ... (O resto da lógica de function calling/roteamento é o mesmo)
        if candidate.finish_reason == "TOOL_USE":
            function_call: FunctionCall = candidate.content.parts[0].function_call
            tool_name = function_call.name
            tool_args = function_call.args
            if tool_name in TOOL_ROUTER:
                function_to_call = TOOL_ROUTER[tool_name]
                api_result = function_to_call(**dict(tool_args))
                tool_response_part = {"function_response": {"name": tool_name, "response": {"content": json.dumps(api_result)}}}
                response_final = gemini_model.generate_content([full_prompt, candidate.content, tool_response_part])
                txt = response_final.candidates[0].content.parts[0].text
            else: txt = "Desculpe, tentei usar uma ferramenta que não conheço."
        else: txt = candidate.content.parts[0].text
        if not txt: return "Poderia repetir, por favor?"
        return txt.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral ao gerar resposta: {e}")
        return "Desculpe, tive um problema para processar sua solicitação."
