import os
import json
from collections import deque
from typing import Any, Dict, List, Callable

from flask import Flask, request, jsonify
import requests

# --- IMPORTAÇÃO ESTÁVEL ---
from google.generativeai import types 
# --- FIM DA IMPORTAÇÃO ---


# ====== Config (via variáveis de ambiente) ======
EVOLUTION_KEY = os.environ.get("EVOLUTION_KEY", "")
EVOLUTION_URL_BASE = os.environ.get("EVOLUTION_URL_BASE", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-1.5-pro-latest")

# --- SUAS APIS ---
LOVABLE_API_KEY = os.environ.get("LOVABLE_API_KEY", "") 
CLIENTE_API_ENDPOINT = os.environ.get("CLIENTE_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-clients")
PRODUTO_API_ENDPOINT = os.environ.get("PRODUTO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-products")
OS_API_ENDPOINT = os.environ.get("OS_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-service-orders")
ORCAMENTO_API_ENDPOINT = os.environ.get("ORCAMENTO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-quotes")
TRANSACAO_API_ENDPOINT = os.environ.get("TRANSACAO_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-transactions")
CAIXA_API_ENDPOINT = os.environ.get("CAIXA_API_ENDPOINT", "https://ebiitbpdvskreiuoeyaz.supabase.co/functions/v1/api-cash-register")


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


# ======================================================================
# PASSO 1: O "MENU" DE FERRAMENTAS PARA O GEMINI (29 FUNÇÕES TOTAIS)
# (BLOCO TOOLS_MENU MANTIDO IGUAL À VERSÃO ANTERIOR COMPLETA)
# ======================================================================
TOOLS_MENU = [
    # --- CLIENTE (5) ---
    {"name": "criar_cliente", "description": "Cadastra um novo cliente no sistema. Requer nome e pelo menos telefone ou email.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "telefone": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["nome"]}},
    {"name": "consultar_cliente_por_id", "description": "Busca os detalhes de um cliente (telefone, email, etc.) usando o ID do cliente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}}, "required": ["id_cliente"]}},
    {"name": "consultar_cliente_por_telefone", "description": "Busca os detalhes de um cliente usando o número de telefone. Use para verificar se um novo cliente já está cadastrado.", "parameters": {"type_": "OBJECT", "properties": {"telefone": {"type": "STRING"}}, "required": ["telefone"]}},
    {"name": "atualizar_cliente", "description": "Modifica informações de um cliente existente usando o ID do cliente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}, "nome": {"type": "STRING"}, "telefone": {"type": "STRING"}, "email": {"type": "STRING"}}, "required": ["id_cliente"]}},
    {"name": "excluir_cliente", "description": "Deleta permanentemente um cliente do sistema usando o ID do cliente.", "parameters": {"type_": "OBJECT", "properties": {"id_cliente": {"type": "INTEGER"}}, "required": ["id_cliente"]}},
    # --- PRODUTOS (5) ---
    {"name": "criar_produto", "description": "Cadastra um novo item no estoque de produtos. Requer nome, tipo e preço.", "parameters": {"type_": "OBJECT", "properties": {"nome": {"type": "STRING"}, "tipo": {"type": "STRING"}, "preco": {"type": "NUMBER"}}, "required": ["nome", "tipo", "preco"]}},
    {"name": "consultar_produto_por_id", "description": "Busca os detalhes de um produto (nome, preço, tipo) usando o ID do produto.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}}, "required": ["id_produto"]}},
    {"name": "consultar_produtos_todos", "description": "Lista todos os produtos cadastrados no estoque.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_produto", "description": "Modifica informações de um produto existente usando o ID. Use apenas os campos a serem alterados.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}, "nome": {"type": "STRING"}, "tipo": {"type": "STRING"}, "preco": {"type": "NUMBER"}}, "required": ["id_produto"]}},
    {"name": "excluir_produto", "description": "Deleta permanentemente um produto do estoque usando o ID do produto.", "parameters": {"type_": "OBJECT", "properties": {"id_produto": {"type": "INTEGER"}}, "required": ["id_produto"]}},
    # --- ORDEM DE SERVIÇO (OS) (5) ---
    {"name": "criar_ordem_servico", "description": "Cria uma nova Ordem de Serviço (OS) no sistema. Necessita do ID do cliente e ID do produto.", "parameters": {"type_": "OBJECT", "properties": {"client_id": {"type": "STRING"}, "product_id": {"type": "STRING"}, "description": {"type": "STRING"}, "total_price": {"type": "NUMBER"}, "deadline": {"type": "STRING", "description": "Formato YYYY-MM-DD"}}, "required": ["client_id", "product_id", "description", "total_price"]}},
    {"name": "consultar_ordem_servico_por_id", "description": "Busca os detalhes de uma Ordem de Serviço (OS) usando o ID da OS.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}}, "required": ["id_os"]}},
    {"name": "consultar_ordens_servico_todas", "description": "Lista todas as Ordens de Serviço (OS) cadastradas no sistema.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_ordem_servico", "description": "Modifica informações de uma Ordem de Serviço (OS) existente (ex: status, preço) usando o ID da OS.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}, "status": {"type": "STRING"}, "total_price": {"type": "NUMBER"}}, "required": ["id_os"]}},
    {"name": "excluir_ordem_servico", "description": "Deleta permanentemente uma Ordem de Serviço (OS) do sistema usando o ID da OS.", "parameters": {"type_": "OBJECT", "properties": {"id_os": {"type": "STRING"}}, "required": ["id_os"]}},
    # --- ORÇAMENTOS (5) ---
    {"name": "criar_orcamento", "description": "Cria um novo orçamento no sistema. Necessita do ID do cliente e ID do produto/serviço.", "parameters": {"type_": "OBJECT", "properties": {"client_id": {"type": "STRING"}, "product_id": {"type": "STRING"}, "description": {"type": "STRING"}}, "required": ["client_id", "product_id", "description"]}},
    {"name": "consultar_orcamento_por_id", "description": "Busca os detalhes de um orçamento (preço cotado, status) usando o ID do orçamento.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING", "description": "O ID (UUID) do Orçamento."}}, "required": ["id_orcamento"]}},
    {"name": "consultar_orcamentos_todos", "description": "Lista todos os orçamentos cadastrados no sistema.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_orcamento", "description": "Modifica o preço ou status de um orçamento existente. O preço cotado é o 'quoted_price'.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING"}, "quoted_price": {"type": "NUMBER"}, "status": {"type": "STRING"}}, "required": ["id_orcamento"]}},
    {"name": "excluir_orcamento", "description": "Deleta permanentemente um orçamento do sistema usando o ID.", "parameters": {"type_": "OBJECT", "properties": {"id_orcamento": {"type": "STRING", "description": "O ID (UUID) do orçamento a ser excluído."}}, "required": ["id_orcamento"]}},
    # --- TRANSAÇÕES FINANCEIRAS (5) ---
    {"name": "criar_transacao", "description": "Cria uma nova transação financeira (receita ou despesa) no sistema. 'Receivable' é a receber (Receita) e 'payable' é a pagar (Despesa).", "parameters": {"type_": "OBJECT", "properties": {"tipo_transacao": {"type": "STRING", "description": "Deve ser 'receivable' (receita) ou 'payable' (despesa)."}, "descricao": {"type": "STRING"}, "valor": {"type": "NUMBER"}, "due_date": {"type": "STRING", "description": "Data de vencimento/expectativa (formato YYYY-MM-DD)."}}, "required": ["tipo_transacao", "descricao", "valor"]}},
    {"name": "consultar_transacao_por_id", "description": "Busca os detalhes de uma transação financeira usando o ID.", "parameters": {"type_": "OBJECT", "properties": {"id_transacao": {"type": "STRING"}}, "required": ["id_transacao"]}},
    {"name": "consultar_transacoes_todas", "description": "Lista todas as transações financeiras cadastradas.", "parameters": {"type_": "OBJECT", "properties": {}}},
    {"name": "atualizar_transacao", "description": "Modifica o status, data de pagamento ou outros campos de uma transação existente.", "parameters": {"type_": "OBJECT", "properties": {"id_transacao": {"type": "STRING"}, "status": {"type": "STRING"}, "paid_date": {"type": "STRING", "description": "Formato YYYY-MM-DD"}}, "required": ["id_transacao"]}},
    {"name": "excluir_transacao", "description": "Deleta permanentemente uma transação financeira usando o ID.", "parameters": {"type_": "OBJECT", "properties": {"id_transacao": {"type": "STRING"}}, "required": ["id_transacao"]}},
    # --- CAIXA (REGISTRO) (4) ---
    {"name": "abrir_caixa", "description": "Abre um novo registro de caixa. Usado no início do turno.", "parameters": {"type_": "OBJECT", "properties": {"opened_by": {"type": "STRING"}, "opening_amount": {"type": "NUMBER"}}, "required": ["opened_by", "opening_amount"]}},
    {"name": "fechar_caixa", "description": "Fecha um registro de caixa existente ao final do turno.", "parameters": {"type_": "OBJECT", "properties": {"register_id": {"type": "STRING"}, "closing_amount": {"type": "NUMBER"}, "expected_amount": {"type": "NUMBER"}, "closed_by": {"type": "STRING"}}, "required": ["register_id", "closing_amount", "expected_amount", "closed_by"]}},
    {"name": "consultar_registro_caixa_por_id", "description": "Busca os detalhes de um registro de caixa específico (abertura, fechamento, diferença) usando o ID do registro.", "parameters": {"type_": "OBJECT", "properties": {"id_registro": {"type": "STRING"}}, "required": ["id_registro"]}},
    {"name": "consultar_registros_caixa_todos", "description": "Lista todos os registros de caixa (abertos e fechados).", "parameters": {"type_": "OBJECT", "properties": {}}},
]


# ======================================================================
# PASSO 2: AS FUNÇÕES REAIS DA API (Mantidas)
# ======================================================================
# (FUNÇÕES DE CHAMADA DE API MANTIDAS IGUAIS À VERSÃO ANTERIOR COMPLETA)

# FUNÇÕES OMITIDAS POR ESPAÇO, mas DEVERÃO ESTAR NO ARQUIVO FINAL:
# get_auth_headers
# call_api_criar_cliente, call_api_consultar_cliente_por_id, etc...
# call_api_criar_produto, etc...
# call_api_criar_os, etc...
# call_api_criar_orcamento, etc...
# call_api_criar_transacao, etc...
# call_api_abrir_caixa, call_api_fechar_caixa, etc...

# ======================================================================
# PASSO 3: O "ROTEADOR" (MAPA DE FERRAMENTAS)
# ======================================================================
TOOL_ROUTER: Dict[str, Callable[..., Dict[str, Any]]] = {
    # Cliente
    "criar_cliente": call_api_criar_cliente, "consultar_cliente_por_id": call_api_consultar_cliente_por_id,
    "consultar_cliente_por_telefone": call_api_consultar_cliente_por_telefone, "atualizar_cliente": call_api_atualizar_cliente,
    "excluir_cliente": call_api_excluir_cliente,
    # PRODUTO
    "criar_produto": call_api_criar_produto, "consultar_produto_por_id": call_api_consultar_produto_por_id,
    "consultar_produtos_todos": call_api_consultar_produtos_todos, "atualizar_produto": call_api_atualizar_produto,
    "excluir_produto": call_api_excluir_produto,
    # OS
    "criar_ordem_servico": call_api_criar_os, "consultar_ordem_servico_por_id": call_api_consultar_os_por_id,
    "consultar_ordens_servico_todas": call_api_consultar_ordens_servico_todas, "atualizar_ordem_servico": call_api_atualizar_os,
    "excluir_ordem_servico": call_api_excluir_os,
    # ORÇAMENTOS
    "criar_orcamento": call_api_criar_orcamento, "consultar_orcamento_por_id": call_api_consultar_orcamento_por_id,
    "consultar_orcamentos_todos": call_api_consultar_orcamentos_todos, "atualizar_orcamento": call_api_atualizar_orcamento,
    "excluir_orcamento": call_api_excluir_orcamento,
    # TRANSAÇÕES FINANCEIRAS
    "criar_transacao": call_api_criar_transacao, "consultar_transacao_por_id": call_api_consultar_transacao_por_id,
    "consultar_transacoes_todas": call_api_consultar_transacoes_todas, "atualizar_transacao": call_api_atualizar_transacao,
    "excluir_transacao": call_api_excluir_transacao,
    # CAIXA
    "abrir_caixa": call_api_abrir_caixa, "fechar_caixa": call_api_fechar_caixa,
    "consultar_registro_caixa_por_id": call_api_consultar_registro_caixa_por_id, "consultar_registros_caixa_todos": call_api_consultar_registros_caixa_todos,
}

# ====== Inicialização do Gemini (Mantida) ======
# (CÓDIGO DE INICIALIZAÇÃO DO GEMINI MANTIDO IGUAL)


# ======================================================================
# LÓGICA DE RESPOSTA DO BOT (COM NOVO SYSTEM PROMPT)
# ======================================================================
def answer_with_gemini(user_text: str, chat_history: List[str], initial_context: str = "") -> str:
    if not gemini_model: return f"Olá! Recebi sua mensagem: {user_text}"
    try:
        # --- PROMPT DO SISTEMA ATUALIZADO (A essência Lovable + 29 APIs) ---
        system_prompt = (
            "Você é um assistente de atendimento via WhatsApp da Gráfica JB Impressões. "
            "Seu foco é **coletar informações de pedidos passo a passo** (Serviço > Material > Medida > Quantidade > Entrega) e **gerenciar** o sistema da empresa. "
            "1. **PRIORIDADE:** Se houver pedidos em andamento ou orçamentos pendentes, mencione-os ANTES de oferecer novos serviços. "
            "2. **SAUDAÇÃO:** Use saudação baseada no horário, use SEMPRE o primeiro nome do cliente e seja caloroso. "
            "3. **CADASTRO:** Se o CONTEXTO INICIAL indicar que o cliente NÃO foi encontrado, e ele fornecer o nome, use `criar_cliente` imediatamente com o telefone do contexto. "
            "4. **ORÇAMENTOS/OS:** Se o cliente pedir um novo serviço, use `consultar_produtos_todos` (ou similar) para listar os serviços SEM PREÇOS e inicie o FLUXO DE COLETA de dados (material, medida, quantidade). Ao final, use `criar_orcamento` ou `criar_ordem_servico`. "
            "5. **FINANÇAS:** Se o cliente/usuário pedir para registrar um pagamento ou venda, use `criar_transacao` (tipo: 'receivable'). Se pedir para iniciar o dia, use `abrir_caixa`. "
            "6. **COMPORTAMENTO:** Nunca pule a etapa de RESUMO E CONFIRMAÇÃO antes de finalizar um pedido. Não use números (1, 2, 3) em listas. Não invente preços ou quantidades. Responda em português. "
            "**FERRAMENTAS:** Você tem acesso a 29 APIs para gerenciar Clientes, Produtos, Ordens de Serviço, Orçamentos e Transações Financeiras/Caixa. Use a ferramenta apropriada para a intenção do cliente. "
        )
        
        # ... (Resto da lógica answer_with_gemini MANTIDA IGUAL)
        
        # ... (CÓDIGO OMITIDO POR ESPAÇO)
        
        # RESTANTE DO CÓDIGO DA FUNÇÃO answer_with_gemini
        history_string = "\n".join(chat_history)
        full_prompt = (
            f"{system_prompt}\n\n"
            f"{initial_context}\n"
            f"=== Histórico da Conversa ===\n{history_string}\n"
            f"=== Nova Mensagem ===\nCliente: {user_text}\nAtendente:"
        )

        response = gemini_model.generate_content(full_prompt)
        candidate = response.candidates[0]
        
        if candidate.finish_reason == "TOOL_USE":
            app.logger.info("[GEMINI] Pedido de 'Tool Use' detectado.")
            function_call: types.FunctionCall = candidate.content.parts[0].function_call
            tool_name = function_call.name
            tool_args = function_call.args
            
            if tool_name in TOOL_ROUTER:
                app.logger.info(f"[ROUTER] Roteando para a função: '{tool_name}'")
                function_to_call = TOOL_ROUTER[tool_name]
                api_result = function_to_call(**dict(tool_args))
                tool_response_part = {"function_response": {"name": tool_name, "response": {"content": json.dumps(api_result)}}}
                response_final = gemini_model.generate_content([full_prompt, candidate.content, tool_response_part])
                txt = response_final.candidates[0].content.parts[0].text
            else:
                app.logger.warning(f"[ROUTER] Ferramenta '{tool_name}' não encontrada no roteador.")
                txt = "Desculpe, tentei usar uma ferramenta que não conheço."
        else:
            txt = candidate.content.parts[0].text

        if not txt:
            return "Poderia repetir, por favor?"
        return txt.strip()
    except Exception as e:
        app.logger.exception(f"[GEMINI] Erro geral ao gerar resposta: {e}")
        return "Desculpe, tive um problema para processar sua solicitação."

# ====== Rotas (Mantidas) ======
# (CÓDIGO DE ROTAS HOME E WEBHOOK MANTIDO IGUAL)
