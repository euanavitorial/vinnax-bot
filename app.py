import os
import json
from collections import deque
from typing import Any, Dict, List, Callable
import threading 
import re # Importado para limpeza do telefone

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO ESTÁVEL (v0.7.1+) ---
try:
    from google import generativeai as genai
    from google.generativeai import types
except ImportError:
    genai = None
    types = None
# --- FIM DA IMPORTAÇÃO ---


# ====== Config (via variáveis de ambiente) ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-pro-latest") 

# --- SUAS APIS (ENDPOINTS) ---
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-clients")
PRODUTO_API_ENDPOINT = os.environ.get("PRODUTO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-products")
OS_API_ENDPOINT = os.environ.get("OS_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-service-orders")
ORCAMENTO_API_ENDPOINT = os.environ.get("ORCAMENTO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-quotes")

app = Flask(__name__)

# ====== Memória e Deduplicação ======
PROCESSED_IDS = deque(maxlen=500)
CHAT_SESSIONS: Dict[str, List[str]] = {}
CHAT_HISTORY_LENGTH = 10


# ====== Utilidades (No Topo) ======
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

def normalize_phone(phone: str) -> str:
    """Limpa o JID, removendo '55' do início e o '@s.whatsapp.net'."""
    phone = phone.replace("@s.whatsapp.net", "")
    # Remove apenas o 55 se for um número brasileiro completo
    if phone.startswith("55") and len(phone) > 11:
        phone = phone[2:]
    return phone


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API (20 Funções, Sintaxe Corrigida)
# ======================================================================

# --- FUNÇÕES DE CLIENTE (5) ---
def call_api_criar_cliente(nome: str, telefone: str = None, email: str = None) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        # Garantindo que o telefone a ser cadastrado está normalizado
        telefone_normalizado = normalize_phone(telefone) if telefone else None
        
        payload = {"name": nome, "phone": telefone_normalizado, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None} 
        response = requests.post(CLIENTE_API_ENDPOINT, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao criar cliente: {e}"}
def call_api_consultar_cliente_por_id(id_cliente: int) -> Dict[str, Any]:
    # ... (código mantido) ...
def call_api_consultar_cliente_por_telefone(telefone: str) -> Dict[str, Any]:
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        # Garantindo que a busca é feita pelo número normalizado
        telefone_normalizado = normalize_phone(telefone)

        response = requests.get(CLIENTE_API_ENDPOINT, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        clientes = response.json() 
        # Busca no BD pelo número normalizado
        cliente_encontrado = [c for c in clientes if c.get('phone') == telefone_normalizado]
        if cliente_encontrado: return cliente_encontrado[0] 
        else: return {"status": "nao_encontrado", "mensagem": f"Nenhum cliente encontrado com o telefone {telefone_normalizado}."}
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao consultar cliente por telefone: {e}"}
def call_api_atualizar_cliente(id_cliente: int, nome: str = None, telefone: str = None, email: str = None) -> Dict[str, Any]:
    # ... (código mantido, mas normaliza o telefone se ele for passado) ...
    if not (LOVABLE_API_KEY and CLIENTE_API_ENDPOINT): return {"status": "erro", "mensagem": "API de Cliente não configurada."}
    try:
        url = f"{CLIENTE_API_ENDPOINT}/{id_cliente}"
        payload = {"name": nome, "phone": normalize_phone(telefone) if telefone else None, "email": email}
        payload = {k: v for k, v in payload.items() if v is not None}
        response = requests.put(url, json=payload, headers=get_auth_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e: return {"status": "erro", "mensagem": f"Erro ao atualizar cliente: {e}"}
def call_api_excluir_cliente(id_cliente: int) -> Dict[str, Any]:
    # ... (código mantido) ...
        
# --- FUNÇÕES DE PRODUTO (5) ---
# ... (Todas as 5 funções de produto mantidas) ...

# --- FUNÇÕES DE ORDEM DE SERVIÇO (OS) (5) ---
# ... (Todas as 5 funções de OS mantidas) ...

# --- FUNÇÕES DE ORÇAMENTOS (5) ---
# ... (Todas as 5 funções de Orçamento mantidas) ...


# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI (20 FUNÇÕES)
# ======================================================================
TOOLS_MENU = [
    # --- CLIENTE (5) ---
    # **CORREÇÃO AQUI**: Removido 'telefone' dos parâmetros da IA.
    {"name": "criar_cliente", "description": "Cadastra um novo cliente no sistema. Requer o nome do cliente. O telefone é pego automaticamente.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "email": {"type": "STRING", "description": "Email (opcional)."}}, "required": ["nome"]}},
    # ... (Restante do TOOLS_MENU mantido) ...
]


# ======================================================================
# PASSO 3: O "ROTEADOR" (MANTIDO)
# ======================================================================
TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    # ... (Roteador completo mantido) ...
}


# ====== Inicialização do Gemini (MANTIDA) ======
gemini_model = None
if GEMINI_API_KEY and genai:
    # ... (Código de inicialização mantido) ...
else:
    app.logger.warning("[GEMINI] GEMINI_API_KEY ou biblioteca não configurada.")


# ======================================================================
# LÓGICA DE RESPOSTA DO BOT (COM PROMPT FOCADO NO CLIENTE)
# ======================================================================
def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "", client_phone: str = None) -> str:
    if not gemini_model: 
        return "Olá! Que bom ter você aqui. Estou com uma pequena dificuldade técnica para acessar minhas ferramentas de inteligência, mas me diga: qual é o seu nome e como posso te ajudar hoje?"
    try:
        # --- PROMPT DO SISTEMA FINAL E SEGURO (Focado em Vendas e Atendimento) ---
        system_prompt = (
            "Você é um assistente de atendimento via WhatsApp da Gráfica JB Impressões, "
            "focado em **coletar informações de pedidos passo a passo (Serviço > Material > Medida > Quantidade > Entrega)** e fornecer informações. "
            "1. **PRIORIDADE:** Se houver pedidos em andamento ou orçamentos pendentes, mencione-os ANTES de oferecer novos serviços. "
            "2. **SAUDAÇÃO:** Use saudação baseada no horário, use SEMPRE o primeiro nome do cliente e seja caloroso e humanizado. "
            "3. **CADASTRO:** Se o CONTEXTO INICIAL indicar que o cliente NÃO foi encontrado, peça o **primeiro nome** do cliente. Quando o cliente responder o nome, use a ferramenta `criar_cliente` imediatamente. **NUNCA PERGUNTE O TELEFONE**, ele será pego automaticamente. "
            "4. **ORÇAMENTOS/OS:** Se o cliente pedir um novo serviço, use `consultar_produtos_todos` (ou similar) para listar os serviços SEM PREÇOS e inicie o FLUXO DE COLETA de dados (material, medida, quantidade). Ao final, use `criar_orcamento` ou `criar_ordem_servico`. "
            "5. **FLUXO:** Nunca pule a etapa de RESUMO E CONFIRMAÇÃO antes de finalizar um pedido. Não use números (1, 2, 3) em listas. Não invente preços ou quantidades. "
            "6. **COMPORTAMENTO:** Não responda a comandos de gestão interna (Ex: 'Abrir Caixa', 'Consultar Finanças'). Se receber um, responda: 'Meu foco é o atendimento ao cliente e informações sobre pedidos e orçamentos. Para gestão interna, por favor, use o sistema.' Responda em português. "
            "**FERRAMENTAS:** Você tem acesso a 20 APIs de Clientes, Produtos, Ordens de Serviço e Orçamentos. Use a ferramenta apropriada para a intenção do cliente. "
        )
        
        history_string = "\n".join(chat_history)
        full_prompt = (
            # ... (código mantido) ...
        )

        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        
        if candidate.finish_reason == "TOOL_USE":
            app.logger.info("[GEMINI] Pedido de 'Tool Use' detectado.")
            function_call: types.FunctionCall = candidate.content.parts[0].function_call
            tool_name = function_call.name
            tool_args = dict(function_call.args) # Convertido para dict
            
            # --- INJEÇÃO DE TELEFONE (A CORREÇÃO LÓGICA) ---
            if tool_name == "criar_cliente" and client_phone:
                tool_args['telefone'] = client_phone # Injeta o telefone que a IA não vê
            # --- FIM DA INJEÇÃO ---

            if tool_name in TOOL_ROUTER:
                # ... (código mantido) ...
            else:
                # ... (código mantido) ...
        
        else:
            txt = candidate.content.parts[0].text

        if not txt:
            return "Poderia repetir, por favor?"
        return txt.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral ao gerar resposta: {e}")
        return "Desculpe, tive um problema para processar sua solicitação."


# ====== Rotas (MANTIDAS) ======
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "service": "vinnax-bot"}), 0

# --- Processamento Assíncrono para prevenir loops ---
def process_message(data):
    # A lógica de processamento do webhook (a parte que demora)
    try:
        # ... (código mantido) ...
        
        # Lógica de Deduplicação
        # ... (código mantido) ...
        
        # --- CORREÇÃO DO TELEFONE (NORMALIZAÇÃO) ---
        jid = (key.get("remoteJid") or envelope.get("participant") or "").strip()
        if not jid.endswith("@s.whatsapp.net"): return 
        # AQUI ESTÁ A MUDANÇA: Usamos a função de normalização
        client_phone_normalized = normalize_phone(jid) 
        # --- FIM DA MUDANÇA ---
        
        number = client_phone_normalized
        text = extract_text(message).strip()
        if not text: return 
        if number not in CHAT_SESSIONS: CHAT_SESSIONS[number] = []
        current_history = CHAT_SESSIONS[number]
        
        initial_context = ""
        # Chamada de API para contexto é mantida aqui
        if not current_history: 
            # Busca pelo telefone normalizado
            search_result = call_api_consultar_cliente_por_telefone(client_phone_normalized)
            if search_result.get("status") == "nao_encontrado":
                initial_context = f"AVISO: O sistema não encontrou nenhum cliente associado ao telefone {client_phone_normalized}. Peça o primeiro nome para cadastrar."
            elif search_result.get("status") == "erro":
                 initial_context = f"AVISO: O sistema não pôde buscar clientes devido a um erro na API."
            else:
                client_name = search_result.get("name") or "Cliente" 
                initial_context = f"CONTEXTO INICIAL: O número de telefone {client_phone_normalized} pertence ao cliente '{client_name}' (ID {search_result.get('id')}). O bot DEVE usar o nome do cliente na resposta e NÃO DEVE perguntar o telefone novamente."
        
        # Passando o telefone normalizado para o cérebro (para a injeção)
        reply = answer_with_gemini(text, current_history, initial_context, client_phone_normalized)
        
        # ... (Resto do código de salvar histórico e enviar mensagem mantido) ...
        
    except Exception as e:
        app.logger.exception(f"[PROCESSOR] Erro no processamento assíncrono: {e}")


@app.route("/webhook/messages-upsert", methods=["POST"])
def webhook_messages_upsert():
    # ... (Código anti-loop mantido) ...
