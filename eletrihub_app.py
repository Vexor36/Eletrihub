"""
EletriHub - Aplicativo completo para eletricistas
Ferramentas de cálculo conforme NBR 5410 + gestão financeira básica.

Como rodar:
    pip install streamlit
    streamlit run eletrihub_app.py
"""

import streamlit as st
import plotly.express as px
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

# =============================================================================
# CONFIGURAÇÃO GERAL DA PÁGINA
# =============================================================================
st.set_page_config(
    page_title="EletriHub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# CSS customizado para deixar o app mais bonito
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.6rem;
        font-weight: 800;
        color: #f59e0b;
        margin-bottom: 0px;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #9ca3af;
        margin-top: 0px;
    }
    .resultado-box {
        padding: 1.2rem 1.5rem;
        border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.1);
    }
    .card-metrica {
        padding: 18px 20px;
        border-radius: 10px;
        margin-bottom: 8px;
    }
    .card-titulo {
        margin: 0;
        font-size: 14px;
        opacity: 0.85;
    }
    .card-valor {
        margin: 0;
        font-size: 30px;
        font-weight: 800;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# CONSTANTES TÉCNICAS (NBR 5410)
# =============================================================================

# Disjuntores comerciais padrão disponíveis no mercado (em Amperes)
DISJUNTORES_PADRAO = [10, 16, 20, 25, 32, 40, 50, 63, 70, 80]

# Fatores de agrupamento - Tabela 42 da NBR 5410
FATORES_AGRUPAMENTO = {
    1: 1.00,
    2: 0.80,
    3: 0.70,
    4: 0.65,
    5: 0.60,
    6: 0.57,
}

# Seções de cabo comercialmente disponíveis (mm²) para o Módulo 3
SECOES_CABO_DISPONIVEIS = [1.5, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0, 35.0, 50.0, 70.0, 95.0]

# Áreas internas de eletrodutos de PVC padrão (rígido/flexível) usados no Brasil
ELETRODUTOS_DATASET = {
    '1/2" (DN 20)': {"diametro_mm": 15, "area_mm2": 176.7},
    '3/4" (DN 25)': {"diametro_mm": 20, "area_mm2": 314.1},
    '1" (DN 32)': {"diametro_mm": 26, "area_mm2": 530.9},
    '1 1/4" (DN 40)': {"diametro_mm": 34, "area_mm2": 907.9},
}

# Áreas externas totais dos fios (cobre + isolação PVC 750V) - média entre marcas como Sil, Corfio, Cobrecom
FIOS_AREA_EXTERNA = {
    "1,5 mm²": 7.5,
    "2,5 mm²": 10.2,
    "4,0 mm²": 13.8,
    "6,0 mm²": 17.3,
    "10,0 mm²": 28.3,
}

# Banco de dados de equipamentos pré-definidos (potência média em Watts)
# Para os aparelhos de ar-condicionado, existem duas variações: Convencional e Inverter.
EQUIPAMENTOS = {
    "Ar Condicionado 9.000 BTU": {"Convencional": 800, "Inverter": 600},
    "Ar Condicionado 12.000 BTU": {"Convencional": 1100, "Inverter": 850},
    "Ar Condicionado 18.000 BTU": {"Convencional": 1600, "Inverter": 1300},
    "Ar Condicionado 24.000 BTU": {"Convencional": 2200, "Inverter": 1800},
    "Chuveiro Elétrico Comum": {"Padrão": 5500},
    "Chuveiro Elétrico Super": {"Padrão": 7500},
    "Micro-ondas": {"Padrão": 1400},
    "Air Fryer": {"Padrão": 1500},
    "Secador de Cabelo Profissional": {"Padrão": 2000},
    "Torneira Elétrica": {"Padrão": 5500},
    "Máquina de Lavar Roupa": {"Padrão": 1000},
    "Cooktop por Indução (4 bocas)": {"Padrão": 7000},
    "Forno Elétrico Embutido": {"Padrão": 2500},
    "Liquidificador": {"Padrão": 400},
    "Outro (Inserir manualmente)": {},
}


# =============================================================================
# FUNÇÕES DE CÁLCULO (NBR 5410)
# =============================================================================

def calcular_corrente_nominal(potencia_watts: float, tensao_volts: float) -> float:
    """Calcula a corrente nominal I = P / V. Protege contra divisão por zero."""
    if tensao_volts <= 0:
        return 0.0
    return potencia_watts / tensao_volts


def dimensionar_disjuntor(corrente: float):
    """
    Retorna o disjuntor comercial padrão imediatamente superior (ou igual)
    à corrente nominal calculada. Se a corrente ultrapassar a maior faixa
    padrão residencial (80A), retorna None.
    """
    for disjuntor in DISJUNTORES_PADRAO:
        if corrente <= disjuntor:
            return disjuntor
    return None


def dimensionar_cabo(corrente: float):
    """
    Dimensiona a seção mínima do cabo de cobre conforme NBR 5410,
    considerando instalação em eletroduto embutido em alvenaria
    (Método de instalação B1), 2 condutores carregados, isolação PVC.

    IMPORTANTE: embora a tabela de capacidade de condução permita 1,5 mm²
    até 11A, a própria NBR 5410 exige seção mínima de 2,5 mm² para circuitos
    de tomadas de uso geral/específico (força). Essa regra é aplicada aqui,
    portanto a seção mínima retornada nunca será inferior a 2,5 mm².
    """
    if corrente <= 15.5:
        return 2.5  # mínimo normativo para circuitos de tomadas/força
    elif corrente <= 21:
        return 4.0
    elif corrente <= 28:
        return 6.0
    elif corrente <= 40:
        return 10.0
    elif corrente <= 54:
        return 16.0
    elif corrente <= 75:
        return 25.0
    else:
        return None  # fora da faixa padrão coberta por esta calculadora


def calcular_queda_tensao(comprimento_m: float, corrente_a: float, secao_mm2: float, tensao_v: float) -> float:
    """
    Fórmula simplificada de queda de tensão percentual para condutores de cobre:
        ΔU% = (2 * L * I) / (56 * S * V) * 100
    Protege contra divisão por zero se seção ou tensão forem inválidas.
    """
    if secao_mm2 <= 0 or tensao_v <= 0:
        return 0.0
    return (2 * comprimento_m * corrente_a) / (56 * secao_mm2 * tensao_v) * 100


def limite_ocupacao_nbr5410(total_fios: int) -> float:
    """
    Retorna o limite máximo de taxa de ocupação (%) de um eletroduto conforme a NBR 5410,
    de acordo com a quantidade total de condutores internos:
      - 1 condutor  -> 53%
      - 2 condutores -> 31%
      - 3 ou mais    -> 40% (cenário mais comum em instalações residenciais/prediais)
    """
    if total_fios == 1:
        return 53.0
    elif total_fios == 2:
        return 31.0
    else:
        return 40.0


def calcular_taxa_ocupacao(area_total_fios_mm2: float, area_eletroduto_mm2: float) -> float:
    """
    Calcula a taxa de ocupação do eletroduto (%), isto é, a proporção entre a área
    externa total ocupada por todos os condutores (podendo ser de bitolas diferentes)
    e a área interna disponível do eletroduto. Protege contra divisão por zero.
    """
    if area_eletroduto_mm2 <= 0:
        return 0.0
    return (area_total_fios_mm2 / area_eletroduto_mm2) * 100


def formatar_brl(valor: float) -> str:
    """Formata um número no padrão monetário brasileiro (R$ 1.234,56)."""
    texto = f"{valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def dividir_receita(valor_bruto: float, profissional: str, km_rodados: float, regras: dict) -> dict:
    """
    Modelo Híbrido Visual: separa matematicamente o lucro puro de trabalho do
    reembolso de veículo (que pertence ao Gabriel), mantendo os dois valores
    isolados para fins contábeis — a junção dos dois só acontece na exibição.

    Ordem de cálculo:
      1. Custo do veículo = km_rodados * valor_por_km (0 se o veículo não foi usado).
      2. Saldo líquido comum = valor_bruto - custo_veiculo.
      3. Corte do Caixa da Empresa = saldo_liquido * (% caixa da regra aplicável).
      4. O restante (saldo_liquido - corte_caixa) é distribuído entre os sócios:
         - "Ambos": dividido exatamente 50%/50%.
         - Solo (Victor ou Gabriel): o parceiro ausente recebe um percentual do
           restante, e quem executou fica com o que sobrar.
    """
    custo_veiculo = round(km_rodados * regras["valor_por_km"], 2)
    saldo_liquido = valor_bruto - custo_veiculo

    caixa_percentual = regras["ambos_caixa"] if profissional == "Ambos" else regras["solo_caixa"]
    retido_caixa = saldo_liquido * caixa_percentual / 100
    restante = saldo_liquido - retido_caixa

    lucro_puro_victor = 0.0
    lucro_puro_gabriel = 0.0

    if profissional == "Ambos":
        lucro_puro_victor = restante / 2
        lucro_puro_gabriel = restante / 2
    elif profissional == "Victor":
        partner_cut = restante * regras["solo_parceiro"] / 100
        lucro_puro_gabriel = partner_cut
        lucro_puro_victor = restante - partner_cut
    elif profissional == "Gabriel":
        partner_cut = restante * regras["solo_parceiro"] / 100
        lucro_puro_victor = partner_cut
        lucro_puro_gabriel = restante - partner_cut

    return {
        "km_rodados": round(km_rodados, 1),
        "custo_veiculo": round(custo_veiculo, 2),
        "retido_caixa": round(retido_caixa, 2),
        "lucro_puro_victor": round(lucro_puro_victor, 2),
        "lucro_puro_gabriel": round(lucro_puro_gabriel, 2),
    }


def agrupar_valor_por_categoria(transacoes: list, tipo: str) -> dict:
    """Soma o valor bruto das transações de um dado tipo (Receita/Despesa), agrupado por categoria."""
    totais = {}
    for t in transacoes:
        if t["tipo"] == tipo:
            totais[t["categoria"]] = totais.get(t["categoria"], 0.0) + t["valor_bruto"]
    return totais


def montar_linhas_historico(transacoes: list) -> list:
    """Converte as transações (chaves internas em snake_case) em linhas com
    rótulos de coluna amigáveis, prontas para exibição em `st.dataframe`."""
    linhas = []
    for t in transacoes:
        linhas.append(
            {
                "Data": t["data"],
                "Descrição": t["descricao"],
                "Tipo": t["tipo"],
                "Categoria": t["categoria"],
                "Profissional": t["profissional"],
                "Valor Bruto (R$)": t["valor_bruto"],
                "KM Rodados": t["km_rodados"],
                "Reembolso Carro (R$)": t["custo_veiculo"],
                "Retido Caixa (R$)": t["retido_caixa"],
                "Lucro Puro Victor (R$)": t["lucro_puro_victor"],
                "Lucro Puro Gabriel (R$)": t["lucro_puro_gabriel"],
            }
        )
    return linhas


def renderizar_card_financeiro(emoji: str, titulo: str, valor: float, cor: str) -> None:
    """Renderiza um cartão colorido de métrica financeira (reutiliza o CSS .card-metrica)."""
    st.markdown(
        f"""
        <div class="card-metrica" style="background-color:{cor}1A;border-left:6px solid {cor};">
            <p class="card-titulo">{emoji} {titulo}</p>
            <p class="card-valor" style="color:{cor};">{formatar_brl(valor)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# PERSISTÊNCIA EM NUVEM (GOOGLE SHEETS) — MÓDULO 4
# =============================================================================
# O plano gratuito do Streamlit Cloud "hiberna" o app por inatividade e reinicia
# tudo do zero, apagando o que estiver só em st.session_state. Para as transações
# e as regras de divisão sobreviverem a isso, elas são lidas/gravadas numa planilha
# do Google. Se as credenciais não estiverem configuradas em st.secrets (ex: rodando
# local sem configurar nada), o app funciona normalmente, só que sem persistência.

NOME_PLANILHA_GOOGLE = "EletriHub_Financeiro"
ABA_TRANSACOES = "transacoes"
ABA_CONFIG = "config"

COLUNAS_TRANSACOES_SHEET = [
    "data", "descricao", "tipo", "categoria", "profissional",
    "valor_bruto", "km_rodados", "custo_veiculo", "retido_caixa",
    "lucro_puro_victor", "lucro_puro_gabriel",
]
COLUNAS_CONFIG_SHEET = ["valor_por_km", "ambos_caixa", "solo_caixa", "solo_parceiro"]


@st.cache_resource(show_spinner=False, ttl=60)
def _conectar_planilha_com_diagnostico():
    """
    Autentica no Google Sheets usando a conta de serviço configurada em
    st.secrets["gcp_service_account"]. Retorna uma tupla (planilha, erro):
      - Sucesso: (objeto Spreadsheet, None)
      - Sem credenciais ou falha de conexão: (None, "mensagem explicando o motivo")

    O resultado (incluindo a mensagem de erro) fica em cache por 60s. Isso é
    importante: como o Streamlit reexecuta o script inteiro a cada interação,
    uma variável comum seria resetada a cada rerun — por isso o erro precisa
    viajar junto dentro do próprio valor cacheado, e não numa variável solta.
    O TTL de 60s também garante que, se você corrigir as credenciais/planilha,
    o app tenta reconectar sozinho pouco depois, sem precisar de "Reboot app".
    """
    try:
        if "gcp_service_account" not in st.secrets:
            return None, "Nenhuma credencial encontrada em st.secrets['gcp_service_account']."
        escopos = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credenciais = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=escopos
        )
        cliente = gspread.authorize(credenciais)
        planilha = cliente.open(NOME_PLANILHA_GOOGLE)
        return planilha, None
    except Exception as e:
        # Cobre tanto a ausência total de um secrets.toml quanto falhas de
        # autenticação/conexão — em qualquer caso, cai no modo local sem nuvem.
        return None, f"{type(e).__name__}: {e}"


def _conectar_planilha():
    planilha, _ = _conectar_planilha_com_diagnostico()
    return planilha


def nuvem_disponivel() -> bool:
    """Indica se a persistência em nuvem está configurada e acessível."""
    return _conectar_planilha() is not None


def obter_ultimo_erro_nuvem():
    """Retorna a mensagem de erro da última tentativa de conexão com a nuvem (ou None se OK)."""
    _, erro = _conectar_planilha_com_diagnostico()
    return erro


def carregar_transacoes_da_nuvem():
    """Lê todas as transações da planilha. Retorna None se a nuvem não estiver disponível."""
    planilha = _conectar_planilha()
    if planilha is None:
        return None
    try:
        aba = planilha.worksheet(ABA_TRANSACOES)
        registros = aba.get_all_records()
        transacoes = []
        for r in registros:
            transacoes.append(
                {
                    "data": str(r["data"]),
                    "descricao": str(r["descricao"]),
                    "tipo": str(r["tipo"]),
                    "categoria": str(r["categoria"]),
                    "profissional": str(r["profissional"]),
                    "valor_bruto": float(r["valor_bruto"] or 0),
                    "km_rodados": float(r["km_rodados"] or 0),
                    "custo_veiculo": float(r["custo_veiculo"] or 0),
                    "retido_caixa": float(r["retido_caixa"] or 0),
                    "lucro_puro_victor": float(r["lucro_puro_victor"] or 0),
                    "lucro_puro_gabriel": float(r["lucro_puro_gabriel"] or 0),
                }
            )
        return transacoes
    except Exception as e:
        st.warning(f"⚠️ Não foi possível carregar as transações da nuvem: {e}")
        return None


def salvar_transacoes_na_nuvem(transacoes: list) -> bool:
    """Regrava a aba de transações por completo com a lista atual. Retorna True se salvou com sucesso."""
    planilha = _conectar_planilha()
    if planilha is None:
        return False
    try:
        aba = planilha.worksheet(ABA_TRANSACOES)
        aba.clear()
        linhas = [COLUNAS_TRANSACOES_SHEET] + [
            [t[coluna] for coluna in COLUNAS_TRANSACOES_SHEET] for t in transacoes
        ]
        aba.update(linhas)
        return True
    except Exception as e:
        st.warning(f"⚠️ Não foi possível salvar as transações na nuvem: {e}")
        return False


def carregar_config_da_nuvem():
    """Lê as regras de divisão da planilha. Retorna None se a nuvem não estiver disponível ou vazia."""
    planilha = _conectar_planilha()
    if planilha is None:
        return None
    try:
        aba = planilha.worksheet(ABA_CONFIG)
        registros = aba.get_all_records()
        if not registros:
            return None
        linha = registros[0]
        return {
            "valor_por_km": float(linha["valor_por_km"]),
            "ambos_caixa": float(linha["ambos_caixa"]),
            "solo_caixa": float(linha["solo_caixa"]),
            "solo_parceiro": float(linha["solo_parceiro"]),
        }
    except Exception:
        return None


def salvar_config_na_nuvem(regras: dict) -> bool:
    """Regrava a aba de configuração com as regras atuais. Retorna True se salvou com sucesso."""
    planilha = _conectar_planilha()
    if planilha is None:
        return False
    try:
        aba = planilha.worksheet(ABA_CONFIG)
        aba.clear()
        aba.update([COLUNAS_CONFIG_SHEET, [regras[c] for c in COLUNAS_CONFIG_SHEET]])
        return True
    except Exception as e:
        st.warning(f"⚠️ Não foi possível salvar a configuração na nuvem: {e}")
        return False


# =============================================================================
# CABEÇALHO
# =============================================================================
st.markdown('<p class="main-header">⚡ EletriHub</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="sub-header">Ferramentas profissionais para dimensionamento elétrico (NBR 5410) '
    'e gestão financeira do eletricista.</p>',
    unsafe_allow_html=True,
)
st.divider()

# Inicializa variáveis de sessão compartilhadas entre módulos
if "ultima_corrente" not in st.session_state:
    st.session_state.ultima_corrente = 10.0
if "ultima_secao" not in st.session_state:
    st.session_state.ultima_secao = 2.5
if "transacoes" not in st.session_state:
    transacoes_nuvem = carregar_transacoes_da_nuvem()
    st.session_state.transacoes = transacoes_nuvem if transacoes_nuvem is not None else []
if "eletroduto_circuitos" not in st.session_state:
    st.session_state.eletroduto_circuitos = []
if "confirmando_limpeza_financeira" not in st.session_state:
    st.session_state.confirmando_limpeza_financeira = False
if "finance_rules" not in st.session_state:
    config_nuvem = carregar_config_da_nuvem()
    st.session_state.finance_rules = config_nuvem if config_nuvem is not None else {
        "valor_por_km": 1.20,
        "ambos_caixa": 15.0,
        "solo_caixa": 10.0,
        "solo_parceiro": 10.0,
    }

# =============================================================================
# ABAS PRINCIPAIS
# =============================================================================
aba1, aba2, aba3, aba4 = st.tabs(
    [
        "🔌 Calculadora NBR 5410",
        "📊 Agrupamento e Ocupação de Eletrodutos",
        "📏 Queda de Tensão",
        "💰 Gestão Financeira & Sociedade",
    ]
)

# =============================================================================
# MÓDULO 1 - CALCULADORA NBR 5410 AVANÇADA (POR EQUIPAMENTO)
# =============================================================================
with aba1:
    st.subheader("Calculadora Avançada por Equipamento")
    st.caption("Selecione um eletrodoméstico para calcular automaticamente a corrente, o disjuntor e o cabo ideais.")

    col_entrada, col_resultado = st.columns([1, 1.2], gap="large")

    with col_entrada:
        with st.container(border=True):
            equipamento_escolhido = st.selectbox(
                "Eletrodoméstico",
                options=list(EQUIPAMENTOS.keys()),
                help="Escolha o equipamento que será instalado no circuito.",
            )

            variantes = EQUIPAMENTOS[equipamento_escolhido]

            # Caso o usuário escolha "Outro", abre campo manual de potência
            if equipamento_escolhido == "Outro (Inserir manualmente)":
                potencia_watts = st.number_input(
                    "Potência do equipamento (W)", min_value=1, value=1000, step=50
                )
            else:
                # Se houver mais de uma variante (ex: Convencional/Inverter), exibe seletor
                if len(variantes) > 1:
                    tipo_variante = st.radio(
                        "Tipo do equipamento", options=list(variantes.keys()), horizontal=True
                    )
                else:
                    tipo_variante = list(variantes.keys())[0]

                potencia_padrao = variantes[tipo_variante]

                # Potência pré-preenchida automaticamente, mas ainda editável pelo usuário
                potencia_watts = st.number_input(
                    "Potência média (W) — auto preenchida, ajuste se necessário",
                    min_value=1,
                    value=int(potencia_padrao),
                    step=50,
                )

            tensao_v = st.radio("Tensão do circuito", options=[127, 220], horizontal=True)

    with col_resultado:
        # --- Cálculos ---
        corrente = calcular_corrente_nominal(potencia_watts, tensao_v)
        disjuntor = dimensionar_disjuntor(corrente)
        secao_cabo = dimensionar_cabo(corrente)

        # Guarda os últimos valores calculados para reaproveitar nos outros módulos
        st.session_state.ultima_corrente = round(corrente, 2)
        if secao_cabo:
            st.session_state.ultima_secao = secao_cabo

        with st.container(border=True):
            st.markdown("##### Resultado do Dimensionamento")

            m1, m2, m3 = st.columns(3)
            m1.metric("Corrente Nominal", f"{corrente:.2f} A")
            m2.metric("Disjuntor Ideal", f"{disjuntor} A" if disjuntor else "Fora de faixa")
            m3.metric("Seção do Cabo", f"{secao_cabo:.1f} mm²" if secao_cabo else "Consultar projeto")

            if disjuntor and secao_cabo:
                st.success(
                    f"✅ Para o **{equipamento_escolhido}** ({potencia_watts} W / {tensao_v} V), "
                    f"a corrente nominal é de **{corrente:.2f} A**.\n\n"
                    f"- Disjuntor recomendado: **{disjuntor} A**\n"
                    f"- Seção mínima do cabo de cobre: **{secao_cabo:.1f} mm²** "
                    f"(Método B1, isolação PVC, 2 condutores carregados)\n\n"
                    "ℹ️ *Cálculo em conformidade com a NBR 5410, compatível com as principais marcas "
                    "homologadas do mercado nacional (Sil Fios, Corfio, Cobrecom, entre outras), "
                    "desde que os produtos sigam rigidamente a norma.*"
                )
            else:
                st.error(
                    "⚠️ A corrente calculada ultrapassa a faixa padrão coberta por esta calculadora "
                    "(disjuntores até 80A / cabos até 25mm²). Consulte um projeto elétrico dimensionado "
                    "individualmente para circuitos de maior porte."
                )

    st.info(
        "💡 Dica: os valores de corrente e seção de cabo calculados aqui são automaticamente "
        "sugeridos nos módulos **Agrupamento e Ocupação de Eletrodutos** e **Queda de Tensão**, "
        "na aba correspondente."
    )

# =============================================================================
# MÓDULO 2 - AGRUPAMENTO E OCUPAÇÃO DE ELETRODUTOS (TABELA 42 + OCUPAÇÃO FÍSICA)
# =============================================================================
with aba2:
    st.subheader("Agrupamento e Ocupação de Eletrodutos")
    st.caption(
        "Monte o eletroduto adicionando grupos de fios de bitolas diferentes (ex.: 1,5mm², "
        "2,5mm² e 4,0mm² convivendo no mesmo tubo) e acompanhe em tempo real a taxa de "
        "ocupação física e o fator de agrupamento conforme a NBR 5410."
    )

    col_config, col_analise = st.columns([1, 1.3], gap="large")

    with col_config:
        with st.container(border=True):
            st.markdown("##### Configuração do Eletroduto")

            eletroduto_escolhido = st.selectbox(
                "Selecione o Eletroduto Alvo", options=list(ELETRODUTOS_DATASET.keys())
            )

            with st.expander("➕ Adicionar Grupo de Fios ao Eletroduto", expanded=True):
                bitola_grupo = st.selectbox(
                    "Bitola do Fio", options=list(FIOS_AREA_EXTERNA.keys()), key="m2_bitola_grupo"
                )
                qtd_fios_grupo = st.number_input(
                    "Quantidade de Fios deste tamanho",
                    min_value=1,
                    max_value=30,
                    value=3,
                    step=1,
                    key="m2_qtd_fios_grupo",
                    help="Ex.: 3 fios para um circuito Fase + Neutro + Terra.",
                )
                qtd_circuitos_grupo = st.number_input(
                    "Quantos Circuitos esse grupo representa?",
                    min_value=1,
                    max_value=10,
                    value=1,
                    step=1,
                    key="m2_qtd_circuitos_grupo",
                    help="Usado para calcular o fator de agrupamento geral (Tabela 42 da NBR 5410).",
                )

                if st.button("➕ Adicionar ao Eletroduto", use_container_width=True):
                    area_fio_grupo = FIOS_AREA_EXTERNA[bitola_grupo]
                    st.session_state.eletroduto_circuitos.append(
                        {
                            "Bitola": bitola_grupo,
                            "Qtd Fios": int(qtd_fios_grupo),
                            "Circuitos": int(qtd_circuitos_grupo),
                            "Área Ocupada (mm²)": round(qtd_fios_grupo * area_fio_grupo, 2),
                        }
                    )
                    st.rerun()

            if st.button("🗑️ Limpar Eletroduto", use_container_width=True):
                st.session_state.eletroduto_circuitos = []
                st.rerun()

    with col_analise:
        with st.container(border=True):
            st.markdown("##### Fios Atualmente no Eletroduto")

            grupos = st.session_state.eletroduto_circuitos

            if grupos:
                tabela_md = "| Bitola | Qtd Fios | Circuitos | Área Ocupada |\n"
                tabela_md += "|---|---|---|---|\n"
                for grupo in grupos:
                    tabela_md += (
                        f"| {grupo['Bitola']} | {grupo['Qtd Fios']} | {grupo['Circuitos']} | "
                        f"{grupo['Área Ocupada (mm²)']:.1f} mm² |\n"
                    )
                st.markdown(tabela_md)
            else:
                st.info("Nenhum fio adicionado ainda.")

            # --- Cálculos agregados de todos os grupos ---
            total_fios = sum(grupo["Qtd Fios"] for grupo in grupos)
            total_circuitos = sum(grupo["Circuitos"] for grupo in grupos)
            area_total_fios = sum(grupo["Área Ocupada (mm²)"] for grupo in grupos)
            area_eletroduto = ELETRODUTOS_DATASET[eletroduto_escolhido]["area_mm2"]

            taxa_ocupacao = calcular_taxa_ocupacao(area_total_fios, area_eletroduto)
            limite_ocupacao = limite_ocupacao_nbr5410(total_fios) if total_fios > 0 else 53.0
            # O fator de agrupamento é limitado a 6 circuitos, conforme a tabela disponível
            fator_agrupamento = (
                FATORES_AGRUPAMENTO[min(total_circuitos, 6)] if total_circuitos > 0 else 1.00
            )

            st.divider()
            st.progress(min(taxa_ocupacao / 100, 1.0))

            if total_fios == 0:
                st.info("Adicione ao menos um grupo de fios para calcular a ocupação do eletroduto.")
            elif taxa_ocupacao <= limite_ocupacao:
                st.success("✅ Fiação aprovada! Os fios passam com segurança.")
            else:
                st.error(
                    "❌ ELETRODUTO SOBRECARREGADO! Ultrapassou o limite regulamentar da NBR 5410. "
                    "Use um eletroduto maior ou divida os circuitos."
                )

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Total Fios", f"{total_fios}")
            k2.metric("Ocupação Atual", f"{taxa_ocupacao:.1f}%")
            k3.metric("Limite Permitido", f"{limite_ocupacao:.0f}%")
            k4.metric("Fator Agrupamento", f"{fator_agrupamento:.2f}")

# =============================================================================
# MÓDULO 3 - QUEDA DE TENSÃO POR DISTÂNCIA
# =============================================================================
with aba3:
    st.subheader("Queda de Tensão por Distância")
    st.caption(
        "Verifica se a distância do circuito provocará perda excessiva de tensão, "
        "conforme fórmula simplificada para condutores de cobre."
    )

    col_entrada3, col_resultado3 = st.columns([1, 1.2], gap="large")

    with col_entrada3:
        with st.container(border=True):
            comprimento = st.number_input(
                "Comprimento do circuito (metros, ida)", min_value=0.0, value=15.0, step=1.0
            )
            tensao_queda = st.radio("Tensão", options=[127, 220], horizontal=True, key="tensao_modulo3")
            corrente_queda = st.number_input(
                "Corrente calculada (A)",
                min_value=0.0,
                value=float(st.session_state.ultima_corrente),
                step=0.5,
                help="Pré-preenchida com a última corrente calculada no Módulo 1.",
            )
            secao_escolhida = st.selectbox(
                "Bitola do cabo escolhida (mm²)",
                options=SECOES_CABO_DISPONIVEIS,
                index=SECOES_CABO_DISPONIVEIS.index(st.session_state.ultima_secao)
                if st.session_state.ultima_secao in SECOES_CABO_DISPONIVEIS
                else 1,
            )
            limite_percentual = st.number_input(
                "Limite de queda de tensão admissível (%)", min_value=0.1, value=4.0, step=0.5
            )

    with col_resultado3:
        with st.container(border=True):
            st.markdown("##### Resultado da Queda de Tensão")

            queda_percentual = calcular_queda_tensao(
                comprimento, corrente_queda, secao_escolhida, tensao_queda
            )

            q1, q2 = st.columns(2)
            q1.metric("Queda de Tensão Calculada", f"{queda_percentual:.2f} %")
            q2.metric("Limite Admissível", f"{limite_percentual:.2f} %")

            if comprimento <= 0 or corrente_queda <= 0:
                st.warning("⚠️ Informe comprimento e corrente válidos (maiores que zero) para calcular.")
            elif queda_percentual > limite_percentual:
                st.error(
                    f"🚫 A queda de tensão de **{queda_percentual:.2f}%** ultrapassa o limite admissível de "
                    f"**{limite_percentual:.2f}%**.\n\n"
                    "Sugestão: aumente a bitola do cabo (próxima seção comercial disponível) "
                    "ou reduza a distância do circuito."
                )
            else:
                st.success(
                    f"✅ A queda de tensão de **{queda_percentual:.2f}%** está dentro do limite admissível "
                    f"de **{limite_percentual:.2f}%**. A bitola de **{secao_escolhida} mm²** é adequada "
                    "para essa distância."
                )

# =============================================================================
# MÓDULO 4 - GESTÃO FINANCEIRA & SOCIEDADE
# =============================================================================
with aba4:
    st.subheader("Gestão Financeira & Sociedade")
    st.caption(
        "Controle de receitas e despesas com reembolso de veículo por KM rodado (a favor do "
        "Gabriel, dono do carro) e divisão automática do lucro entre os sócios."
    )

    sub_painel, sub_lancar, sub_config = st.tabs(
        ["📊 Painel & Gráficos", "💸 Lançar Transação", "⚙️ Configurar Divisão"]
    )

    # -------------------------------------------------------------------
    # SUB-ABA: CONFIGURAR DIVISÃO
    # -------------------------------------------------------------------
    with sub_config:
        conectado_agora = nuvem_disponivel()
        if conectado_agora:
            st.caption("☁️ Persistência em nuvem ativa — os dados sobrevivem a reinícios do app.")
        else:
            st.caption(
                "💻 Modo local (sem nuvem configurada) — os dados são perdidos se o app reiniciar. "
                "Configure a integração com Google Sheets para persistência real."
            )

        with st.expander("🔍 Diagnóstico da conexão com a nuvem"):
            if conectado_agora:
                st.success(f"Conectado com sucesso à planilha **{NOME_PLANILHA_GOOGLE}**.")
            else:
                st.error("Não foi possível conectar à planilha.")
                st.code(obter_ultimo_erro_nuvem() or "Nenhum detalhe de erro disponível.")
                st.caption(
                    "Erros comuns: nome da planilha diferente de "
                    f"'{NOME_PLANILHA_GOOGLE}', planilha não compartilhada com o e-mail da "
                    "conta de serviço (como Editor), APIs do Google Sheets/Drive não ativadas, "
                    "ou o TOML colado em Secrets com formatação inválida."
                )

        st.markdown("##### Reembolso de Veículo (KM Rodado)")
        st.caption(
            "O veículo pertence ao Gabriel — este valor é pago a ele integralmente, "
            "antes de qualquer divisão de lucro, sempre que o carro for usado num serviço."
        )

        regras = st.session_state.finance_rules

        with st.container(border=True):
            regras["valor_por_km"] = st.number_input(
                "Valor de Reembolso por KM Rodado (R$)",
                min_value=0.0,
                value=float(regras["valor_por_km"]),
                step=0.05,
                format="%.2f",
                key="cfg_valor_por_km",
                help="Ajuste conforme o preço do combustível e o desgaste do veículo (ex.: R$ 1,00, R$ 1,20, R$ 1,50).",
            )

        st.markdown("##### Percentuais da Divisão do Lucro")
        st.caption("Qualquer alteração aqui atualiza imediatamente as regras usadas ao lançar novas receitas.")

        col_ambos, col_solo = st.columns(2, gap="large")

        with col_ambos:
            with st.container(border=True):
                st.markdown("**Quando Ambos trabalham juntos**")
                regras["ambos_caixa"] = st.number_input(
                    "Caixa da Empresa (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(regras["ambos_caixa"]),
                    step=0.5,
                    key="cfg_ambos_caixa",
                )
                restante_ambos = 100 - regras["ambos_caixa"]
                if restante_ambos < 0:
                    st.error("⚠️ O percentual ultrapassa 100%! Ajuste o valor.")
                else:
                    st.caption(f"Restante dividido 50%/50%: **{restante_ambos / 2:.1f}%** para cada um.")

        with col_solo:
            with st.container(border=True):
                st.markdown("**Quando um profissional trabalha sozinho**")
                regras["solo_caixa"] = st.number_input(
                    "Caixa da Empresa (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(regras["solo_caixa"]),
                    step=0.5,
                    key="cfg_solo_caixa",
                )
                regras["solo_parceiro"] = st.number_input(
                    "Parceiro Ausente (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(regras["solo_parceiro"]),
                    step=0.5,
                    key="cfg_solo_parceiro",
                )
                restante_solo = 100 - regras["solo_caixa"] - regras["solo_parceiro"]
                if restante_solo < 0:
                    st.error("⚠️ A soma dos percentuais ultrapassa 100%! Ajuste os valores.")
                else:
                    st.caption(f"Restante para quem executou sozinho: **{restante_solo:.1f}%**.")

        st.session_state.finance_rules = regras

        if st.button("💾 Salvar Configurações na Nuvem", use_container_width=True):
            if salvar_config_na_nuvem(regras):
                st.success("✅ Configurações salvas na nuvem com sucesso!")
            else:
                st.warning(
                    "⚠️ Não foi possível salvar na nuvem (integração não configurada ou indisponível). "
                    "As regras continuam valendo normalmente nesta sessão."
                )

        st.divider()
        st.markdown("##### Como funciona a divisão (Modelo Híbrido Visual)")
        st.info(
            f"- **1. Reembolso do veículo:** se o carro do Gabriel for usado, ele recebe "
            f"**R$ {regras['valor_por_km']:.2f}** por KM rodado (ida + volta) — separado do lucro, "
            f"descontado do valor bruto antes de qualquer divisão.\n\n"
            f"- **2. Corte do Caixa:** sobre o saldo líquido (valor bruto − reembolso do veículo), "
            f"o Caixa da Empresa retém {regras['ambos_caixa']:.1f}% (Ambos) ou {regras['solo_caixa']:.1f}% "
            f"(Solo).\n\n"
            f"- **3. Ambos trabalham:** o que sobra após o Caixa é dividido meio a meio entre "
            f"Victor e Gabriel (lucro puro de trabalho).\n\n"
            f"- **3. Somente Victor executa:** do que sobra após o Caixa, {regras['solo_parceiro']:.1f}% "
            f"vai para o Gabriel (parceiro ausente) e o restante fica com o Victor.\n\n"
            f"- **3. Somente Gabriel executa:** do que sobra após o Caixa, {regras['solo_parceiro']:.1f}% "
            f"vai para o Victor (parceiro ausente) e o restante fica com o Gabriel.\n\n"
            f"💡 *O reembolso do veículo e o lucro puro do Gabriel são contabilizados separadamente, "
            f"mas exibidos somados no Painel para facilitar a conferência do valor total a transferir.*"
        )

    # -------------------------------------------------------------------
    # SUB-ABA: LANÇAR TRANSAÇÃO
    # -------------------------------------------------------------------
    with sub_lancar:
        st.markdown("##### Nova Transação")

        tipo = st.radio("Tipo", options=["Receita", "Despesa"], horizontal=True, key="fin_tipo")

        if tipo == "Receita":
            profissional = st.radio(
                "Quem executou o serviço?",
                options=["Gabriel", "Victor", "Ambos"],
                horizontal=True,
                key="fin_profissional",
            )
            veiculo_usado = st.radio(
                "O veículo do Gabriel foi utilizado para este serviço?",
                options=["Não", "Sim"],
                horizontal=True,
                key="fin_veiculo_usado",
            )
            if veiculo_usado == "Sim":
                km_rodados = st.number_input(
                    "Quilômetros Rodados (Ida + Volta)",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    key="fin_km_rodados",
                )
            else:
                km_rodados = 0.0
        else:
            profissional = "N/A"
            km_rodados = 0.0
            st.caption("ℹ️ Despesas não possuem divisão por profissional — afetam apenas o Caixa da Empresa.")

        with st.container(border=True):
            with st.form("form_transacao_financeira", clear_on_submit=True):
                fc1, fc2 = st.columns(2)
                with fc1:
                    descricao = st.text_input(
                        "Descrição", placeholder="Ex: Instalação de quadro elétrico"
                    )
                    valor = st.number_input("Valor (R$)", min_value=0.0, value=0.0, step=10.0)
                with fc2:
                    categoria = st.selectbox(
                        "Categoria",
                        options=[
                            "Infraestrutura",
                            "Manutenção",
                            "Automação",
                            "Ferramentas",
                            "Combustível",
                            "Outros",
                        ],
                    )
                    data_transacao = st.date_input("Data", value=date.today())

                enviado = st.form_submit_button("💾 Salvar Transação", use_container_width=True)

                if enviado:
                    if not descricao.strip():
                        st.warning("⚠️ Informe uma descrição para a transação.")
                    elif valor <= 0:
                        st.warning("⚠️ Informe um valor maior que zero.")
                    else:
                        if tipo == "Receita":
                            divisao = dividir_receita(
                                float(valor), profissional, float(km_rodados), st.session_state.finance_rules
                            )
                        else:
                            divisao = {
                                "km_rodados": 0.0,
                                "custo_veiculo": 0.0,
                                "retido_caixa": 0.0,
                                "lucro_puro_victor": 0.0,
                                "lucro_puro_gabriel": 0.0,
                            }

                        st.session_state.transacoes.append(
                            {
                                "data": data_transacao.strftime("%d/%m/%Y"),
                                "descricao": descricao.strip(),
                                "tipo": tipo,
                                "categoria": categoria,
                                "profissional": profissional,
                                "valor_bruto": float(valor),
                                **divisao,
                            }
                        )
                        salvar_transacoes_na_nuvem(st.session_state.transacoes)
                        st.success("✅ Transação registrada com sucesso!")
                        if tipo == "Receita":
                            total_gabriel = divisao["lucro_puro_gabriel"] + divisao["custo_veiculo"]
                            st.markdown(
                                f"**Divisão aplicada:** Reembolso Carro {formatar_brl(divisao['custo_veiculo'])} · "
                                f"Retido Caixa {formatar_brl(divisao['retido_caixa'])} · "
                                f"Lucro Puro Victor {formatar_brl(divisao['lucro_puro_victor'])} · "
                                f"Lucro Puro Gabriel {formatar_brl(divisao['lucro_puro_gabriel'])} "
                                f"→ **Total a transferir ao Gabriel: {formatar_brl(total_gabriel)}**"
                            )

    # -------------------------------------------------------------------
    # SUB-ABA: PAINEL & GRÁFICOS
    # -------------------------------------------------------------------
    with sub_painel:
        # Exibe (uma única vez) o resultado da última sincronização manual, se houver.
        # Precisa ficar guardado no session_state porque o st.rerun() logo abaixo
        # reinicia o script antes que uma mensagem criada na mesma rodada apareça na tela.
        if "msg_sincronizacao" in st.session_state:
            tipo_msg, texto_msg = st.session_state.pop("msg_sincronizacao")
            getattr(st, tipo_msg)(texto_msg)

        col_status_nuvem, col_botao_sync = st.columns([3, 1])
        with col_status_nuvem:
            if nuvem_disponivel():
                st.caption("☁️ Persistência em nuvem ativa.")
            else:
                st.caption("💻 Modo local (sem nuvem configurada).")
        with col_botao_sync:
            if st.button("🔄 Sincronizar", use_container_width=True):
                dados_nuvem_atualizados = carregar_transacoes_da_nuvem()
                config_nuvem_atualizada = carregar_config_da_nuvem()
                if dados_nuvem_atualizados is not None:
                    st.session_state.transacoes = dados_nuvem_atualizados
                if config_nuvem_atualizada is not None:
                    st.session_state.finance_rules = config_nuvem_atualizada
                if dados_nuvem_atualizados is not None or config_nuvem_atualizada is not None:
                    st.session_state.msg_sincronizacao = ("success", "✅ Dados atualizados a partir da nuvem!")
                else:
                    st.session_state.msg_sincronizacao = ("warning", "⚠️ Nuvem não disponível — nada para sincronizar.")
                st.rerun()

        transacoes = st.session_state.transacoes

        faturamento_bruto = sum(t["valor_bruto"] for t in transacoes if t["tipo"] == "Receita")
        despesas_totais = sum(t["valor_bruto"] for t in transacoes if t["tipo"] == "Despesa")

        caixa_empresa = (
            sum(t["retido_caixa"] for t in transacoes if t["tipo"] == "Receita") - despesas_totais
        )
        lucro_puro_victor_total = sum(t["lucro_puro_victor"] for t in transacoes if t["tipo"] == "Receita")
        lucro_puro_gabriel_total = sum(t["lucro_puro_gabriel"] for t in transacoes if t["tipo"] == "Receita")
        reembolso_carro_total = sum(t["custo_veiculo"] for t in transacoes if t["tipo"] == "Receita")

        # --- Linha 1: os 4 cartões estritamente separados (contabilidade real) ---
        st.markdown("##### Visão Geral das Carteiras")
        cw1, cw2, cw3, cw4 = st.columns(4)
        with cw1:
            renderizar_card_financeiro("🏦", "Caixa da Empresa", caixa_empresa, "#3b82f6")
        with cw2:
            renderizar_card_financeiro("🧑", "Lucro Puro Victor", lucro_puro_victor_total, "#06b6d4")
        with cw3:
            renderizar_card_financeiro("🧑", "Lucro Puro Gabriel", lucro_puro_gabriel_total, "#a855f7")
        with cw4:
            renderizar_card_financeiro("🚗", "Reembolso do Carro", reembolso_carro_total, "#f59e0b")

        st.divider()

        if not transacoes:
            st.info("Nenhuma transação registrada ainda. Utilize a aba **Lançar Transação** para começar.")
        else:
            st.markdown("##### Entradas vs. Saídas")
            fig_bar = px.bar(
                x=["Receitas", "Despesas"],
                y=[faturamento_bruto, despesas_totais],
                color=["Receitas", "Despesas"],
                color_discrete_map={"Receitas": "#22c55e", "Despesas": "#ef4444"},
                labels={"x": "", "y": "Valor (R$)", "color": ""},
                text_auto=".2s",
                template="plotly_dark",
            )
            fig_bar.update_layout(showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_bar, use_container_width=True)

            col_pie1, col_pie2 = st.columns(2, gap="large")

            with col_pie1:
                st.markdown("###### Origem das Receitas por Categoria")
                receitas_por_categoria = agrupar_valor_por_categoria(transacoes, "Receita")
                if receitas_por_categoria:
                    fig_pie1 = px.pie(
                        names=list(receitas_por_categoria.keys()),
                        values=list(receitas_por_categoria.values()),
                        template="plotly_dark",
                        hole=0.35,
                    )
                    fig_pie1.update_layout(paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_pie1, use_container_width=True)
                else:
                    st.info("Nenhuma receita registrada ainda.")

            with col_pie2:
                st.markdown("###### Distribuição de Despesas por Categoria")
                despesas_por_categoria = agrupar_valor_por_categoria(transacoes, "Despesa")
                if despesas_por_categoria:
                    fig_pie2 = px.pie(
                        names=list(despesas_por_categoria.keys()),
                        values=list(despesas_por_categoria.values()),
                        template="plotly_dark",
                        hole=0.35,
                    )
                    fig_pie2.update_layout(paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_pie2, use_container_width=True)
                else:
                    st.info("Nenhuma despesa registrada ainda.")

            st.divider()
            st.markdown("##### Histórico Completo de Transações")
            st.dataframe(montar_linhas_historico(transacoes), use_container_width=True, hide_index=True)

            # -----------------------------------------------------------
            # Remover uma única transação do histórico
            # -----------------------------------------------------------
            with st.container(border=True):
                st.markdown("##### 🗑️ Remover Lançamento Específico")

                opcoes_remocao = [
                    f"[{i}] {t['data']} — {t['descricao']}" for i, t in enumerate(transacoes)
                ]
                indice_selecionado = st.selectbox(
                    "Selecione a transação que deseja remover",
                    options=range(len(transacoes)),
                    format_func=lambda i: opcoes_remocao[i],
                    key="fin_indice_remocao",
                )

                if st.button("Excluir Transação Selecionada", use_container_width=True):
                    st.session_state.transacoes.pop(indice_selecionado)
                    salvar_transacoes_na_nuvem(st.session_state.transacoes)
                    st.success("✅ Transação removida com sucesso!")
                    st.rerun()

            # -----------------------------------------------------------
            # Limpar todo o histórico — protegido por senha + confirmação dupla
            # -----------------------------------------------------------
            with st.container(border=True):
                st.markdown("##### 🔒 Limpar Todo o Histórico")

                # ALTERE A SENHA AQUI para definir sua própria senha de confirmação.
                SENHA_LIMPEZA_HISTORICO = "1234"

                if not st.session_state.confirmando_limpeza_financeira:
                    st.caption("Esta ação apaga TODAS as transações registradas. É protegida por senha.")
                    if st.button("🗑️ Limpar Todo o Histórico", use_container_width=True):
                        st.session_state.confirmando_limpeza_financeira = True
                        st.rerun()
                else:
                    st.warning("⚠️ Você está prestes a apagar TODO o histórico. Esta ação não pode ser desfeita.")
                    senha_digitada = st.text_input(
                        "Digite a senha para continuar",
                        type="password",
                        key="fin_senha_limpeza",
                    )

                    col_confirmar, col_cancelar = st.columns(2)
                    with col_confirmar:
                        if senha_digitada:
                            if senha_digitada == SENHA_LIMPEZA_HISTORICO:
                                if st.button(
                                    "⚠️ Sim, tenho certeza absoluta que quero apagar tudo",
                                    use_container_width=True,
                                ):
                                    st.session_state.transacoes = []
                                    salvar_transacoes_na_nuvem(st.session_state.transacoes)
                                    st.session_state.confirmando_limpeza_financeira = False
                                    st.success("✅ Histórico apagado com sucesso.")
                                    st.rerun()
                            else:
                                st.error("Senha incorreta.")
                    with col_cancelar:
                        if st.button("Cancelar", use_container_width=True):
                            st.session_state.confirmando_limpeza_financeira = False
                            st.rerun()

# =============================================================================
# RODAPÉ
# =============================================================================
st.divider()
st.caption("⚡ EletriHub — Ferramenta de apoio profissional. Os cálculos seguem a NBR 5410, "
           "mas não substituem a responsabilidade técnica de um profissional habilitado.")
