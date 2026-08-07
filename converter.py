# -*- coding: utf-8 -*-
"""
Aplicativo de Conversao - GIV (SQL Anywhere 9) -> Web (PostgreSQL)
Converte Grupo_moda -> Grupo, fornecedor -> fornecedor e cliente -> cliente.
Python 32 bits - Usa ODBC para SQL Anywhere e pg8000 para PostgreSQL.
"""

import pyodbc
import pg8000
import requests
import sys
import datetime
import getpass
import os
import re
import time
import unicodedata
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlparse, unquote

# Forcar UTF-8 no console Windows e liberar logs linha a linha para a interface grafica.
sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True, write_through=True)
sys.stderr.reconfigure(encoding='utf-8', errors='replace', line_buffering=True, write_through=True)


# ============================================================
# CONFIGURAÇÕES - Altere conforme necessário
# ============================================================

# ODBC DSN para o SQL Anywhere 9 (GIV)
def env_config(nome, padrao):
    """Le configuracao por variavel de ambiente sem quebrar o uso por terminal."""
    return os.environ.get(nome, padrao)


def pg_config_padrao():
    """Permite configurar PostgreSQL por URL ou campos separados."""
    config = {
        'host': "rds-nuvem.ch0iy8mcu5f8.sa-east-1.rds.amazonaws.com",
        'port': "5432",
        'database': "desenvolvimento",
        'user': "postgres",
        'password': "tw-ApostS5202",
    }
    url = os.environ.get("CONVERTER_PG_URL") or os.environ.get("DATABASE_URL")
    if url:
        parsed = urlparse(url)
        if parsed.hostname:
            config['host'] = parsed.hostname
        if parsed.port:
            config['port'] = str(parsed.port)
        if parsed.path and parsed.path.strip('/'):
            config['database'] = parsed.path.strip('/').split('/')[0]
        if parsed.username:
            config['user'] = unquote(parsed.username)
        if parsed.password:
            config['password'] = unquote(parsed.password)

    config['host'] = env_config("CONVERTER_PG_HOST", config['host'])
    config['port'] = env_config("CONVERTER_PG_PORT", config['port'])
    config['database'] = env_config("CONVERTER_PG_DATABASE", config['database'])
    config['user'] = env_config("CONVERTER_PG_USER", config['user'])
    config['password'] = env_config("CONVERTER_PG_PASSWORD", config['password'])
    return config


ODBC_DSN = env_config("CONVERTER_ODBC_DSN", "giv")
ODBC_USER = "dba"       # Deixe vazio se o DSN já tem usuário
ODBC_PASSWORD = "sql"   # Deixe vazio se o DSN já tem senha

# String de conexão PostgreSQL (Web)
PG_HOST = "rds-nuvem.ch0iy8mcu5f8.sa-east-1.rds.amazonaws.com"
PG_PORT = "5432"
PG_DATABASE = "desenvolvimento"
PG_USER = "postgres"
PG_PASSWORD = "tw-ApostS5202"

# Overrides usados pela interface grafica e por execucoes automatizadas.
ODBC_USER = env_config("CONVERTER_ODBC_USER", ODBC_USER)
ODBC_PASSWORD = env_config("CONVERTER_ODBC_PASSWORD", ODBC_PASSWORD)
_PG_CONFIG = pg_config_padrao()
PG_HOST = _PG_CONFIG['host']
PG_PORT = _PG_CONFIG['port']
PG_DATABASE = _PG_CONFIG['database']
PG_USER = _PG_CONFIG['user']
PG_PASSWORD = _PG_CONFIG['password']

TENANT_ID_PADRAO = 1
CD_EMPRESA_PADRAO = 1
ID_STATUS_FORNECEDOR_PADRAO = "A"
FUNCAO_USUARIO_PADRAO = 72
MAPA_STATUS_PRODUTO_PADRAO = {
    'L': 'A',
    'E': 'I',
    'F': 'I',
}
# ---------------------------------------------------------------------------
# Tabela de preco padrao
#
# Regra oficial do sistema Web (projeto_v1), confirmada em:
#   src/adapters/outbound/repositories/utils/tabelaPrecoPadrao.ts
#   src/application/usecases/config/upsertConfigValueUseCase.ts
#   prisma/migrations/20260715210000_tabela_preco_padrao_por_empresa/migration.sql
#   prisma/migrations/20260716143000_add_preco_por_produto_filho/migration.sql
#
# 1) "tabela_preco" pertence ao TENANT (nao possui cd_empresa) e o
#    "cd_tabela_preco" e uma sequence GLOBAL (@default(autoincrement())).
#    Logo, nao existe garantia nenhuma de que o tenant tenha o codigo 1.
# 2) Cada EMPRESA escolhe qual tabela do tenant e a padrao atraves de:
#       sys_config_values(key='tabela_preco_padrao', scope_type='empresa',
#                         tenant_id, cd_empresa) -> value = cd_tabela_preco
#    (a chave e obrigatoriamente de escopo 'empresa'; o escopo 'tenant' foi
#     removido pela migration 20260715210000).
# 3) O codigo so e valido se existir em tabela_preco com o MESMO tenant_id e
#    id_status = 'A'.
# 4) A FK "fk_produto_preco_tabela_tenant" exige o par
#    (tenant_id, cd_tabela_preco) presente em tabela_preco, e a unique
#    "ux_tabela_preco_tenant_codigo" garante esse par.
#
# Por isso NAO existe codigo fixo seguro. O valor abaixo comeca indefinido e e
# preenchido em tempo de execucao por resolver_tabela_preco_padrao_web().
# ---------------------------------------------------------------------------
# Modos de execucao segura (ver parse_argumentos_execucao()).
MODO_DRY_RUN = False
LIMITE_PRODUTOS_CONVERSAO = 0

CONFIG_TABELA_PRECO_PADRAO = "tabela_preco_padrao"
DS_TABELA_PRECO_PADRAO_CONVERSAO = "TABELA PADRAO"
CD_TABELA_PRECO_PRODUTO_PADRAO = None
CD_COLECAO_PRODUTO_PADRAO = 3
CD_SITUACAO_TRIBUTARIA_PRODUTO_PADRAO = "000"
DIAS_LICENCA_EMPRESA_PADRAO = 30
ADMIN_NOME_PADRAO = "Administrador"
ADMIN_LOGIN_PADRAO = "admin"
ADMIN_SENHA_PADRAO = "admin123"
TAMANHO_LOTE_PRODUTO = 300
TAMANHO_BLOCO_SEQUENCE_PRODUTO = 5000
LOG_PRODUTO_INTERVALO_REGISTROS = 1000
LOG_PRODUTO_INTERVALO_SEGUNDOS = 5
AUXILIARES_PRODUTO = (
    "tamanho",
    "unidade",
    "colecao",
    "grade",
)
OPCAO_NOVA_EMPRESA = "nova_empresa"
OPCAO_EMPRESA_GIV = "empresa_giv"
TABELAS_DISPONIVEIS = (
    "grupo",
    "departamento",
    "sub_grupo",
    "marca",
    "cor",
    "tamanho",
    "unidade",
    "colecao",
    "grade",
    "fornecedor",
    "usuario",
    "cliente",
    "banco",
    "banco_conta",
    "condicao_pagamento",
    "cartao_administradora",
    "produto",
    "condicional",
    "pedido_compra",
    "nota_fiscal_entrada",
    "prevenda",
    "nota_fiscal_saida",
    "titulo_receber",
    "titulo_pagar",
)
DEPENDENCIAS_PROCESSAMENTO = {
    "sub_grupo": ("grupo",),
    "grade": ("tamanho",),
    "cliente": ("usuario",),
    "produto": (
        "grupo",
        "departamento",
        "sub_grupo",
        "marca",
        "cor",
        "tamanho",
        "unidade",
        "colecao",
        "grade",
        "fornecedor",
    ),
    "banco_conta": ("banco",),
    "condicional": ("cliente", "usuario", "produto"),
    "pedido_compra": ("fornecedor", "usuario", "condicao_pagamento", "produto"),
    "nota_fiscal_entrada": ("fornecedor", "usuario", "condicao_pagamento", "produto"),
    "prevenda": ("cliente", "usuario", "condicao_pagamento", "cartao_administradora", "produto"),
    "nota_fiscal_saida": ("cliente", "usuario", "condicao_pagamento", "produto"),
    "titulo_receber": ("cliente", "usuario", "condicao_pagamento", "nota_fiscal_saida"),
    "titulo_pagar": ("fornecedor", "usuario", "condicao_pagamento", "banco", "nota_fiscal_entrada"),
}
ROTINAS_COM_PRODUTO = {
    "condicional",
    "pedido_compra",
    "nota_fiscal_entrada",
    "prevenda",
    "nota_fiscal_saida",
}
NOMES_TABELAS_LOG = {
    "grupo": "grupo",
    "departamento": "departamento",
    "sub_grupo": "sub_grupo",
    "marca": "marca",
    "cor": "cor",
    "tamanho": "tamanho",
    "unidade": "unidade",
    "colecao": "colecao",
    "grade": "grade",
    "fornecedor": "fornecedor",
    "usuario": "usuario",
    "cliente": "cliente",
    "banco": "banco",
    "banco_conta": "banco_conta",
    "condicao_pagamento": "condicao_pagamento",
    "cartao_administradora": "cartao_administradora",
    "produto": "produto",
    "condicional": "condicional",
    "pedido_compra": "pedido_compra",
    "nota_fiscal_entrada": "nota_fiscal_entrada",
    "prevenda": "prevenda",
    "nota_fiscal_saida": "nota_fiscal_saida",
    "titulo_receber": "titulo_receber",
    "titulo_pagar": "titulo_pagar",
}


def ordenar_tabelas_para_processamento(tabelas):
    """Ordena as tabelas pela ordem segura de dependencias."""
    selecionadas = set(tabelas)
    ordenadas = [tabela for tabela in TABELAS_DISPONIVEIS if tabela in selecionadas]
    extras = [tabela for tabela in tabelas if tabela not in TABELAS_DISPONIVEIS and tabela not in ordenadas]
    return ordenadas + extras


def dependencias_nao_selecionadas(tabelas):
    """Retorna dependencias nao marcadas; elas podem existir previamente no Web."""
    selecionadas = set(tabelas)
    faltantes = {}
    for tabela in tabelas:
        deps = [dep for dep in DEPENDENCIAS_PROCESSAMENTO.get(tabela, ()) if dep not in selecionadas]
        if deps:
            faltantes[tabela] = deps
    return faltantes


def avisar_dependencias_nao_selecionadas(tabelas):
    faltantes = dependencias_nao_selecionadas(tabelas)
    if not faltantes:
        return
    print()
    print("[AVISO] Algumas dependencias nao foram selecionadas nesta execucao.")
    print("[AVISO] O conversor vai tentar usar os cadastros que ja existem no Web.")
    print("[AVISO] Se o banco Web estiver limpo, marque tambem estas tabelas:")
    for tabela, deps in faltantes.items():
        deps_txt = ", ".join(NOMES_TABELAS_LOG.get(dep, dep) for dep in deps)
        print(f"  - {NOMES_TABELAS_LOG.get(tabela, tabela)} depende de: {deps_txt}")


def gui_progress_tabela(tabela):
    """Emite marcador estruturado usado somente pela interface grafica."""
    print(f"[GUI_PROGRESS] tabela={tabela}", flush=True)


API_CEP_BASE_URL_PADRAO = "http://17.1.28.148:8001/api"
API_CEP_USUARIO_PADRAO = "tw"
API_CEP_SENHA_PADRAO = "ebx820"
API_TIMEOUT = 30
API_CEP_TIMEOUT = 10
API_CEP_TENTATIVAS = 3
API_CEP_ESPERA_RETRY = 1
TAMANHO_LOTE_GERAL = 500
LOG_GERAL_INTERVALO_REGISTROS = 1000
LOG_GERAL_INTERVALO_SEGUNDOS = 5

# ============================================================


class CepApiIndisponivel(RuntimeError):
    """Indica falha temporaria na consulta externa de CEP."""


class CepApiErroServidor(RuntimeError):
    """Indica erro 5xx retornado pela API de CEP."""


def conectar_giv():
    """Conecta ao banco GIV via ODBC."""
    tentativas = []
    conn_str = f"DSN={ODBC_DSN}"
    if ODBC_USER:
        conn_str += f";UID={ODBC_USER}"
    if ODBC_PASSWORD:
        conn_str += f";PWD={ODBC_PASSWORD}"
    tentativas.append(conn_str)
    tentativas.append(f"DSN={ODBC_DSN}")

    ultimo_erro = None
    for idx, conn_str in enumerate(dict.fromkeys(tentativas), start=1):
        try:
            conn = pyodbc.connect(conn_str)
            if idx == 1:
                print("[OK] Conectado ao banco GIV (SQL Anywhere 9) via ODBC.")
            else:
                print("[OK] Conectado ao banco GIV via DSN sem usuario/senha explicitos.")
            return conn
        except Exception as e:
            ultimo_erro = e

    print(f"[ERRO] Falha ao conectar no banco GIV: {ultimo_erro}")
    sys.exit(1)


def conectar_web():
    """Conecta ao banco Web via PostgreSQL."""
    try:
        conn = pg8000.connect(
            host=PG_HOST,
            port=int(PG_PORT),
            database=PG_DATABASE,
            user=PG_USER,
            password=PG_PASSWORD
        )
        # Desabilita autocommit para controle manual de transação
        conn.autocommit = False
        print("[OK] Conectado ao banco Web (PostgreSQL).")
        return conn
    except Exception as e:
        print(f"[ERRO] Falha ao conectar no banco Web (PostgreSQL): {e}")
        sys.exit(1)


def normalizar_codigo_cidade(valor):
    """Normaliza codigo de cidade para inteiro quando possivel."""
    if valor is None:
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return valor


def normalizar_nome_cidade(nome):
    """Normaliza nome de cidade para comparacao GIV x Web."""
    if nome is None:
        return None

    texto = str(nome).strip().upper()
    if not texto:
        return None

    texto = unicodedata.normalize('NFD', texto)
    texto = ''.join(ch for ch in texto if unicodedata.category(ch) != 'Mn')
    texto = re.sub(r'[^A-Z0-9]+', ' ', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto or None


def buscar_cidades_web(cursor_web):
    """
    Busca todas as cidades do banco Web (PostgreSQL) e retorna um dict
    com nome_cidade (lowercase) → cd_cidade (código IBGE).
    """
    cursor_web.execute("SAVEPOINT sp_buscar_cidades_web")
    try:
        cursor_web.execute("SELECT cd_cidade, nm_cidade FROM cidade")
        cidades = {
            'por_nome': {},
            'codigos': set(),
        }
        for row in cursor_web.fetchall():
            cd = normalizar_codigo_cidade(row[0])
            nome = row[1]
            if cd is not None:
                cidades['codigos'].add(cd)
            chave_nome = normalizar_nome_cidade(nome)
            if chave_nome and chave_nome not in cidades['por_nome']:
                cidades['por_nome'][chave_nome] = cd
        cursor_web.execute("RELEASE SAVEPOINT sp_buscar_cidades_web")
        print(f"[OK] {len(cidades['por_nome'])} cidades carregadas do banco Web.")
        return cidades
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_buscar_cidades_web")
            cursor_web.execute("RELEASE SAVEPOINT sp_buscar_cidades_web")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar cidades do banco Web: {e}")
        return {}


def buscar_cidades_giv(cursor_giv):
    """
    Busca todas as cidades do banco GIV e retorna um dict
    com cd_cidade_giv → nome_cidade.
    """
    try:
        cursor_giv.execute("SELECT cd_cidade, nm_cidade, cd_cidade_ibge, cd_estado FROM cidade")
        cidades = {}
        for row in cursor_giv.fetchall():
            cd = normalizar_codigo_cidade(row[0])
            nome = row[1]
            cd_ibge = normalizar_codigo_cidade(row[2])
            cd_estado = limpar_valor(row[3])
            chave_nome = normalizar_nome_cidade(nome)
            if cd is not None and chave_nome:
                cidades[cd] = {
                    'nome': str(nome).strip(),
                    'chave_nome': chave_nome,
                    'cd_cidade_ibge': cd_ibge,
                    'cd_estado': cd_estado,
                }
        print(f"[OK] {len(cidades)} cidades carregadas do banco GIV.")
        return cidades
    except Exception as e:
        print(f"[AVISO] Erro ao buscar cidades do banco GIV: {e}")
        return {}


def resolver_cidade_trab(cd_cidade_giv, cidades_giv, cidades_web, contexto='cidade'):
    """
    Converte o cd_cidade_trab do GIV (sequencial) para o código IBGE do Web.
    Faz isso buscando o nome da cidade no GIV e depois procurando no Web.
    """
    if cd_cidade_giv is None:
        return None

    cd_cidade_giv = normalizar_codigo_cidade(cd_cidade_giv)
    cidade_giv = cidades_giv.get(cd_cidade_giv)
    if cidade_giv is None:
        return None

    chave_nome = cidade_giv.get('chave_nome')
    cd_cidade_web = cidades_web.get('por_nome', {}).get(chave_nome)
    if cd_cidade_web is not None:
        return cd_cidade_web

    cd_ibge_giv = cidade_giv.get('cd_cidade_ibge')
    if cd_ibge_giv in cidades_web.get('codigos', set()):
        return cd_ibge_giv

    print(
        f"  [AVISO] Cidade nao encontrada no Web para {contexto}: "
        f"cd_cidade_giv={cd_cidade_giv}, nome_giv={cidade_giv.get('nome')}, ibge_giv={cd_ibge_giv}"
    )
    return None


def limpar_valor(valor):
    """Remove espacos extras dos campos texto vindos do SQL Anywhere."""
    if isinstance(valor, str):
        valor = valor.strip()
        return valor or None
    return valor


def somente_digitos(valor):
    """Retorna apenas os digitos de um valor."""
    valor = limpar_valor(valor)
    if valor is None:
        return None

    if isinstance(valor, Decimal):
        if valor == valor.to_integral_value():
            valor = str(int(valor))
        else:
            valor = format(valor, 'f')
    elif isinstance(valor, float):
        if valor.is_integer():
            valor = str(int(valor))

    texto = str(valor).strip()
    if texto.replace(',', '.', 1).replace('.', '', 1).isdigit() and ('.' in texto or ',' in texto):
        parte_inteira, _, parte_decimal = texto.replace(',', '.').partition('.')
        if parte_decimal and set(parte_decimal) == {'0'}:
            texto = parte_inteira

    digitos = ''.join(ch for ch in texto if ch.isdigit())
    return digitos or None


def normalizar_cadastro_nacional(valor):
    """Normaliza CPF/CNPJ; valores como S/N viram None."""
    digitos = somente_digitos(valor)
    if digitos and len(digitos) == 10:
        digitos = digitos.zfill(11)
    elif digitos and len(digitos) == 13:
        digitos = digitos.zfill(14)

    if digitos and len(digitos) in (11, 14):
        return digitos
    return None


def normalizar_cpf_web(valor):
    """Normaliza CPF para colunas do Web que aceitam somente 11 digitos."""
    digitos = somente_digitos(valor)
    if digitos and len(digitos) == 10:
        digitos = digitos.zfill(11)
    if digitos and len(digitos) == 11:
        return digitos
    return None


def normalizar_documento_cliente(tp_pessoa, *valores):
    """Normaliza CPF/CNPJ de cliente respeitando o tipo de pessoa."""
    tipo = limpar_valor(tp_pessoa)
    tipo = str(tipo).strip().upper() if tipo is not None else ''

    for valor in valores:
        digitos = somente_digitos(valor)
        if not digitos:
            continue

        if tipo == 'J':
            if 12 <= len(digitos) < 14:
                digitos = digitos.zfill(14)
            if len(digitos) == 14:
                return digitos
            continue

        if tipo == 'F':
            if len(digitos) == 10:
                digitos = digitos.zfill(11)
            if len(digitos) == 11:
                return digitos
            continue

        documento = normalizar_cadastro_nacional(digitos)
        if documento:
            return documento

    return None


def normalizar_estado_civil_cliente(valor):
    """Normaliza estado civil do GIV para texto usado no cadastro Web."""
    texto = limpar_valor(valor)
    if texto is None:
        return None

    chave = unicodedata.normalize('NFD', str(texto).strip().upper())
    chave = ''.join(ch for ch in chave if unicodedata.category(ch) != 'Mn')
    chave = re.sub(r'[^A-Z0-9]+', ' ', chave).strip()

    mapa = {
        'S': 'SOLTEIRO',
        'SOLTEIRO': 'SOLTEIRO',
        'SOLTEIRA': 'SOLTEIRO',
        'C': 'CASADO',
        'CASADO': 'CASADO',
        'CASADA': 'CASADO',
        'D': 'DIVORCIADO',
        'DIVORCIADO': 'DIVORCIADO',
        'DIVORCIADA': 'DIVORCIADO',
        'SEPARADO': 'DIVORCIADO',
        'SEPARADA': 'DIVORCIADO',
        'V': 'VIUVO',
        'VIUVO': 'VIUVO',
        'VIUVA': 'VIUVO',
    }
    return mapa.get(chave, str(texto).strip().upper())


def classificar_contribuinte_cliente_pj(valor):
    """Converte IE/tipo contribuinte do GIV para tp_contribuinte e rg do cliente PJ no Web."""
    texto = limpar_valor(valor)
    if texto is None:
        return None, None

    chave = unicodedata.normalize('NFD', str(texto).strip().upper())
    chave = ''.join(ch for ch in chave if unicodedata.category(ch) != 'Mn')
    chave = re.sub(r'[^A-Z0-9]+', ' ', chave).strip()

    if not chave:
        return None, None
    if (
        'NAO CONTRIB' in chave
        or 'N CONTRIB' in chave
        or 'N O CONTRIB' in chave
        or (chave.startswith('NC') and 'CONTRIB' in chave)
    ):
        return 'NC', None
    if 'ISENTO' in chave:
        return 'I', None

    digitos = somente_digitos(texto)
    ie = digitos or str(texto).strip().upper()
    return 'C', ie


def data_fundacao_cliente_pj(registro_giv):
    """Para PJ, a data de fundacao vem de dt_nascimento; cai para dt_cadastro se vier vazia."""
    data = registro_giv.get('dt_nascimento')
    if data and str(data)[:10] != '1900-01-01':
        return data
    data = registro_giv.get('dt_cadastro')
    if data and str(data)[:10] != '1900-01-01':
        return data
    return None


def normalizar_cep(valor):
    """Normaliza CEP para 8 digitos; valores invalidos viram None."""
    digitos = somente_digitos(valor)
    if digitos and len(digitos) == 8:
        return digitos
    return None


DDDS_BRASIL = {
    '11', '12', '13', '14', '15', '16', '17', '18', '19',
    '21', '22', '24', '27', '28',
    '31', '32', '33', '34', '35', '37', '38',
    '41', '42', '43', '44', '45', '46', '47', '48', '49',
    '51', '53', '54', '55',
    '61', '62', '63', '64', '65', '66', '67', '68', '69',
    '71', '73', '74', '75', '77', '79',
    '81', '82', '83', '84', '85', '86', '87', '88', '89',
    '91', '92', '93', '94', '95', '96', '97', '98', '99',
}

DDD_PADRAO_UF = {
    'RO': '69', 'AC': '68', 'AM': '92', 'RR': '95', 'PA': '91', 'AP': '96', 'TO': '63',
    'MA': '98', 'PI': '86', 'CE': '85', 'RN': '84', 'PB': '83', 'PE': '81', 'AL': '82',
    'SE': '79', 'BA': '71', 'MG': '31', 'ES': '27', 'RJ': '21', 'SP': '11',
    'PR': '41', 'SC': '48', 'RS': '51', 'MS': '67', 'MT': '65', 'GO': '62', 'DF': '61',
}

DDD_PADRAO_UF_IBGE = {
    11: '69', 12: '68', 13: '92', 14: '95', 15: '91', 16: '96', 17: '63',
    21: '98', 22: '86', 23: '85', 24: '84', 25: '83', 26: '81', 27: '82',
    28: '79', 29: '71', 31: '31', 32: '27', 33: '21', 35: '11',
    41: '41', 42: '48', 43: '51', 50: '67', 51: '65', 52: '62', 53: '61',
}

DDD_CIDADE_NOME = {
    # Santa Catarina: o banco atual trabalha bastante com cidades desse estado.
    'JOINVILLE': '47',
    'ARAQUARI': '47',
    'BALNEARIO CAMBORIU': '47',
    'BLUMENAU': '47',
    'BRUSQUE': '47',
    'CAMBORIU': '47',
    'GASPAR': '47',
    'GUARAMIRIM': '47',
    'INDAIAL': '47',
    'ITAJAI': '47',
    'ITAPEMA': '47',
    'JARAGUA DO SUL': '47',
    'NAVEGANTES': '47',
    'PENHA': '47',
    'POMERODE': '47',
    'RIO DO SUL': '47',
    'SAO BENTO DO SUL': '47',
    'TIMBO': '47',
    'CHAPECO': '49',
    'CONCORDIA': '49',
    'CURITIBANOS': '49',
    'CACADOR': '49',
    'JOACABA': '49',
    'LAGES': '49',
    'SAO MIGUEL DO OESTE': '49',
    'VIDEIRA': '49',
    'XANXERE': '49',
    'CRICIUMA': '48',
    'FLORIANOPOLIS': '48',
    'ORLEANS': '48',
    'TUBARAO': '48',
}


def extrair_ddd_telefone(valor):
    """Extrai DDD apenas quando o numero ja parece vir com DDD."""
    digitos = somente_digitos(valor)
    if not digitos:
        return None
    while digitos.startswith('0') and len(digitos) > 10:
        digitos = digitos[1:]
    if len(digitos) in (10, 11) and digitos[:2] in DDDS_BRASIL:
        return digitos[:2]
    return None


def resolver_ddd_cidade_giv(cd_cidade_giv, cidades_giv, ddd_por_cidade=None):
    """Resolve DDD pela cidade do GIV, com fallback por UF e depois 48."""
    cd_cidade = normalizar_codigo_cidade(cd_cidade_giv)
    if ddd_por_cidade and cd_cidade in ddd_por_cidade:
        return ddd_por_cidade[cd_cidade]

    cidade = cidades_giv.get(cd_cidade) if cidades_giv else None
    if not cidade:
        return '48'

    chave_nome = cidade.get('chave_nome')
    if chave_nome in DDD_CIDADE_NOME:
        return DDD_CIDADE_NOME[chave_nome]

    uf = limpar_valor(cidade.get('cd_estado'))
    if uf:
        uf = str(uf).strip().upper()
        if uf in DDD_PADRAO_UF:
            return DDD_PADRAO_UF[uf]

    cd_ibge = cidade.get('cd_cidade_ibge')
    try:
        uf_ibge = int(str(cd_ibge)[:2])
        return DDD_PADRAO_UF_IBGE.get(uf_ibge, '48')
    except Exception:
        return '48'


def montar_mapa_ddd_por_cidade_clientes(clientes_giv):
    """Infere DDD por cidade usando telefones que ja vieram completos no GIV."""
    contagem = {}
    for registro in clientes_giv:
        campos = [
            ('cd_cidade', registro.get('fone')),
            ('cd_cidade', registro.get('celular')),
            ('cd_cidade_trab', registro.get('fone_trab')),
        ]
        for campo_cidade, telefone in campos:
            cd_cidade = normalizar_codigo_cidade(registro.get(campo_cidade))
            ddd = extrair_ddd_telefone(telefone)
            if cd_cidade is None or not ddd:
                continue
            chave = (cd_cidade, ddd)
            contagem[chave] = contagem.get(chave, 0) + 1

    mapa = {}
    for (cd_cidade, ddd), total in contagem.items():
        atual = mapa.get(cd_cidade)
        if atual is None or total > atual[1]:
            mapa[cd_cidade] = (ddd, total)
    return {cd_cidade: ddd for cd_cidade, (ddd, _total) in mapa.items()}


def normalizar_telefone_web(valor, ddd=None, celular=False):
    """Normaliza telefone/celular; se vier sem DDD, adiciona o DDD da cidade."""
    digitos = somente_digitos(valor)
    if not digitos:
        return None

    while digitos.startswith('0') and len(digitos) > 8:
        digitos = digitos[1:]

    if len(digitos) > 11 and digitos.startswith('55') and digitos[2:4] in DDDS_BRASIL:
        digitos = digitos[2:]

    ddd = somente_digitos(ddd) or '48'
    ddd = ddd[:2] if len(ddd) >= 2 else '48'
    if ddd not in DDDS_BRASIL:
        ddd = '48'

    def montar_numero(ddd_numero, local):
        while local.startswith('0') and len(local) > 8:
            local = local[1:]

        if celular:
            if len(local) > 9:
                if local[-9:].startswith('9'):
                    local = local[-9:]
                else:
                    local = '9' + local[-8:]
            elif len(local) == 8:
                local = '9' + local
            elif len(local) < 8:
                return ddd_numero + '999999999'
            return ddd_numero + local

        if len(local) > 9:
            if local[-9:].startswith('9'):
                local = local[-9:]
            else:
                local = local[-8:]
        elif len(local) < 8:
            return ddd_numero + '999999999'
        return ddd_numero + local

    if len(digitos) >= 10 and digitos[:2] in DDDS_BRASIL:
        return montar_numero(digitos[:2], digitos[2:])

    if len(digitos) in (8, 9):
        return montar_numero(ddd, digitos)

    if len(digitos) < 8:
        return ddd + '999999999'

    if len(digitos) > 9:
        if celular:
            return montar_numero(ddd, digitos)
        return montar_numero(ddd, digitos)

    return digitos


def normalizar_celular_web(valor, ddd=None):
    """Normaliza celular usando DDD da cidade quando o GIV vier sem DDD."""
    return normalizar_telefone_web(valor, ddd=ddd, celular=True)


def normalizar_codigo_fiscal(valor, tamanho):
    """Mantem somente digitos e limita codigos fiscais como NCM/CEST."""
    digitos = somente_digitos(valor)
    if not digitos:
        return None
    return digitos[:tamanho]


def valor_decimal_ou_zero(valor):
    """Retorna zero para campos numericos nulos."""
    if valor is None:
        return Decimal('0')
    return valor


def valor_decimal_campo_giv(registro, campo_principal, *campos_legado):
    """Le o campo numerico oficial do GIV sem somar aliases legados.

    Algumas tabelas antigas mantem colunas com nomes parecidos, por exemplo
    ``vl_outras_despesas`` e ``vl_outro``. Quando a coluna oficial existe, um
    zero nela e um valor valido e nao pode disparar fallback para o alias. Os
    procedimentos de relatorio do GIV usam o campo oficial; o fallback fica
    restrito a bancos/schema antigos em que essa coluna realmente nao exista.
    """
    if campo_principal in registro:
        return valor_decimal_ou_zero(registro.get(campo_principal))
    for campo_legado in campos_legado:
        if campo_legado in registro:
            return valor_decimal_ou_zero(registro.get(campo_legado))
    return Decimal('0')


def valor_inteiro_ou_zero(valor):
    """Retorna inteiro ou zero quando o valor estiver vazio/invalido."""
    if valor is None:
        return 0
    try:
        return int(valor)
    except Exception:
        return 0


def valor_flag(valor, padrao='N'):
    """Normaliza flags de um caractere."""
    valor = limpar_valor(valor)
    if valor is None:
        return padrao
    return str(valor).strip().upper()[:1] or padrao


def limitar_texto(valor, tamanho):
    """Limita texto para caber nas colunas varchar do Web."""
    valor = limpar_valor(valor)
    if valor is None:
        return None
    return str(valor)[:tamanho]


def chave_texto(valor):
    """Normaliza texto para comparacoes simples de duplicidade."""
    valor = limpar_valor(valor)
    if valor is None:
        return None
    return ' '.join(str(valor).upper().split()) or None


def texto_eh_asteriscos(valor):
    """Indica texto mascarado do tipo ********."""
    valor = limpar_valor(valor)
    if valor is None:
        return False
    texto = str(valor).strip()
    return bool(texto) and set(texto) == {'*'}


def nome_fornecedor_origem(registro_giv):
    """Nome do fornecedor conforme regra de conversao."""
    nm_fornecedor = limpar_valor(registro_giv.get('nm_fornecedor'))
    razao_social = limpar_valor(registro_giv.get('razao_social'))
    cd_fornecedor_origem = registro_giv.get('cd_fornecedor')

    if nm_fornecedor and not texto_eh_asteriscos(nm_fornecedor):
        return nm_fornecedor
    if razao_social:
        return razao_social
    return f"FORNECEDOR {cd_fornecedor_origem}"


def razao_social_fornecedor_origem(registro_giv):
    """Razao social do fornecedor deve vir do campo razao_social do GIV."""
    razao_social = limpar_valor(registro_giv.get('razao_social'))
    nm_fornecedor = limpar_valor(registro_giv.get('nm_fornecedor'))
    cd_fornecedor_origem = registro_giv.get('cd_fornecedor')

    if razao_social and not texto_eh_asteriscos(razao_social):
        return razao_social
    if nm_fornecedor and not texto_eh_asteriscos(nm_fornecedor):
        return nm_fornecedor
    return f"FORNECEDOR {cd_fornecedor_origem}"


def fantasia_fornecedor_origem(registro_giv):
    """Fantasia do fornecedor vem de nm_fornecedor, com fallback para razao_social."""
    nm_fornecedor = limpar_valor(registro_giv.get('nm_fornecedor'))
    if nm_fornecedor and not texto_eh_asteriscos(nm_fornecedor):
        return nm_fornecedor
    return razao_social_fornecedor_origem(registro_giv)


def limpar_registro(registro):
    """Aplica limpeza simples em todos os campos string de um registro."""
    for chave, valor in registro.items():
        registro[chave] = limpar_valor(valor)
    return registro


def quote_identificador(nome):
    """Coloca aspas em identificadores PostgreSQL."""
    return '"' + str(nome).replace('"', '""') + '"'


def resolver_tabela_web(cursor_web, nome_tabela):
    """
    Resolve o nome real da tabela no PostgreSQL.
    Assim funciona tanto se a tabela for grupo quanto se tiver sido criada como "Grupo".
    """
    cursor_web.execute("SAVEPOINT sp_resolver_tabela")
    try:
        cursor_web.execute(
            """
            SELECT table_schema, table_name
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
               AND lower(table_name) = lower(%s)
             ORDER BY
               CASE
                 WHEN table_name = %s THEN 0
                 WHEN table_name = lower(%s) THEN 1
                 ELSE 2
               END
             LIMIT 1
            """,
            (nome_tabela, nome_tabela, nome_tabela)
        )
        row = cursor_web.fetchone()
        cursor_web.execute("RELEASE SAVEPOINT sp_resolver_tabela")
        if row:
            return f"{quote_identificador(row[0])}.{quote_identificador(row[1])}"
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_resolver_tabela")
            cursor_web.execute("RELEASE SAVEPOINT sp_resolver_tabela")
        except Exception:
            pass
        print(f"[AVISO] Nao foi possivel resolver a tabela {nome_tabela} no Web: {e}")

    return nome_tabela


def resolver_tabela_web_opcional(cursor_web, nome_tabela):
    """Resolve uma tabela somente se ela existir no PostgreSQL."""
    cursor_web.execute("SAVEPOINT sp_resolver_tabela_opcional")
    try:
        cursor_web.execute(
            """
            SELECT table_schema, table_name
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
               AND lower(table_name) = lower(%s)
             ORDER BY
               CASE
                 WHEN table_name = %s THEN 0
                 WHEN table_name = lower(%s) THEN 1
                 ELSE 2
               END
             LIMIT 1
            """,
            (nome_tabela, nome_tabela, nome_tabela)
        )
        row = cursor_web.fetchone()
        cursor_web.execute("RELEASE SAVEPOINT sp_resolver_tabela_opcional")
        if row:
            return f"{quote_identificador(row[0])}.{quote_identificador(row[1])}"
    except Exception:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_resolver_tabela_opcional")
            cursor_web.execute("RELEASE SAVEPOINT sp_resolver_tabela_opcional")
        except Exception:
            pass
    return None


def buscar_registros_giv(cursor_giv, sql, params=None, limite=None):
    """Executa uma consulta no GIV e retorna lista de dicionarios."""
    if params is None:
        cursor_giv.execute(sql)
    else:
        cursor_giv.execute(sql, params)
    colunas = [desc[0].lower() for desc in cursor_giv.description]
    resultados = []
    for row in cursor_giv.fetchall():
        resultados.append(dict(zip(colunas, row)))
        if limite is not None and len(resultados) >= limite:
            break
    return resultados


_GIV_COLUNA_CACHE = {}


def tabela_giv_tem_coluna(cursor_giv, tabela, coluna):
    """Verifica uma coluna no SQL Anywhere sem derrubar a conversao se ela nao existir."""
    chave = (str(tabela).lower(), str(coluna).lower())
    if chave in _GIV_COLUNA_CACHE:
        return _GIV_COLUNA_CACHE[chave]

    try:
        cursor_giv.execute(f"SELECT {coluna} FROM {tabela} WHERE 1 = 0")
        _GIV_COLUNA_CACHE[chave] = True
    except Exception:
        _GIV_COLUNA_CACHE[chave] = False
    return _GIV_COLUNA_CACHE[chave]


def filtro_empresa_giv(cursor_giv, tabela, alias=None, cd_empresa_giv=None):
    """Monta filtro de cd_empresa para tabelas GIV que possuem essa coluna."""
    if cd_empresa_giv is None or not tabela_giv_tem_coluna(cursor_giv, tabela, 'cd_empresa'):
        return "", []
    prefixo = f"{alias}." if alias else ""
    return f"WHERE {prefixo}cd_empresa = ?", [cd_empresa_giv]


def buscar_existentes_web_generico(cursor_web, tabela_web, colunas_chave):
    """Busca chaves existentes no Web para evitar duplicidades."""
    cursor_web.execute("SAVEPOINT sp_buscar_existentes")
    try:
        colunas_sql = ', '.join(quote_identificador(col) for col in colunas_chave)
        cursor_web.execute(f"SELECT {colunas_sql} FROM {tabela_web}")
        existentes = {tuple(row) for row in cursor_web.fetchall()}
        cursor_web.execute("RELEASE SAVEPOINT sp_buscar_existentes")
        return existentes
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_buscar_existentes")
            cursor_web.execute("RELEASE SAVEPOINT sp_buscar_existentes")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar registros existentes em {tabela_web}: {e}")
        return set()


def separar_schema_tabela(tabela_web):
    """Retorna schema e tabela a partir de um nome possivelmente qualificado/aspado."""
    tabela_limpa = tabela_web.replace('"', '')
    if '.' in tabela_limpa:
        schema, tabela = tabela_limpa.split('.', 1)
        return schema, tabela
    return 'public', tabela_limpa


def extrair_sequence_de_default(column_default):
    """Extrai o nome da sequence de um default do tipo nextval('seq'::regclass)."""
    if not column_default:
        return None

    marcador = "nextval('"
    inicio = str(column_default).find(marcador)
    if inicio < 0:
        return None

    inicio += len(marcador)
    fim = str(column_default).find("'", inicio)
    if fim < 0:
        return None

    return str(column_default)[inicio:fim]


def buscar_sequence_coluna_web(cursor_web, tabela_web, coluna):
    """Busca a sequence PostgreSQL vinculada a uma coluna serial/identity."""
    schema, tabela = separar_schema_tabela(tabela_web)
    tabela_para_funcao = f"{schema}.{tabela}"

    cursor_web.execute(
        "SELECT pg_get_serial_sequence(%s, %s)",
        (tabela_para_funcao, coluna)
    )
    row = cursor_web.fetchone()
    if row and row[0]:
        return row[0]

    cursor_web.execute(
        """
        SELECT column_default
          FROM information_schema.columns
         WHERE table_schema = %s
           AND table_name = %s
           AND column_name = %s
        """,
        (schema, tabela, coluna)
    )
    row = cursor_web.fetchone()
    sequence_nome = extrair_sequence_de_default(row[0] if row else None)
    if sequence_nome:
        if '.' not in sequence_nome:
            return f"{schema}.{sequence_nome}"
        return sequence_nome

    nomes_provaveis = (
        f"{tabela}_{coluna}_seq",
        f"{tabela.lower()}_{coluna.lower()}_seq",
    )
    cursor_web.execute(
        """
        SELECT sequence_schema, sequence_name
          FROM information_schema.sequences
         WHERE sequence_schema = %s
           AND sequence_name IN (%s, %s)
         ORDER BY sequence_name
         LIMIT 1
        """,
        (schema, nomes_provaveis[0], nomes_provaveis[1])
    )
    row = cursor_web.fetchone()
    if row:
        return f"{row[0]}.{row[1]}"

    raise RuntimeError(f"Nao foi encontrada sequence para {tabela_web}.{coluna}")


def sincronizar_sequence_com_max(cursor_web, tabela_web, coluna, sequence_nome):
    """Garante que a sequence nao esteja abaixo do maior codigo ja gravado."""
    cursor_web.execute(f"SELECT COALESCE(MAX({quote_identificador(coluna)}), 0) FROM {tabela_web}")
    row = cursor_web.fetchone()
    max_atual = int(row[0] or 0)
    if max_atual > 0:
        cursor_web.execute(
            "SELECT setval(CAST(%s AS regclass), %s, true)",
            (sequence_nome, max_atual)
        )
    return max_atual


def proximo_valor_sequence(cursor_web, sequence_nome):
    """Retorna o proximo valor de uma sequence PostgreSQL."""
    cursor_web.execute("SELECT nextval(CAST(%s AS regclass))", (sequence_nome,))
    row = cursor_web.fetchone()
    return row[0]


def reservar_valores_sequence(cursor_web, sequence_nome, quantidade):
    """Reserva varios valores de uma sequence em uma unica ida ao PostgreSQL."""
    quantidade = int(quantidade or 0)
    if quantidade <= 0:
        return []
    cursor_web.execute(
        "SELECT nextval(CAST(%s AS regclass)) FROM generate_series(1, %s)",
        (sequence_nome, quantidade)
    )
    return [row[0] for row in cursor_web.fetchall()]


def gerador_codigos_sequence(cursor_web, sequence_nome, total, tamanho_bloco):
    """
    Entrega codigos da sequence sob demanda, reservando em blocos.
    Evita consumir centenas de milhares de valores da sequence quando a etapa
    falha no inicio. nextval nao volta atras no rollback, entao so reservamos o
    que realmente vamos usar. A ordem de emissao continua crescente, preservando
    o relacionamento pai/filho montado na mesma ordem de antes.
    """
    restante = int(total or 0)
    while restante > 0:
        bloco = min(tamanho_bloco, restante)
        for codigo in reservar_valores_sequence(cursor_web, sequence_nome, bloco):
            yield codigo
        restante -= bloco


def sincronizar_sequence_produto_global(cursor_web, tabela_web_produto, tabela_web_produto_filho, sequence_nome):
    """Sincroniza a sequence global de produto com produto e produto_filho."""
    cursor_web.execute(f"SELECT COALESCE(MAX({quote_identificador('cd_produto')}), 0) FROM {tabela_web_produto}")
    max_produto = int(cursor_web.fetchone()[0] or 0)
    cursor_web.execute(
        f"SELECT COALESCE(MAX({quote_identificador('cd_produto_filho')}), 0) FROM {tabela_web_produto_filho}"
    )
    max_filho = int(cursor_web.fetchone()[0] or 0)
    max_atual = max(max_produto, max_filho)
    if max_atual > 0:
        cursor_web.execute(
            "SELECT setval(CAST(%s AS regclass), %s, true)",
            (sequence_nome, max_atual)
        )
    return max_atual


def buscar_limites_texto_web(cursor_web, tabela_web):
    """Busca limites de colunas textuais para evitar erro varchar(n)."""
    schema, tabela = separar_schema_tabela(tabela_web)
    cursor_web.execute("SAVEPOINT sp_limites_texto")
    try:
        cursor_web.execute(
            """
            SELECT column_name, character_maximum_length
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
               AND character_maximum_length IS NOT NULL
            """,
            (schema, tabela)
        )
        limites = {row[0]: int(row[1]) for row in cursor_web.fetchall() if row[1]}
        cursor_web.execute("RELEASE SAVEPOINT sp_limites_texto")
        return limites
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_limites_texto")
            cursor_web.execute("RELEASE SAVEPOINT sp_limites_texto")
        except Exception:
            pass
        print(f"[AVISO] Nao foi possivel buscar limites de texto em {tabela_web}: {e}")
        return {}


def aplicar_limites_texto_web(cursor_web, tabela_web, registros, contexto):
    """Trunca campos string conforme varchar/char do PostgreSQL."""
    if not registros:
        return

    limites = buscar_limites_texto_web(cursor_web, tabela_web)
    if not limites:
        return

    ajustados = 0
    for registro in registros:
        for coluna, limite in limites.items():
            if coluna not in registro:
                continue

            valor = limpar_valor(registro.get(coluna))
            if not isinstance(valor, str):
                continue

            if len(valor) > limite:
                registro[coluna] = valor[:limite]
                ajustados += 1
            else:
                registro[coluna] = valor

    if ajustados:
        print(f"[AVISO] {contexto}: {ajustados} campos texto foram limitados ao tamanho do PostgreSQL.")


def aplicar_limites_texto_registro(registro, limites):
    """Aplica limites textuais ja carregados em um unico registro."""
    if not limites:
        return registro
    for coluna, limite in limites.items():
        if coluna not in registro:
            continue
        valor = limpar_valor(registro.get(coluna))
        if isinstance(valor, str):
            registro[coluna] = valor[:limite]
    return registro


def buscar_fornecedores_existentes_web(cursor_web, tabela_web_fornecedor, tenant_id):
    """Busca documentos e nomes ja existentes para evitar duplicar fornecedor sequencial."""
    cursor_web.execute("SAVEPOINT sp_docs_fornecedor")
    try:
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador('id_cadastro_nacional')},
                {quote_identificador('razao_social')},
                {quote_identificador('nm_fantasia_fornecedor')}
              FROM {tabela_web_fornecedor}
             WHERE {quote_identificador('tenant_id')} = %s
            """,
            (tenant_id,)
        )
        documentos = set()
        nomes = set()
        for row in cursor_web.fetchall():
            documento = normalizar_cadastro_nacional(row[0])
            if documento:
                documentos.add(documento)

            for valor_nome in (row[1], row[2]):
                nome = chave_texto(valor_nome)
                if nome:
                    nomes.add(nome)

        cursor_web.execute("RELEASE SAVEPOINT sp_docs_fornecedor")
        return documentos, nomes
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_docs_fornecedor")
            cursor_web.execute("RELEASE SAVEPOINT sp_docs_fornecedor")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar fornecedores existentes: {e}")
        return set(), set()


def corrigir_fornecedores_existentes_web(cursor_web, tabela_web_fornecedor, fornecedores_giv, tenant_id):
    """Corrige nomes/documentos de fornecedores ja gravados no Web usando o cgc do GIV."""
    corrigidos_por_documento = 0
    documentos_processados = set()

    for reg in fornecedores_giv:
        documento = normalizar_cadastro_nacional(reg.get('cgc'))
        if not documento or documento in documentos_processados:
            continue

        razao_social = razao_social_fornecedor_origem(reg)
        fantasia = fantasia_fornecedor_origem(reg)
        try:
            cursor_web.execute("SAVEPOINT sp_corrige_fornecedor")
            cursor_web.execute(
                f"""
                UPDATE {tabela_web_fornecedor}
                   SET {quote_identificador('razao_social')} = %s,
                       {quote_identificador('nm_fantasia_fornecedor')} = %s,
                       {quote_identificador('id_cadastro_nacional')} = %s
                 WHERE {quote_identificador('tenant_id')} = %s
                   AND {quote_identificador('id_cadastro_nacional')} = %s
                """,
                (razao_social, fantasia, documento, tenant_id, documento)
            )
            if getattr(cursor_web, 'rowcount', 0) and cursor_web.rowcount > 0:
                corrigidos_por_documento += cursor_web.rowcount
            cursor_web.execute("RELEASE SAVEPOINT sp_corrige_fornecedor")
            documentos_processados.add(documento)
        except Exception as e:
            try:
                cursor_web.execute("ROLLBACK TO SAVEPOINT sp_corrige_fornecedor")
                cursor_web.execute("RELEASE SAVEPOINT sp_corrige_fornecedor")
            except Exception:
                pass
            print(f"  [AVISO] Nao foi possivel corrigir fornecedor cgc={documento}: {e}")

    cursor_web.execute("SAVEPOINT sp_limpa_fornecedor")
    try:
        cursor_web.execute(
            f"""
            UPDATE {tabela_web_fornecedor}
               SET {quote_identificador('nm_fantasia_fornecedor')} = {quote_identificador('razao_social')}
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('razao_social')} IS NOT NULL
               AND TRIM({quote_identificador('razao_social')}) <> ''
               AND {quote_identificador('nm_fantasia_fornecedor')} IS NOT NULL
               AND REPLACE(TRIM({quote_identificador('nm_fantasia_fornecedor')}), '*', '') = ''
            """,
            (tenant_id,)
        )
        corrigidos_mascarados = cursor_web.rowcount if getattr(cursor_web, 'rowcount', 0) and cursor_web.rowcount > 0 else 0

        cursor_web.execute(
            f"""
            UPDATE {tabela_web_fornecedor}
               SET {quote_identificador('id_cadastro_nacional')} = NULL
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('id_cadastro_nacional')} IS NOT NULL
               AND {quote_identificador('id_cadastro_nacional')} !~ '^[0-9]{{11}}$|^[0-9]{{14}}$'
            """,
            (tenant_id,)
        )
        documentos_invalidos = cursor_web.rowcount if getattr(cursor_web, 'rowcount', 0) and cursor_web.rowcount > 0 else 0
        cursor_web.execute("RELEASE SAVEPOINT sp_limpa_fornecedor")
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_limpa_fornecedor")
            cursor_web.execute("RELEASE SAVEPOINT sp_limpa_fornecedor")
        except Exception:
            pass
        corrigidos_mascarados = 0
        documentos_invalidos = 0
        print(f"  [AVISO] Nao foi possivel limpar fornecedores existentes: {e}")

    print(
        f"[OK] Correcao de fornecedores existentes: {corrigidos_por_documento} por CNPJ/CPF, "
        f"{corrigidos_mascarados} mascarados, {documentos_invalidos} documentos invalidos limpos."
    )


def normalizar_base_url_api(base_url):
    """Normaliza a URL base da API."""
    base_url = limpar_valor(base_url) or API_CEP_BASE_URL_PADRAO
    base_url = base_url.rstrip('/')
    if not base_url.endswith('/api'):
        base_url = f"{base_url}/api"
    return base_url


def extrair_cookies_de_texto(valor):
    """Extrai cookies de texto no formato nome=valor, nome: valor ou nome -> valor."""
    valor = limpar_valor(valor)
    if not valor:
        return {}

    texto = str(valor).strip()
    cookies = {}
    for parte in texto.replace('\r', '\n').replace('\n', ';').split(';'):
        parte = parte.strip()
        if not parte:
            continue

        if '->' in parte:
            nome, conteudo = parte.split('->', 1)
        elif '=' in parte:
            nome, conteudo = parte.split('=', 1)
        elif ':' in parte:
            nome, conteudo = parte.split(':', 1)
        else:
            continue

        nome = nome.strip()
        conteudo = conteudo.strip().strip('"').strip("'")
        if nome and conteudo:
            cookies[nome] = conteudo

    return cookies


def extrair_cookie_nomeado(nome_cookie, valor):
    """Aceita cookie puro ou texto como nome=... / nome -> ..."""
    valor = limpar_valor(valor)
    if not valor:
        return None

    cookies = extrair_cookies_de_texto(valor)
    for nome, conteudo in cookies.items():
        if nome.lower() == nome_cookie.lower():
            return conteudo

    return str(valor).strip().strip('"').strip("'") or None


def solicitar_config_api_cep():
    """Retorna dados padrao de acesso da API de CEP."""
    base_url = normalizar_base_url_api(API_CEP_BASE_URL_PADRAO)
    print(f"[API CEP] Usando URL padrao: {base_url}")
    print(f"[API CEP] Usando usuario padrao: {API_CEP_USUARIO_PADRAO}")
    return {
        'base_url': base_url,
        'cookies_api': {},
        'usuario': API_CEP_USUARIO_PADRAO,
        'senha': API_CEP_SENHA_PADRAO,
    }


def autenticar_api_cep(config_api):
    """Autentica na API de CEP mantendo cookies na sessao."""
    base_url = config_api['base_url']
    session = requests.Session()

    cookies_api = config_api.get('cookies_api') or {}
    if cookies_api:
        for nome, conteudo in cookies_api.items():
            session.cookies.set(nome, conteudo)
        print(f"[OK] API de CEP configurada com cookies: {', '.join(cookies_api.keys())}.")
        return session

    try:
        response = session.post(
            f"{base_url}/login",
            json={
                "data": {
                    "usuario": config_api['usuario'],
                    "senha": config_api['senha'],
                }
            },
            timeout=API_TIMEOUT
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 401:
            raise RuntimeError(
                "Login da API de CEP recusado (401). "
                "Confira usuario/senha ou cole um cookie jwt valido no prompt anterior."
            ) from e
        raise
    print("[OK] API de CEP autenticada.")
    return session


def validar_api_cep(session, base_url):
    """Valida se a API de CEP esta acessivel antes da conversao."""
    try:
        response = session.get(
            f"{base_url}/cep/busca",
            params={
                "search": "01001000",
                "page": 1,
                "pageSize": 1,
            },
            timeout=API_TIMEOUT
        )
        response.raise_for_status()
        resultado = response.json()
        if external_lookup_indisponivel(resultado):
            print("[AVISO] API de CEP respondeu, mas ViaCEP esta indisponivel no momento.")
        print("[OK] API de CEP respondeu ao endpoint /cep/busca.")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"Nao foi possivel conectar na API de CEP em {base_url}. "
            "Confira URL, porta e se o servico esta rodando."
        ) from e
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 401:
            detalhe = "Cookie/login ausente ou expirado."
        elif status == 403:
            detalhe = "Usuario sem permissao cep:listar/cep:criar ou problema de licenca."
        else:
            detalhe = f"HTTP {status}."
        raise RuntimeError(f"API de CEP recusou a validacao: {detalhe}") from e
    except Exception as e:
        raise RuntimeError(f"Falha ao validar API de CEP em {base_url}: {e}") from e


def external_lookup_indisponivel(resultado):
    """Retorna True quando a API informa falha temporaria do provedor externo."""
    external_lookup = (resultado or {}).get("externalLookup") or {}
    status = str(external_lookup.get("status") or "").strip().lower()
    return status in {"unavailable", "timeout"}


def aguardar_retry_cep(tentativa):
    """Pequena espera entre tentativas de CEP sem travar demais a conversao."""
    if API_CEP_ESPERA_RETRY <= 0:
        return
    time.sleep(API_CEP_ESPERA_RETRY * tentativa)


def cadastrar_cep_api(session, base_url, cep):
    """Chama /cep/busca para buscar/cadastrar automaticamente o CEP."""
    cep_limpo = normalizar_cep(cep)
    if not cep_limpo:
        return None

    ultimo_erro = None
    for tentativa in range(1, API_CEP_TENTATIVAS + 1):
        try:
            response = session.get(
                f"{base_url}/cep/busca",
                params={
                    "search": cep_limpo,
                    "page": 1,
                    "pageSize": 10,
                },
                timeout=API_CEP_TIMEOUT
            )

            if response.status_code >= 500:
                raise CepApiErroServidor(
                    f"HTTP {response.status_code} retornado pela API. "
                    "Se a correcao do backend ja foi feita, publique/reinicie a API."
                )

            response.raise_for_status()
            resultado = response.json()
            dados = resultado.get("data") or []
            if dados:
                return dados[0]
            if external_lookup_indisponivel(resultado):
                raise CepApiIndisponivel("ViaCEP indisponivel informado pela API")
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, CepApiIndisponivel) as e:
            ultimo_erro = e
            if tentativa < API_CEP_TENTATIVAS:
                aguardar_retry_cep(tentativa)
                continue
            raise CepApiIndisponivel(
                f"{cep_limpo} indisponivel apos {API_CEP_TENTATIVAS} tentativas: {ultimo_erro}"
            ) from e

    return None


def garantir_ceps_api(registros, cursor_web, session_api, base_url_api, contexto, campos_cep):
    """
    Cadastra CEPs pela API antes do insert.
    Se a API nao encontrar o CEP, o registro fica sem CEP para nao violar FK.
    CEPs ja existentes no Web nao consomem API.
    """
    if not registros:
        return

    ceps = set()
    for registro in registros:
        for campo in campos_cep:
            cep = normalizar_cep(registro.get(campo['cep']))
            registro[campo['cep']] = cep
            if cep:
                ceps.add(cep)

    ceps = sorted(ceps)
    if not ceps:
        return

    existentes_web = buscar_ceps_existentes_web(cursor_web, ceps)
    ceps_api = [cep for cep in ceps if cep not in existentes_web]
    usar_api = bool(session_api and base_url_api)
    if not usar_api:
        ceps_api = []

    cache = {}
    cadastrados = 0
    nao_encontrados = 0
    indisponiveis = 0
    erros = 0

    if usar_api:
        print(
            f"[...] CEPs {contexto}: {len(ceps)} unicos, "
            f"{len(existentes_web)} ja existem no Web, {len(ceps_api)} serao consultados na API..."
        )
    else:
        print(
            f"[...] CEPs {contexto}: {len(ceps)} unicos, "
            f"{len(existentes_web)} ja existem no Web, {len(ceps) - len(existentes_web)} sem cadastro no Web. "
            "API pulada."
        )
    for i, cep in enumerate(ceps_api, start=1):
        try:
            dados = cadastrar_cep_api(session_api, base_url_api, cep)
            cache[cep] = dados
            if dados:
                cadastrados += 1
            else:
                nao_encontrados += 1
        except CepApiErroServidor as e:
            cache[cep] = None
            indisponiveis += 1
            if indisponiveis <= 5:
                print(f"  [AVISO] API CEP retornou erro de servidor para {cep}: {e}")
            elif indisponiveis == 6:
                print("  ... suprimindo demais avisos de erro de servidor da API CEP")
        except CepApiIndisponivel as e:
            cache[cep] = None
            indisponiveis += 1
            if indisponiveis <= 5:
                print(f"  [AVISO] API/ViaCEP indisponivel para {cep}: {e}")
            elif indisponiveis == 6:
                print("  ... suprimindo demais avisos de indisponibilidade da API/ViaCEP")
        except Exception as e:
            cache[cep] = None
            erros += 1
            if erros <= 10:
                print(f"  [AVISO] API CEP falhou para {cep}: {e}")
            elif erros == 11:
                print("  ... suprimindo demais avisos da API de CEP")

        if i % 50 == 0 or i == len(ceps_api):
            print(f"  CEPs API: {i}/{len(ceps_api)} processados...")

    sem_cep = 0
    for registro in registros:
        for campo in campos_cep:
            campo_cep = campo['cep']
            cep = normalizar_cep(registro.get(campo_cep))
            registro[campo_cep] = cep
            if not cep:
                continue

            if cep in existentes_web:
                continue

            dados = cache.get(cep)
            if not dados:
                registro[campo_cep] = None
                sem_cep += 1
                continue

            cd_cidade_api = dados.get('cd_cidade')
            if cd_cidade_api and campo.get('cidade'):
                registro[campo['cidade']] = cd_cidade_api
            if campo.get('bairro'):
                registro[campo['bairro']] = registro.get(campo['bairro']) or dados.get('nm_bairro')
            if campo.get('endereco'):
                registro[campo['endereco']] = registro.get(campo['endereco']) or dados.get('nm_rua')

    if usar_api:
        print(
            f"[OK] CEP {contexto}: {len(existentes_web)} ja existiam no Web; "
            f"API: {cadastrados} encontrados/cadastrados, "
            f"{nao_encontrados} nao encontrados, {indisponiveis} indisponiveis para tentar depois, "
            f"{erros} com erro, {sem_cep} campos de CEP removidos."
        )
    else:
        print(
            f"[OK] CEP {contexto}: {len(existentes_web)} ja existiam no Web; "
            f"API pulada; {sem_cep} campos de CEP removidos por nao existirem no Web."
        )


def garantir_ceps_fornecedor_api(registros, cursor_web, session_api, base_url_api):
    garantir_ceps_api(
        registros,
        cursor_web,
        session_api,
        base_url_api,
        'fornecedores',
        [
            {
                'cep': 'cep',
                'cidade': 'cd_cidade',
                'bairro': 'bairro',
                'endereco': 'endereco',
            }
        ]
    )


def garantir_ceps_cliente_api(registros, cursor_web, session_api, base_url_api):
    garantir_ceps_api(
        registros,
        cursor_web,
        session_api,
        base_url_api,
        'clientes',
        [
            {
                'cep': 'cep',
                'cidade': 'cd_cidade',
                'bairro': 'bairro',
                'endereco': 'endereco',
            },
            {
                'cep': 'cep_trab',
                'cidade': 'cd_cidade_trab',
                'bairro': 'bairro_trab',
                'endereco': 'endereco_trab',
            },
        ]
    )


def buscar_ceps_existentes_web(cursor_web, ceps):
    """Busca CEPs ja cadastrados no Web."""
    ceps = sorted({cep for cep in ceps if cep})
    if not ceps:
        return set()

    cursor_web.execute("SAVEPOINT sp_buscar_ceps")
    try:
        placeholders = ', '.join(['%s'] * len(ceps))
        cursor_web.execute(f"SELECT cep FROM cep WHERE cep IN ({placeholders})", ceps)
        existentes = {
            normalizar_cep(row[0])
            for row in cursor_web.fetchall()
            if normalizar_cep(row[0])
        }
        cursor_web.execute("RELEASE SAVEPOINT sp_buscar_ceps")
        return existentes
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_buscar_ceps")
            cursor_web.execute("RELEASE SAVEPOINT sp_buscar_ceps")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar CEPs existentes: {e}")
        return set()


def garantir_ceps_web(cursor_web, registros, contexto):
    """
    Garante CEPs necessarios para FK. Se nao houver cidade para cadastrar o CEP,
    o CEP do registro fica None para o insert nao falhar.
    """
    if not registros:
        return

    candidatos = {}
    registros_por_cep = {}
    for registro in registros:
        cep = normalizar_cep(registro.get('cep'))
        registro['cep'] = cep
        if not cep:
            continue

        registros_por_cep.setdefault(cep, []).append(registro)
        atual = candidatos.get(cep)
        if atual is None or (not atual.get('cd_cidade') and registro.get('cd_cidade')):
            candidatos[cep] = {
                'cd_cidade': registro.get('cd_cidade'),
                'nm_bairro': limitar_texto(registro.get('bairro'), 200),
                'nm_rua': limitar_texto(registro.get('endereco'), 200),
            }

    if not candidatos:
        return

    existentes = buscar_ceps_existentes_web(cursor_web, candidatos.keys())
    inseridos = 0
    anulados = 0
    erros = 0

    for cep, dados in candidatos.items():
        if cep in existentes:
            continue

        if not dados.get('cd_cidade'):
            for registro in registros_por_cep.get(cep, []):
                registro['cep'] = None
                anulados += 1
            continue

        try:
            cursor_web.execute("SAVEPOINT sp_inserir_cep")
            cursor_web.execute(
                """
                INSERT INTO cep (cep, cd_cidade, nm_bairro, nm_rua)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (cep) DO NOTHING
                """,
                (
                    cep,
                    dados.get('cd_cidade'),
                    dados.get('nm_bairro'),
                    dados.get('nm_rua'),
                )
            )
            cursor_web.execute("RELEASE SAVEPOINT sp_inserir_cep")
            inseridos += 1
            existentes.add(cep)
        except Exception as e:
            try:
                cursor_web.execute("ROLLBACK TO SAVEPOINT sp_inserir_cep")
                cursor_web.execute("RELEASE SAVEPOINT sp_inserir_cep")
            except Exception:
                pass

            erros += 1
            for registro in registros_por_cep.get(cep, []):
                registro['cep'] = None
                anulados += 1
            if erros <= 10:
                print(f"  [AVISO] CEP {cep} nao cadastrado para {contexto}: {e}")
            elif erros == 11:
                print("  ... suprimindo demais avisos de CEP")

    print(f"[OK] CEPs {contexto}: {inseridos} cadastrados, {anulados} registros sem CEP por falta/erro de cidade.")


def buscar_usuarios_existentes_web(cursor_web):
    """Busca usuarios existentes para evitar violacao de FK em cliente."""
    cursor_web.execute("SAVEPOINT sp_usuarios")
    try:
        cursor_web.execute("SELECT cd_usuario FROM usuario")
        usuarios = {row[0] for row in cursor_web.fetchall()}
        cursor_web.execute("RELEASE SAVEPOINT sp_usuarios")
        return usuarios
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_usuarios")
            cursor_web.execute("RELEASE SAVEPOINT sp_usuarios")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar usuarios existentes: {e}")
        return set()


# Codigos SQLSTATE que indicam problema estrutural/global: repetir item a item
# so multiplica o tempo de execucao, porque todos os registros vao falhar igual.
SQLSTATE_ERRO_GLOBAL = {
    '42P01',  # undefined_table
    '42703',  # undefined_column
    '42804',  # datatype_mismatch
    '42883',  # undefined_function
    '42P10',  # invalid_column_reference
    '3F000',  # invalid_schema_name
    '42601',  # syntax_error
}
# FKs de configuracao: quando a chave que falta e a mesma para o lote inteiro
# (tabela de preco, tenant, empresa), o erro tambem e global.
FK_CONFIGURACAO_GLOBAL = {
    'fk_produto_preco_tabela_tenant',
    'fk_produto_preco_tenant',
    'fk_produto_tenant',
    'fk_empresa_tenant',
    'fk_tabela_preco_tenant',
}


def _detalhe_erro_pg(erro):
    """Extrai (sqlstate, nome_constraint) de um erro do psycopg, quando disponivel."""
    diag = getattr(erro, 'diag', None)
    sqlstate = getattr(diag, 'sqlstate', None) or getattr(erro, 'pgcode', None)
    constraint = getattr(diag, 'constraint_name', None)
    if sqlstate is None or constraint is None:
        texto = str(erro)
        if sqlstate is None:
            match = re.search(r"'C':\s*'([0-9A-Z]{5})'", texto)
            sqlstate = match.group(1) if match else None
        if constraint is None:
            match = re.search(r"'n':\s*'([^']+)'", texto)
            constraint = match.group(1) if match else None
    return sqlstate, constraint


def erro_e_global(erro):
    """
    Diz se o erro tem causa unica para o lote inteiro. Nesse caso o fallback
    item a item e inutil e a etapa deve ser interrompida com a causa raiz.
    """
    sqlstate, constraint = _detalhe_erro_pg(erro)
    if sqlstate in SQLSTATE_ERRO_GLOBAL:
        return True, f"SQLSTATE {sqlstate}"
    if constraint and constraint in FK_CONFIGURACAO_GLOBAL:
        return True, f"chave estrangeira de configuracao {constraint}"
    return False, None


def inserir_registros_web(
    cursor_web,
    tabela_web,
    registros,
    campo_chave,
    savepoint_nome,
    ignorar_conflito=False
):
    """
    Insere registros no PostgreSQL em lotes, com fallback por registro se um lote falhar.
    Erros de causa global interrompem a etapa em vez de repetir item a item.
    """
    if not registros:
        return 0, 0, []

    total = len(registros)
    inseridos = 0
    erros = 0
    erros_detalhe = []
    inicio = time.monotonic()
    ultimo_log_tempo = inicio
    ultimo_log_qtd = 0

    for inicio_lote, lote in lotes(registros, TAMANHO_LOTE_GERAL):
        fim_lote = inicio_lote + len(lote)
        try:
            cursor_web.execute(f"SAVEPOINT {savepoint_nome}_lote")
            inserir_registros_multi_sem_savepoint(
                cursor_web, tabela_web, lote, ignorar_conflito=ignorar_conflito
            )
            cursor_web.execute(f"RELEASE SAVEPOINT {savepoint_nome}_lote")
            inseridos += len(lote)
        except Exception as e:
            try:
                cursor_web.execute(f"ROLLBACK TO SAVEPOINT {savepoint_nome}_lote")
                cursor_web.execute(f"RELEASE SAVEPOINT {savepoint_nome}_lote")
            except Exception:
                pass

            global_, motivo = erro_e_global(e)
            if global_:
                raise ErroConfiguracaoConversao(
                    f"Lote {inicio_lote + 1}-{fim_lote} de {tabela_web} falhou por causa global "
                    f"({motivo}). O fallback item a item foi cancelado porque todos os registros "
                    f"falhariam igual. Causa raiz: {e}"
                )

            print(
                f"  [AVISO] Lote {inicio_lote + 1}-{fim_lote} de {tabela_web} "
                "falhou; tentando item a item...",
                flush=True
            )
            for registro in lote:
                chave = registro.get(campo_chave, '?')
                try:
                    cursor_web.execute(f"SAVEPOINT {savepoint_nome}")
                    inserir_registro_web_sem_savepoint(
                        cursor_web, tabela_web, registro, ignorar_conflito=ignorar_conflito
                    )
                    cursor_web.execute(f"RELEASE SAVEPOINT {savepoint_nome}")
                    inseridos += 1
                except Exception as erro_item:
                    try:
                        cursor_web.execute(f"ROLLBACK TO SAVEPOINT {savepoint_nome}")
                        cursor_web.execute(f"RELEASE SAVEPOINT {savepoint_nome}")
                    except Exception:
                        pass

                    erros += 1
                    erros_detalhe.append(f"{campo_chave}={chave}: {erro_item}")
                    if erros <= 10:
                        print(f"  [PULO] {tabela_web} {campo_chave}={chave}: {erro_item}")
                    elif erros == 11:
                        print("  ... suprimindo demais erros (serao listados no resumo)")

        agora = time.monotonic()
        if (
            fim_lote == total
            or fim_lote - ultimo_log_qtd >= LOG_GERAL_INTERVALO_REGISTROS
            or agora - ultimo_log_tempo >= LOG_GERAL_INTERVALO_SEGUNDOS
        ):
            print(
                f"  Progresso: {fim_lote}/{total} registros processados "
                f"({inseridos} inseridos, {erros} erros) em {int(agora - inicio)}s...",
                flush=True
            )
            ultimo_log_tempo = agora
            ultimo_log_qtd = fim_lote

    return inseridos, erros, erros_detalhe


def codigos_insert_com_erro(erros_detalhe, campo_chave):
    """Extrai codigos que falharam no insert para nao usar no mapa GIV -> Web."""
    codigos = set()
    padrao = re.compile(rf"{re.escape(campo_chave)}=(\d+)")
    for detalhe in erros_detalhe:
        match = padrao.search(detalhe)
        if match:
            codigos.add(int(match.group(1)))
    return codigos


def remover_mapa_com_codigos_invalidos(mapa, codigos_invalidos):
    """Remove do mapa os codigos Web que nao foram inseridos."""
    if not codigos_invalidos:
        return mapa
    return {
        cd_origem: cd_web
        for cd_origem, cd_web in mapa.items()
        if cd_web not in codigos_invalidos
    }


def inserir_registro_web_sem_savepoint(cursor_web, tabela_web, registro, ignorar_conflito=False):
    """Insere um registro assumindo que o SAVEPOINT/transacao ja esta aberto."""
    colunas = list(registro.keys())
    placeholders = ', '.join(['%s'] * len(colunas))
    colunas_str = ', '.join(quote_identificador(col) for col in colunas)
    sufixo = " ON CONFLICT DO NOTHING" if ignorar_conflito else ""
    sql = f"INSERT INTO {tabela_web} ({colunas_str}) VALUES ({placeholders}){sufixo}"
    valores = [registro[col] for col in colunas]
    cursor_web.execute(sql, valores)


def inserir_registros_multi_sem_savepoint(cursor_web, tabela_web, registros, ignorar_conflito=False):
    """Insere varios registros em um unico INSERT multi-values."""
    if not registros:
        return

    colunas = []
    for registro in registros:
        for coluna in registro.keys():
            if coluna not in colunas:
                colunas.append(coluna)

    placeholders_linha = '(' + ', '.join(['%s'] * len(colunas)) + ')'
    placeholders = ', '.join([placeholders_linha] * len(registros))
    colunas_str = ', '.join(quote_identificador(col) for col in colunas)
    sufixo = " ON CONFLICT DO NOTHING" if ignorar_conflito else ""
    sql = f"INSERT INTO {tabela_web} ({colunas_str}) VALUES {placeholders}{sufixo}"

    valores = []
    for registro in registros:
        valores.extend(registro.get(col) for col in colunas)
    cursor_web.execute(sql, valores)


def inserir_registro_web_retornando(cursor_web, tabela_web, registro, coluna_retorno):
    """Insere um registro e retorna a coluna gerada pelo PostgreSQL."""
    colunas = list(registro.keys())
    placeholders = ', '.join(['%s'] * len(colunas))
    colunas_str = ', '.join(quote_identificador(col) for col in colunas)
    sql = (
        f"INSERT INTO {tabela_web} ({colunas_str}) "
        f"VALUES ({placeholders}) RETURNING {quote_identificador(coluna_retorno)}"
    )
    valores = [registro[col] for col in colunas]
    cursor_web.execute(sql, valores)
    return cursor_web.fetchone()[0]


def lotes(lista, tamanho):
    """Gera fatias de uma lista."""
    for inicio in range(0, len(lista), tamanho):
        yield inicio, lista[inicio:inicio + tamanho]


def buscar_grupos_giv(cursor_giv, cd_empresa_giv=None):
    """Busca grupos do GIV na tabela Grupo_moda."""
    where_sql, params = filtro_empresa_giv(cursor_giv, 'Grupo_moda', 'gm', cd_empresa_giv)
    sql = """
        SELECT
            gm.cd_grupo,
            gm.ds_grupo
        FROM Grupo_moda gm
        {where_sql}
        ORDER BY gm.cd_grupo
    """.format(where_sql=where_sql)
    grupos = buscar_registros_giv(cursor_giv, sql, params if params else None)
    if grupos:
        return grupos

    print("[AVISO] Grupo_moda esta vazia; usando grupo_produto P como origem dos grupos.")
    return buscar_grupos_produto_pai_giv(cursor_giv, cd_empresa_giv)


def buscar_grupo_produto_giv(cursor_giv, cd_empresa_giv=None):
    """Busca a hierarquia antiga de grupos de produto do GIV."""
    where_sql, params = filtro_empresa_giv(cursor_giv, 'grupo_produto', 'gp', cd_empresa_giv)
    sql = """
        SELECT
            gp.cd_grupo,
            gp.ds_grupo,
            gp.id_tipo
        FROM grupo_produto gp
        {where_sql}
        ORDER BY gp.cd_grupo
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def codigo_grupo_produto(valor):
    """Mantem codigo de grupo_produto como texto, preservando zeros a esquerda."""
    valor = limpar_valor(valor)
    if valor is None:
        return None
    return str(valor)


def encontrar_pai_grupo_produto(cd_grupo, codigos_pai):
    """Encontra o maior prefixo P para um codigo de grupo_produto."""
    codigo = codigo_grupo_produto(cd_grupo)
    if not codigo:
        return None
    if codigo in codigos_pai:
        return codigo

    for tamanho in range(len(codigo) - 1, 0, -1):
        candidato = codigo[:tamanho]
        if candidato in codigos_pai:
            return candidato
    return None


def buscar_grupos_produto_pai_giv(cursor_giv, cd_empresa_giv=None):
    """Usa grupo_produto id_tipo=P como grupos quando Grupo_moda estiver vazia."""
    grupos = []
    for reg in buscar_grupo_produto_giv(cursor_giv, cd_empresa_giv):
        if valor_flag(reg.get('id_tipo'), '') != 'P':
            continue
        grupos.append({
            'cd_grupo': codigo_grupo_produto(reg.get('cd_grupo')),
            'ds_grupo': reg.get('ds_grupo'),
        })
    return grupos


def buscar_subgrupos_produto_filho_giv(cursor_giv, cd_empresa_giv=None):
    """Usa grupo_produto id_tipo<>P como subgrupos quando subgrupo_moda estiver vazia."""
    grupos_produto = buscar_grupo_produto_giv(cursor_giv, cd_empresa_giv)
    codigos_pai = {
        codigo_grupo_produto(reg.get('cd_grupo'))
        for reg in grupos_produto
        if valor_flag(reg.get('id_tipo'), '') == 'P'
    }

    subgrupos = []
    for reg in grupos_produto:
        if valor_flag(reg.get('id_tipo'), '') == 'P':
            continue
        cd_subgrupo = codigo_grupo_produto(reg.get('cd_grupo'))
        cd_grupo_pai = encontrar_pai_grupo_produto(cd_subgrupo, codigos_pai)
        subgrupos.append({
            'cd_subgrupo': cd_subgrupo,
            'cd_grupo': cd_grupo_pai,
            'ds_subgrupo': reg.get('ds_grupo'),
        })
    return subgrupos


def inferir_cd_genero_moda_por_grupo(ds_grupo):
    """Infere genero_moda a partir da descricao do grupo_produto pai."""
    chave = chave_nome_produto(ds_grupo) or ''
    if 'INF' in chave or 'BEBE' in chave:
        return 3
    if 'FEM' in chave:
        return 1
    if 'MASC' in chave:
        return 2
    return 4


def buscar_genero_por_grupo_produto_giv(cursor_giv, cd_empresa_giv=None):
    """Mapeia cd_grupo de grupo_produto para cd_genero_moda inferido."""
    grupos_produto = buscar_grupo_produto_giv(cursor_giv, cd_empresa_giv)
    codigos_pai = {
        codigo_grupo_produto(reg.get('cd_grupo'))
        for reg in grupos_produto
        if valor_flag(reg.get('id_tipo'), '') == 'P'
    }
    genero_por_pai = {}
    for reg in grupos_produto:
        if valor_flag(reg.get('id_tipo'), '') != 'P':
            continue
        cd_grupo = codigo_grupo_produto(reg.get('cd_grupo'))
        genero_por_pai[cd_grupo] = inferir_cd_genero_moda_por_grupo(reg.get('ds_grupo'))

    mapa = {}
    for reg in grupos_produto:
        cd_grupo = codigo_grupo_produto(reg.get('cd_grupo'))
        cd_pai = encontrar_pai_grupo_produto(cd_grupo, codigos_pai)
        cd_genero = genero_por_pai.get(cd_pai)
        if cd_genero is not None:
            mapa[cd_grupo] = cd_genero
            mapa[reg.get('cd_grupo')] = cd_genero
    return mapa


def converter_grupo(registro_giv, tenant_id, cd_empresa, cd_grupo_web):
    """Converte Grupo_moda (GIV) para Grupo (Web)."""
    registro_web = {
        'cd_grupo':  cd_grupo_web,
        'ds_grupo':  registro_giv.get('ds_grupo'),
        'tenant_id': tenant_id,
        'cd_empresa': cd_empresa,
    }
    return limpar_registro(registro_web)


def mapear_tipo_fornecedor(id_tipo_fornecedor):
    """Converte P/T/S do GIV para os indicadores separados do Web."""
    tipo = limpar_valor(id_tipo_fornecedor)
    tipo = str(tipo).upper() if tipo is not None else ''

    return {
        'id_fornecedor_produto': 'S' if tipo == 'P' else 'N',
        'id_fornecedor_transporte': 'S' if tipo == 'T' else 'N',
        'id_fornecedor_servico': 'S' if tipo == 'S' else 'N',
    }


def buscar_fornecedores_giv(cursor_giv, cd_empresa_giv=None):
    """Busca fornecedores do GIV."""
    where_sql = ""
    params = []
    if cd_empresa_giv is not None and tabela_giv_tem_coluna(cursor_giv, 'fornecedor', 'cd_empresa'):
        where_sql = "WHERE f.cd_empresa = ?"
        params.append(cd_empresa_giv)

    sql = """
        SELECT
            f.cd_fornecedor,
            f.razao_social,
            f.nm_fornecedor,
            f.tp_pessoa,
            f.cgc,
            f.inscricao_estadual,
            f.id_tipo_fornecedor,
            f.nm_representante,
            f.cd_cidade,
            f.cep,
            f.endereco,
            f.ds_numero,
            f.bairro,
            f.fone,
            f.email,
            f.observacao,
            f.site
        FROM fornecedor f
        {where_sql}
        ORDER BY f.cd_fornecedor
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


# Regra oficial do Web para endereco sem numero, confirmada em
# projeto_v1/src/adapters/outbound/repositories/fornecedorRepository.ts:
#   createData.ds_numero = String(nr_endereco ?? '')
# Ou seja: string vazia, nunca NULL (a coluna e NOT NULL VarChar(15) tanto em
# fornecedor quanto em usuario e empresa). Nao existe "S/N" no cadastro.
DS_NUMERO_SEM_NUMERO_WEB = ''
DS_NUMERO_TAMANHO_MAXIMO_WEB = 15

# Extrai o numero apenas quando ele esta claramente no fim do logradouro,
# separado por espaco/ponto/virgula/traco, e o restante do logradouro nao
# termina em digito. Casos duvidosos ficam sem numero, preservando o endereco.
_RE_NUMERO_NO_FIM_DO_ENDERECO = re.compile(
    r'^(?P<logradouro>.*[^\d\s.,\-])[\s.,\-]+(?P<numero>\d{1,6})\s*$'
)


def normalizar_ds_numero_web(ds_numero, endereco=None):
    """
    Devolve (ds_numero, endereco, extraido) no padrao do Web.
    - ds_numero nunca e None (coluna NOT NULL): ausencia vira string vazia.
    - Quando o GIV nao traz numero, tenta extrair do fim do logradouro somente
      em caso de alta confianca; o logradouro original e preservado nos demais.
    """
    numero = limpar_valor(ds_numero)
    extraido = False

    if numero is None and endereco:
        casamento = _RE_NUMERO_NO_FIM_DO_ENDERECO.match(str(endereco).strip())
        if casamento:
            numero = casamento.group('numero')
            endereco = casamento.group('logradouro').strip(' .,-')
            extraido = True

    if numero is None:
        numero = DS_NUMERO_SEM_NUMERO_WEB

    numero = str(numero).strip()[:DS_NUMERO_TAMANHO_MAXIMO_WEB]
    return numero, endereco, extraido


def converter_fornecedor(registro_giv, cidades_giv, cidades_web, cd_fornecedor_web, tenant_id):
    """Converte fornecedor (GIV) para fornecedor (Web)."""
    razao_social = razao_social_fornecedor_origem(registro_giv)
    fantasia = fantasia_fornecedor_origem(registro_giv)

    cd_cidade_web = resolver_cidade_trab(
        registro_giv.get('cd_cidade'),
        cidades_giv,
        cidades_web,
        f"fornecedor cd_origem={registro_giv.get('cd_fornecedor')}"
    )
    tipos = mapear_tipo_fornecedor(registro_giv.get('id_tipo_fornecedor'))
    ddd_cidade = resolver_ddd_cidade_giv(registro_giv.get('cd_cidade'), cidades_giv)

    ds_numero, endereco, numero_extraido = normalizar_ds_numero_web(
        registro_giv.get('ds_numero'),
        limpar_valor(registro_giv.get('endereco'))
    )

    registro_web = {
        'cd_fornecedor': cd_fornecedor_web,
        'tenant_id': tenant_id,
        'id_status': ID_STATUS_FORNECEDOR_PADRAO,
        'razao_social': razao_social,
        'nm_fantasia_fornecedor': fantasia,
        'tp_pessoa': registro_giv.get('tp_pessoa'),
        'id_cadastro_nacional': normalizar_cadastro_nacional(registro_giv.get('cgc')),
        'id_cadastro_estadual': registro_giv.get('inscricao_estadual'),
        'id_fornecedor_produto': tipos['id_fornecedor_produto'],
        'id_fornecedor_transporte': tipos['id_fornecedor_transporte'],
        'id_fornecedor_servico': tipos['id_fornecedor_servico'],
        'nm_representante': registro_giv.get('nm_representante'),
        'cd_cidade': cd_cidade_web,
        'cep': normalizar_cep(registro_giv.get('cep')),
        'endereco': endereco,
        'bairro': registro_giv.get('bairro'),
        'fone': normalizar_telefone_web(registro_giv.get('fone'), ddd=ddd_cidade),
        'celular': normalizar_celular_web(registro_giv.get('fone'), ddd=ddd_cidade),
        'email': registro_giv.get('email'),
        'observacao': registro_giv.get('observacao'),
        'site': registro_giv.get('site'),
    }
    registro_web = limpar_registro(registro_web)
    # Gravado depois de limpar_registro porque a coluna e NOT NULL e limpar_valor
    # converteria a string vazia oficial do Web em None.
    registro_web['ds_numero'] = ds_numero
    registro_web['_numero_extraido_do_endereco'] = numero_extraido
    return registro_web


def processar_grupos(cursor_giv, cursor_web, tabela_web_grupo, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[INFO] Grupo: registros existentes no Web nao serao usados para pular a importacao.")

    sequence_grupo = buscar_sequence_coluna_web(cursor_web, tabela_web_grupo, 'cd_grupo')
    max_grupo = sincronizar_sequence_com_max(cursor_web, tabela_web_grupo, 'cd_grupo', sequence_grupo)
    print(f"[OK] Sequence de grupo: {sequence_grupo} (sincronizada com max={max_grupo}).")

    print()
    print("[...] Buscando grupos do banco GIV...")
    grupos_giv = buscar_grupos_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(grupos_giv)} grupos encontrados no GIV.")

    grupos_web = []
    mapa_grupos, reaproveitados, pendentes_por_chave, grupos_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_grupo,
        'cd_grupo',
        'ds_grupo',
        grupos_giv,
        'cd_grupo',
        lambda reg: limpar_valor(reg.get('ds_grupo')) or f"GRUPO {reg.get('cd_grupo')}",
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    codigos_grupo = iter(reservar_valores_sequence(cursor_web, sequence_grupo, len(grupos_para_inserir)))
    for chave_nome in grupos_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        cd_grupo_web = next(codigos_grupo)
        for reg in pendente['regs']:
            mapa_grupos[reg.get('cd_grupo')] = cd_grupo_web
            mapa_grupos[codigo_grupo_produto(reg.get('cd_grupo'))] = cd_grupo_web
        grupos_web.append(converter_grupo(pendente['regs'][0], tenant_id, cd_empresa, cd_grupo_web))

    for reg in grupos_giv:
        cd_grupo_giv = reg.get('cd_grupo')
        cd_grupo_web = mapa_grupos.get(cd_grupo_giv)
        if cd_grupo_web is None:
            cd_grupo_web = mapa_grupos.get(normalizar_codigo_cidade(cd_grupo_giv))
        if cd_grupo_web is not None:
            mapa_grupos[codigo_grupo_produto(cd_grupo_giv)] = cd_grupo_web

    if reaproveitados:
        print(f"[OK] {reaproveitados} grupos reaproveitados por descricao no mesmo tenant_id/cd_empresa.")
    print(f"[OK] {len(grupos_web)} grupos novos para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_grupo, grupos_web, 'grupo')

    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_grupo,
        grupos_web,
        'cd_grupo',
        'sp_grupo'
    )

    cds_com_erro = set()
    for detalhe in erros_detalhe:
        match = re.search(r"cd_grupo=(\d+)", detalhe)
        if match:
            cds_com_erro.add(int(match.group(1)))

    mapa_grupos_ok = {
        cd_origem: cd_web
        for cd_origem, cd_web in mapa_grupos.items()
        if cd_web not in cds_com_erro
    }

    return {
        'tabela': 'Grupo',
        'lidos': len(grupos_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
        'mapa_grupos': mapa_grupos_ok,
    }


def buscar_marcas_giv(cursor_giv, cd_empresa_giv=None):
    """Busca marcas do GIV."""
    where_sql, params = filtro_empresa_giv(cursor_giv, 'marca', 'm', cd_empresa_giv)
    sql = """
        SELECT
            m.cd_marca,
            m.ds_marca,
            m.pr_comissao_marca
        FROM marca m
        {where_sql}
        ORDER BY m.cd_marca
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def converter_marca(registro_giv, tenant_id, cd_marca_web):
    """Converte marca do GIV para marca do Web."""
    cd_marca_origem = registro_giv.get('cd_marca')
    ds_marca = limpar_valor(registro_giv.get('ds_marca')) or f"MARCA {cd_marca_origem}"
    return limpar_registro({
        'cd_marca': cd_marca_web,
        'id_status': 'A',
        'ds_marca': ds_marca,
        'pr_comissao': registro_giv.get('pr_comissao_marca'),
        'tenant_id': tenant_id,
    })


def processar_marcas(cursor_giv, cursor_web, tabela_web_marca, tenant_id, cd_empresa_giv=None):
    sequence_marca = buscar_sequence_coluna_web(cursor_web, tabela_web_marca, 'cd_marca')
    max_marca = sincronizar_sequence_com_max(cursor_web, tabela_web_marca, 'cd_marca', sequence_marca)
    print()
    print(f"[OK] Sequence de marca: {sequence_marca} (sincronizada com max={max_marca}).")

    print()
    print("[...] Buscando marcas do banco GIV...")
    marcas_giv = buscar_marcas_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(marcas_giv)} marcas encontradas no GIV.")

    marcas_web = []
    mapa, reaproveitados, pendentes_por_chave, marcas_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_marca,
        'cd_marca',
        'ds_marca',
        marcas_giv,
        'cd_marca',
        lambda reg: limpar_valor(reg.get('ds_marca')) or f"MARCA {reg.get('cd_marca')}",
        tenant_id=tenant_id
    )
    codigos_marca = iter(reservar_valores_sequence(cursor_web, sequence_marca, len(marcas_para_inserir)))
    for chave_nome in marcas_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        cd_marca_web = next(codigos_marca)
        for reg in pendente['regs']:
            mapa[reg.get('cd_marca')] = cd_marca_web
            mapa[normalizar_codigo_cidade(reg.get('cd_marca'))] = cd_marca_web
        marcas_web.append(converter_marca(pendente['regs'][0], tenant_id, cd_marca_web))

    if reaproveitados:
        print(f"[OK] {reaproveitados} marcas reaproveitadas por descricao no mesmo tenant_id.")
    print(f"[OK] {len(marcas_web)} marcas novas para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_marca, marcas_web, 'marca')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_marca,
        marcas_web,
        'cd_marca',
        'sp_marca'
    )

    return {
        'tabela': 'marca',
        'lidos': len(marcas_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_cores_giv(cursor_giv, cd_empresa_giv=None):
    """Busca cores do GIV."""
    where_sql, params = filtro_empresa_giv(cursor_giv, 'cor', 'c', cd_empresa_giv)
    sql = """
        SELECT
            c.cd_cor,
            c.ds_cor
        FROM cor c
        {where_sql}
        ORDER BY c.cd_cor
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def converter_cor(registro_giv, tenant_id, cd_cor_web):
    """Converte cor do GIV para cor do Web."""
    cd_cor_origem = registro_giv.get('cd_cor')
    ds_cor = limpar_valor(registro_giv.get('ds_cor')) or f"COR {cd_cor_origem}"
    return limpar_registro({
        'cd_cor': cd_cor_web,
        'id_status': 'A',
        'ds_cor': ds_cor,
        'tenant_id': tenant_id,
    })


def processar_cores(cursor_giv, cursor_web, tabela_web_cor, tenant_id, cd_empresa_giv=None):
    sequence_cor = buscar_sequence_coluna_web(cursor_web, tabela_web_cor, 'cd_cor')
    max_cor = sincronizar_sequence_com_max(cursor_web, tabela_web_cor, 'cd_cor', sequence_cor)
    print()
    print(f"[OK] Sequence de cor: {sequence_cor} (sincronizada com max={max_cor}).")

    print()
    print("[...] Buscando cores do banco GIV...")
    cores_giv = buscar_cores_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(cores_giv)} cores encontradas no GIV.")

    cores_web = []
    mapa, reaproveitados, pendentes_por_chave, cores_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_cor,
        'cd_cor',
        'ds_cor',
        cores_giv,
        'cd_cor',
        lambda reg: limpar_valor(reg.get('ds_cor')) or f"COR {reg.get('cd_cor')}",
        tenant_id=tenant_id
    )
    codigos_cor = iter(reservar_valores_sequence(cursor_web, sequence_cor, len(cores_para_inserir)))
    for chave_nome in cores_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        cd_cor_web = next(codigos_cor)
        for reg in pendente['regs']:
            mapa[reg.get('cd_cor')] = cd_cor_web
            mapa[normalizar_codigo_cidade(reg.get('cd_cor'))] = cd_cor_web
        cores_web.append(converter_cor(pendente['regs'][0], tenant_id, cd_cor_web))

    if reaproveitados:
        print(f"[OK] {reaproveitados} cores reaproveitadas por descricao no mesmo tenant_id.")
    print(f"[OK] {len(cores_web)} cores novas para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_cor, cores_web, 'cor')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_cor,
        cores_web,
        'cd_cor',
        'sp_cor'
    )

    return {
        'tabela': 'cor',
        'lidos': len(cores_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
        'mapa_cores': mapa,
    }


def buscar_departamentos_giv(cursor_giv, cd_empresa_giv=None):
    """Busca generos de moda do GIV para departamento."""
    where_sql, params = filtro_empresa_giv(cursor_giv, 'genero_moda', 'g', cd_empresa_giv)
    sql = """
        SELECT
            g.cd_genero_moda,
            g.ds_genero_moda
        FROM genero_moda g
        {where_sql}
        ORDER BY g.cd_genero_moda
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def converter_departamento(registro_giv, tenant_id, cd_empresa, cd_departamento_web):
    """Converte genero_moda do GIV para departamento do Web."""
    cd_departamento_origem = registro_giv.get('cd_genero_moda')
    ds_departamento = limpar_valor(registro_giv.get('ds_genero_moda')) or f"DEPARTAMENTO {cd_departamento_origem}"
    return limpar_registro({
        'cd_departamento': cd_departamento_web,
        'ds_departamento': ds_departamento,
        'tenant_id': tenant_id,
        'cd_empresa': cd_empresa,
    })


def processar_departamentos(cursor_giv, cursor_web, tabela_web_departamento, tenant_id, cd_empresa, cd_empresa_giv=None):
    sequence_departamento = buscar_sequence_coluna_web(
        cursor_web,
        tabela_web_departamento,
        'cd_departamento'
    )
    max_departamento = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_departamento,
        'cd_departamento',
        sequence_departamento
    )
    print()
    print(
        f"[OK] Sequence de departamento: {sequence_departamento} "
        f"(sincronizada com max={max_departamento})."
    )

    print()
    print("[...] Buscando generos de moda do banco GIV...")
    departamentos_giv = buscar_departamentos_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(departamentos_giv)} generos encontrados no GIV.")

    departamentos_web = []
    mapa, reaproveitados, pendentes_por_chave, departamentos_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_departamento,
        'cd_departamento',
        'ds_departamento',
        departamentos_giv,
        'cd_genero_moda',
        lambda reg: limpar_valor(reg.get('ds_genero_moda')) or f"DEPARTAMENTO {reg.get('cd_genero_moda')}",
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    codigos_departamento = iter(reservar_valores_sequence(
        cursor_web,
        sequence_departamento,
        len(departamentos_para_inserir)
    ))
    for chave_nome in departamentos_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        cd_departamento_web = next(codigos_departamento)
        for reg in pendente['regs']:
            mapa[reg.get('cd_genero_moda')] = cd_departamento_web
            mapa[normalizar_codigo_cidade(reg.get('cd_genero_moda'))] = cd_departamento_web
        departamentos_web.append(
            converter_departamento(pendente['regs'][0], tenant_id, cd_empresa, cd_departamento_web)
        )

    if reaproveitados:
        print(f"[OK] {reaproveitados} departamentos reaproveitados por descricao no mesmo tenant_id/cd_empresa.")
    print(f"[OK] {len(departamentos_web)} departamentos novos para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_departamento, departamentos_web, 'departamento')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_departamento,
        departamentos_web,
        'cd_departamento',
        'sp_departamento'
    )

    return {
        'tabela': 'departamento',
        'lidos': len(departamentos_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_subgrupos_giv(cursor_giv, cd_empresa_giv=None):
    """Busca subgrupos de moda do GIV."""
    where_sql, params = filtro_empresa_giv(cursor_giv, 'subgrupo_moda', 's', cd_empresa_giv)
    sql = """
        SELECT
            s.cd_subgrupo,
            s.cd_grupo,
            s.ds_subgrupo
        FROM subgrupo_moda s
        {where_sql}
        ORDER BY s.cd_subgrupo
    """.format(where_sql=where_sql)
    subgrupos = buscar_registros_giv(cursor_giv, sql, params if params else None)
    if subgrupos:
        return subgrupos

    print("[AVISO] subgrupo_moda esta vazia; usando grupo_produto F como origem dos subgrupos.")
    return buscar_subgrupos_produto_filho_giv(cursor_giv, cd_empresa_giv)


def buscar_mapa_grupos_giv(cursor_giv, cd_empresa_giv=None):
    """Retorna cd_grupo GIV -> dados do grupo GIV."""
    mapa = {}
    for grupo in buscar_grupos_giv(cursor_giv, cd_empresa_giv):
        cd_grupo = codigo_grupo_produto(grupo.get('cd_grupo'))
        mapa[grupo.get('cd_grupo')] = {
            'ds_grupo': limpar_valor(grupo.get('ds_grupo')),
            'chave': chave_texto(grupo.get('ds_grupo')),
        }
        mapa[cd_grupo] = {
            'ds_grupo': limpar_valor(grupo.get('ds_grupo')),
            'chave': chave_texto(grupo.get('ds_grupo')),
        }
    return mapa


def buscar_mapa_grupos_web(cursor_web, tabela_web_grupo, tenant_id, cd_empresa):
    """Retorna nome normalizado do grupo Web -> cd_grupo Web."""
    cursor_web.execute("SAVEPOINT sp_mapa_grupo_web")
    try:
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador('cd_grupo')},
                {quote_identificador('ds_grupo')}
              FROM {tabela_web_grupo}
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('cd_empresa')} = %s
            """,
            (tenant_id, cd_empresa)
        )
        mapa = {}
        for row in cursor_web.fetchall():
            chave = chave_texto(row[1])
            if chave and chave not in mapa:
                mapa[chave] = row[0]
        cursor_web.execute("RELEASE SAVEPOINT sp_mapa_grupo_web")
        return mapa
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_mapa_grupo_web")
            cursor_web.execute("RELEASE SAVEPOINT sp_mapa_grupo_web")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar grupos Web para sub_grupo: {e}")
        return {}


def buscar_chaves_sub_grupo_web(cursor_web, tabela_web_sub_grupo, tenant_id, cd_empresa):
    """Busca nomes usados por grupo para respeitar unique de sub_grupo."""
    cursor_web.execute("SAVEPOINT sp_chaves_sub_grupo")
    try:
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador('cd_grupo')},
                {quote_identificador('ds_sub_grupo')}
              FROM {tabela_web_sub_grupo}
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('cd_empresa')} = %s
            """,
            (tenant_id, cd_empresa)
        )
        nomes_por_grupo = {}
        for row in cursor_web.fetchall():
            chave = chave_nome_produto(row[1])
            if chave:
                nomes_por_grupo.setdefault(row[0], set()).add(chave)
        cursor_web.execute("RELEASE SAVEPOINT sp_chaves_sub_grupo")
        return nomes_por_grupo
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_chaves_sub_grupo")
            cursor_web.execute("RELEASE SAVEPOINT sp_chaves_sub_grupo")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar nomes existentes de sub_grupo: {e}")
        return {}


def buscar_mapa_sub_grupo_web_por_grupo_nome(cursor_web, tabela_web_sub_grupo, tenant_id, cd_empresa):
    """Busca (cd_grupo, nome normalizado) -> cd_sub_grupo para reaproveitar cadastro Web."""
    cursor_web.execute("SAVEPOINT sp_mapa_sub_grupo_web")
    try:
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador('cd_sub_grupo')},
                {quote_identificador('cd_grupo')},
                {quote_identificador('ds_sub_grupo')}
              FROM {tabela_web_sub_grupo}
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('cd_empresa')} = %s
             ORDER BY {quote_identificador('cd_sub_grupo')}
            """,
            (tenant_id, cd_empresa)
        )
        mapa = {}
        for row in cursor_web.fetchall():
            chave_nome = chave_nome_produto(row[2])
            if chave_nome:
                mapa[(row[1], chave_nome)] = row[0]
        cursor_web.execute("RELEASE SAVEPOINT sp_mapa_sub_grupo_web")
        return mapa
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_mapa_sub_grupo_web")
            cursor_web.execute("RELEASE SAVEPOINT sp_mapa_sub_grupo_web")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar sub_grupos Web por grupo/nome: {e}")
        return {}


def resolver_grupo_subgrupo(registro_giv, mapa_grupos_giv, mapa_grupos_web, mapa_grupos_convertidos=None):
    """Resolve cd_grupo Web a partir do cd_grupo sequencial do GIV."""
    cd_grupo_giv = registro_giv.get('cd_grupo')
    if mapa_grupos_convertidos and cd_grupo_giv in mapa_grupos_convertidos:
        return mapa_grupos_convertidos[cd_grupo_giv], None
    cd_grupo_texto = codigo_grupo_produto(cd_grupo_giv)
    if mapa_grupos_convertidos and cd_grupo_texto in mapa_grupos_convertidos:
        return mapa_grupos_convertidos[cd_grupo_texto], None

    grupo_giv = mapa_grupos_giv.get(cd_grupo_giv) or mapa_grupos_giv.get(cd_grupo_texto)
    if not grupo_giv:
        return None, f"grupo GIV nao encontrado cd_grupo={cd_grupo_giv}"

    cd_grupo_web = mapa_grupos_web.get(grupo_giv.get('chave'))
    if cd_grupo_web is None:
        return None, (
            f"grupo Web nao encontrado para cd_grupo_giv={cd_grupo_giv}, "
            f"ds_grupo={grupo_giv.get('ds_grupo')}"
        )

    return cd_grupo_web, None


def converter_sub_grupo(registro_giv, tenant_id, cd_empresa, cd_sub_grupo_web, cd_grupo_web):
    """Converte subgrupo_moda do GIV para sub_grupo do Web."""
    cd_subgrupo_origem = registro_giv.get('cd_subgrupo')
    ds_sub_grupo = limpar_valor(registro_giv.get('ds_subgrupo')) or f"SUBGRUPO {cd_subgrupo_origem}"
    return limpar_registro({
        'cd_sub_grupo': cd_sub_grupo_web,
        'cd_empresa': cd_empresa,
        'tenant_id': tenant_id,
        'cd_grupo': cd_grupo_web,
        'ds_sub_grupo': ds_sub_grupo,
    })


def processar_sub_grupos(
    cursor_giv,
    cursor_web,
    tabela_web_sub_grupo,
    tabela_web_grupo,
    tenant_id,
    cd_empresa,
    mapa_grupos_convertidos=None,
    cd_empresa_giv=None
):
    sequence_sub_grupo = buscar_sequence_coluna_web(
        cursor_web,
        tabela_web_sub_grupo,
        'cd_sub_grupo'
    )
    max_sub_grupo = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_sub_grupo,
        'cd_sub_grupo',
        sequence_sub_grupo
    )
    print()
    print(
        f"[OK] Sequence de sub_grupo: {sequence_sub_grupo} "
        f"(sincronizada com max={max_sub_grupo})."
    )

    print()
    print("[...] Carregando grupos GIV/Web para mapeamento de sub_grupo...")
    mapa_grupos_giv = buscar_mapa_grupos_giv(cursor_giv, cd_empresa_giv)
    mapa_grupos_web = buscar_mapa_grupos_web(cursor_web, tabela_web_grupo, tenant_id, cd_empresa)
    print(f"[OK] {len(mapa_grupos_giv)} grupos GIV e {len(mapa_grupos_web)} grupos Web carregados.")
    if mapa_grupos_convertidos:
        print(f"[OK] {len(mapa_grupos_convertidos)} grupos convertidos nesta execucao serao usados no sub_grupo.")

    print()
    print("[...] Buscando subgrupos de moda do banco GIV...")
    subgrupos_giv = buscar_subgrupos_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(subgrupos_giv)} subgrupos encontrados no GIV.")

    subgrupos_web = []
    pendentes_por_chave = {}
    subgrupos_para_inserir = []
    mapa_sub_grupos = {}
    erros_detalhe = []
    erros_mapeamento = 0
    reaproveitados = 0
    mapa_sub_grupo_web = buscar_mapa_sub_grupo_web_por_grupo_nome(
        cursor_web,
        tabela_web_sub_grupo,
        tenant_id,
        cd_empresa
    )
    for reg in subgrupos_giv:
        cd_grupo_web, erro = resolver_grupo_subgrupo(
            reg,
            mapa_grupos_giv,
            mapa_grupos_web,
            mapa_grupos_convertidos
        )
        if erro:
            erros_mapeamento += 1
            detalhe = f"cd_subgrupo={reg.get('cd_subgrupo')}: {erro}"
            erros_detalhe.append(detalhe)
            if erros_mapeamento <= 10:
                print(f"  [PULO] sub_grupo {detalhe}")
            elif erros_mapeamento == 11:
                print("  ... suprimindo demais erros de mapeamento de sub_grupo")
            continue

        reg_ajustado = dict(reg)
        ds_subgrupo = limpar_valor(reg_ajustado.get('ds_subgrupo')) or f"SUBGRUPO {reg_ajustado.get('cd_subgrupo')}"
        reg_ajustado['ds_subgrupo'] = ds_subgrupo
        chave = (cd_grupo_web, chave_nome_produto(ds_subgrupo))

        cd_sub_grupo_existente = mapa_sub_grupo_web.get(chave)
        if cd_sub_grupo_existente is not None:
            mapa_sub_grupos[reg_ajustado.get('cd_subgrupo')] = cd_sub_grupo_existente
            mapa_sub_grupos[normalizar_codigo_cidade(reg_ajustado.get('cd_subgrupo'))] = cd_sub_grupo_existente
            reaproveitados += 1
            continue

        if chave in pendentes_por_chave:
            pendentes_por_chave[chave]['regs'].append(reg_ajustado)
            reaproveitados += 1
            continue

        pendentes_por_chave[chave] = {
            'regs': [reg_ajustado],
            'cd_grupo_web': cd_grupo_web,
        }
        subgrupos_para_inserir.append(chave)

    codigos_sub_grupo = iter(reservar_valores_sequence(cursor_web, sequence_sub_grupo, len(subgrupos_para_inserir)))
    for chave in subgrupos_para_inserir:
        pendente = pendentes_por_chave[chave]
        cd_sub_grupo_web = next(codigos_sub_grupo)
        for reg in pendente['regs']:
            mapa_sub_grupos[reg.get('cd_subgrupo')] = cd_sub_grupo_web
            mapa_sub_grupos[normalizar_codigo_cidade(reg.get('cd_subgrupo'))] = cd_sub_grupo_web
        subgrupos_web.append(
            converter_sub_grupo(
                pendente['regs'][0],
                tenant_id,
                cd_empresa,
                cd_sub_grupo_web,
                pendente['cd_grupo_web']
            )
        )

    if reaproveitados:
        print(f"[OK] {reaproveitados} sub_grupos reaproveitados por descricao no mesmo tenant_id/cd_empresa/grupo.")
    print(f"[OK] {len(subgrupos_web)} sub_grupos novos para inserir ({erros_mapeamento} sem grupo mapeado).")
    aplicar_limites_texto_web(cursor_web, tabela_web_sub_grupo, subgrupos_web, 'sub_grupo')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_sub_grupo,
        subgrupos_web,
        'cd_sub_grupo',
        'sp_sub_grupo'
    )
    erros_detalhe.extend(erros_insert_detalhe)

    return {
        'tabela': 'sub_grupo',
        'lidos': len(subgrupos_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros_mapeamento + erros_insert,
        'erros_detalhe': erros_detalhe,
    }


def buscar_tamanhos_giv(cursor_giv, cd_empresa_giv=None):
    where_sql, params = filtro_empresa_giv(cursor_giv, 'tamanho', 't', cd_empresa_giv)
    sql = """
        SELECT
            t.cd_tamanho,
            t.ds_tamanho
        FROM tamanho t
        {where_sql}
        ORDER BY t.cd_tamanho
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def buscar_chaves_nome_web(cursor_web, tabela_web, coluna_nome, tenant_id=None, cd_empresa=None):
    """Busca nomes ja usados no Web para evitar colisao de chave unica, sem reaproveitar codigo."""
    filtros = []
    params = []
    if tenant_id is not None:
        filtros.append(f"{quote_identificador('tenant_id')} = %s")
        params.append(tenant_id)
    if cd_empresa is not None:
        filtros.append(f"{quote_identificador('cd_empresa')} = %s")
        params.append(cd_empresa)

    where_sql = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador(coluna_nome)}
          FROM {tabela_web}
          {where_sql}
        """,
        params
    )
    chaves = set()
    for row in cursor_web.fetchall():
        chave = chave_nome_produto(row[0])
        if chave:
            chaves.add(chave)
    return chaves


def nome_unico_conversao(nome_base, codigo_origem, nomes_usados, limite):
    """Gera descricao unica para inserir a origem GIV sem reaproveitar registro Web."""
    nome_base = limpar_valor(nome_base) or f"CADASTRO {codigo_origem}"
    limite = int(limite or 255)

    candidato = limitar_texto(nome_base, limite)
    chave = chave_nome_produto(candidato)
    if chave and chave not in nomes_usados:
        nomes_usados.add(chave)
        return candidato, False

    sufixo_base = f" GIV{codigo_origem}"
    tentativas = [sufixo_base] + [f" GIV{codigo_origem}-{i}" for i in range(2, 100)]
    for sufixo in tentativas:
        if len(sufixo) >= limite:
            candidato = str(codigo_origem)[-limite:]
        else:
            candidato = limitar_texto(nome_base, limite - len(sufixo)) + sufixo
        chave = chave_nome_produto(candidato)
        if chave and chave not in nomes_usados:
            nomes_usados.add(chave)
            return candidato, True

    candidato = limitar_texto(f"{codigo_origem}", limite)
    nomes_usados.add(chave_nome_produto(candidato))
    return candidato, True


def planejar_cadastro_por_descricao(
    cursor_web,
    tabela_web,
    coluna_codigo,
    coluna_descricao,
    registros_giv,
    coluna_codigo_giv,
    obter_descricao,
    tenant_id,
    cd_empresa=None
):
    """Monta mapa GIV->Web reaproveitando mesma descricao no mesmo tenant/empresa."""
    mapa = {}
    reaproveitados = 0
    mapa_nome_web = buscar_mapa_nome_web(
        cursor_web,
        tabela_web,
        coluna_codigo,
        coluna_descricao,
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    mapa_nome_planejado = dict(mapa_nome_web)
    pendentes_por_chave = {}
    ordem_insercao = []

    for reg in registros_giv:
        ds_cadastro = obter_descricao(reg)
        chave_nome = chave_nome_produto(ds_cadastro)
        cd_web_reaproveitado = mapa_nome_planejado.get(chave_nome)
        if cd_web_reaproveitado is not None:
            mapa[reg.get(coluna_codigo_giv)] = cd_web_reaproveitado
            mapa[normalizar_codigo_cidade(reg.get(coluna_codigo_giv))] = cd_web_reaproveitado
            reaproveitados += 1
            continue

        if chave_nome in pendentes_por_chave:
            pendentes_por_chave[chave_nome]['regs'].append(reg)
            reaproveitados += 1
            continue

        pendentes_por_chave[chave_nome] = {
            'descricao': ds_cadastro,
            'regs': [reg],
        }
        ordem_insercao.append(chave_nome)

    return mapa, reaproveitados, pendentes_por_chave, ordem_insercao


def processar_tamanhos_produto(cursor_giv, cursor_web, tabela_web_tamanho, tenant_id, cd_empresa_giv=None):
    sequence_tamanho = buscar_sequence_coluna_web(cursor_web, tabela_web_tamanho, 'cd_tamanho')
    max_tamanho = sincronizar_sequence_com_max(cursor_web, tabela_web_tamanho, 'cd_tamanho', sequence_tamanho)
    print()
    print(f"[OK] Sequence de tamanho: {sequence_tamanho} (sincronizada com max={max_tamanho}).")

    tamanhos_giv = buscar_tamanhos_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(tamanhos_giv)} tamanhos encontrados no GIV.")

    registros = []
    mapa, reaproveitados, pendentes_por_chave, tamanhos_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_tamanho,
        'cd_tamanho',
        'ds_tamanho',
        tamanhos_giv,
        'cd_tamanho',
        lambda reg: limpar_valor(reg.get('ds_tamanho')) or f"TAMANHO {reg.get('cd_tamanho')}",
        tenant_id=tenant_id
    )

    codigos_tamanho = iter(reservar_valores_sequence(cursor_web, sequence_tamanho, len(tamanhos_para_inserir)))
    for chave_nome in tamanhos_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        ds_tamanho = pendente['descricao']

        cd_tamanho_web = next(codigos_tamanho)
        for reg in pendente['regs']:
            mapa[reg.get('cd_tamanho')] = cd_tamanho_web
            mapa[normalizar_codigo_cidade(reg.get('cd_tamanho'))] = cd_tamanho_web
        registros.append(limpar_registro({
            'cd_tamanho': cd_tamanho_web,
            'id_status': 'A',
            'ds_tamanho': ds_tamanho,
            'tenant_id': tenant_id,
        }))

    if reaproveitados:
        print(f"[OK] {reaproveitados} tamanhos reaproveitados por descricao no mesmo tenant_id.")
    print(f"[OK] {len(registros)} tamanhos novos para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_tamanho, registros, 'tamanho')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_tamanho,
        registros,
        'cd_tamanho',
        'sp_tamanho'
    )
    mapa = remover_mapa_com_codigos_invalidos(
        mapa,
        codigos_insert_com_erro(erros_detalhe, 'cd_tamanho')
    )
    return mapa, {
        'tabela': 'tamanho',
        'lidos': len(tamanhos_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_unidades_giv(cursor_giv, cd_empresa_giv=None):
    where_sql, params = filtro_empresa_giv(cursor_giv, 'unidade', 'u', cd_empresa_giv)
    sql = """
        SELECT
            u.cd_unidade,
            u.ds_unidade,
            u.ds_unidade_fator,
            u.fator
        FROM unidade u
        {where_sql}
        ORDER BY u.cd_unidade
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def processar_unidades_produto(cursor_giv, cursor_web, tabela_web_unidade, tenant_id, cd_empresa_giv=None):
    sequence_unidade = buscar_sequence_coluna_web(cursor_web, tabela_web_unidade, 'cd_unidade')
    max_unidade = sincronizar_sequence_com_max(cursor_web, tabela_web_unidade, 'cd_unidade', sequence_unidade)
    print()
    print(f"[OK] Sequence de unidade: {sequence_unidade} (sincronizada com max={max_unidade}).")

    unidades_giv = buscar_unidades_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(unidades_giv)} unidades encontradas no GIV.")

    registros = []
    mapa, reaproveitados, pendentes_por_chave, unidades_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_unidade,
        'cd_unidade',
        'ds_unidade',
        unidades_giv,
        'cd_unidade',
        lambda reg: limpar_valor(reg.get('ds_unidade')) or f"UN{reg.get('cd_unidade')}",
        tenant_id=tenant_id
    )

    codigos_unidade = iter(reservar_valores_sequence(cursor_web, sequence_unidade, len(unidades_para_inserir)))
    for chave_nome in unidades_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        ds_unidade = pendente['descricao']
        cd_unidade_web = next(codigos_unidade)
        reg_base = pendente['regs'][0]
        for reg in pendente['regs']:
            mapa[reg.get('cd_unidade')] = cd_unidade_web
            mapa[normalizar_codigo_cidade(reg.get('cd_unidade'))] = cd_unidade_web
        registros.append(limpar_registro({
            'cd_unidade': cd_unidade_web,
            'id_status': 'A',
            'ds_unidade': ds_unidade,
            'ds_unidade_longa': reg_base.get('ds_unidade_fator'),
            'fator_estoque': reg_base.get('fator') or Decimal('1'),
            'tenant_id': tenant_id,
        }))

    if reaproveitados:
        print(f"[OK] {reaproveitados} unidades reaproveitadas por descricao no mesmo tenant_id.")
    print(f"[OK] {len(registros)} unidades novas para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_unidade, registros, 'unidade')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_unidade,
        registros,
        'cd_unidade',
        'sp_unidade'
    )
    mapa = remover_mapa_com_codigos_invalidos(
        mapa,
        codigos_insert_com_erro(erros_detalhe, 'cd_unidade')
    )
    return mapa, {
        'tabela': 'unidade',
        'lidos': len(unidades_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_colecoes_giv(cursor_giv, cd_empresa_giv=None):
    where_sql, params = filtro_empresa_giv(cursor_giv, 'colecao', 'c', cd_empresa_giv)
    sql = """
        SELECT
            c.cd_colecao,
            c.ds_colecao
        FROM colecao c
        {where_sql}
        ORDER BY c.cd_colecao
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def processar_colecoes_produto(cursor_giv, cursor_web, tabela_web_colecao, tenant_id, cd_empresa, cd_empresa_giv=None):
    sequence_colecao = buscar_sequence_coluna_web(cursor_web, tabela_web_colecao, 'cd_colecao')
    max_colecao = sincronizar_sequence_com_max(cursor_web, tabela_web_colecao, 'cd_colecao', sequence_colecao)
    print()
    print(f"[OK] Sequence de colecao: {sequence_colecao} (sincronizada com max={max_colecao}).")

    colecoes_giv = buscar_colecoes_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(colecoes_giv)} colecoes encontradas no GIV.")

    registros = []
    mapa, reaproveitados, pendentes_por_chave, colecoes_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_colecao,
        'cd_colecao',
        'ds_colecao',
        colecoes_giv,
        'cd_colecao',
        lambda reg: limpar_valor(reg.get('ds_colecao')) or f"COLECAO {reg.get('cd_colecao')}",
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )

    codigos_colecao = iter(reservar_valores_sequence(cursor_web, sequence_colecao, len(colecoes_para_inserir)))
    for chave_nome in colecoes_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        ds_colecao = pendente['descricao']
        cd_colecao_web = next(codigos_colecao)
        for reg in pendente['regs']:
            mapa[reg.get('cd_colecao')] = cd_colecao_web
            mapa[normalizar_codigo_cidade(reg.get('cd_colecao'))] = cd_colecao_web
        registros.append(limpar_registro({
            'cd_colecao': cd_colecao_web,
            'tenant_id': tenant_id,
            'cd_empresa': cd_empresa,
            'ds_colecao': ds_colecao,
        }))

    if reaproveitados:
        print(f"[OK] {reaproveitados} colecoes reaproveitadas por descricao no mesmo tenant_id/cd_empresa.")
    print(f"[OK] {len(registros)} colecoes novas para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_colecao, registros, 'colecao')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_colecao,
        registros,
        'cd_colecao',
        'sp_colecao'
    )
    mapa = remover_mapa_com_codigos_invalidos(
        mapa,
        codigos_insert_com_erro(erros_detalhe, 'cd_colecao')
    )
    return mapa, {
        'tabela': 'colecao',
        'lidos': len(colecoes_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_grades_giv(cursor_giv, cd_empresa_giv=None):
    where_sql, params = filtro_empresa_giv(cursor_giv, 'grade', 'g', cd_empresa_giv)
    sql = """
        SELECT
            g.cd_grade,
            g.ds_grade
        FROM grade g
        {where_sql}
        ORDER BY g.cd_grade
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def buscar_grade_itens_giv(cursor_giv, cd_empresa_giv=None):
    where_sql, params = filtro_empresa_giv(cursor_giv, 'grade_item', 'gi', cd_empresa_giv)
    sql = """
        SELECT
            gi.cd_grade,
            gi.cd_tamanho
        FROM grade_item gi
        {where_sql}
        ORDER BY gi.cd_grade, gi.ordem, gi.nr_item
    """.format(where_sql=where_sql)
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def processar_grades_produto(
    cursor_giv,
    cursor_web,
    tabela_web_grade,
    tabela_web_grade_tamanho,
    mapa_tamanhos,
    tenant_id,
    cd_empresa,
    cd_empresa_giv=None
):
    sequence_grade = buscar_sequence_coluna_web(cursor_web, tabela_web_grade, 'cd_grade')
    max_grade = sincronizar_sequence_com_max(cursor_web, tabela_web_grade, 'cd_grade', sequence_grade)
    print()
    print(f"[OK] Sequence de grade: {sequence_grade} (sincronizada com max={max_grade}).")

    grades_giv = buscar_grades_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(grades_giv)} grades encontradas no GIV.")

    registros = []
    mapa = {}
    nomes_ajustados = 0
    nomes_usados = buscar_chaves_nome_web(
        cursor_web,
        tabela_web_grade,
        'ds_grade',
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    limites_texto = buscar_limites_texto_web(cursor_web, tabela_web_grade)
    codigos_grade = iter(reservar_valores_sequence(cursor_web, sequence_grade, len(grades_giv)))
    for reg in grades_giv:
        ds_grade = limpar_valor(reg.get('ds_grade')) or f"GRADE {reg.get('cd_grade')}"
        ds_grade, ajustado = nome_unico_conversao(
            ds_grade,
            reg.get('cd_grade'),
            nomes_usados,
            limites_texto.get('ds_grade')
        )
        if ajustado:
            nomes_ajustados += 1

        cd_grade_web = next(codigos_grade)
        mapa[reg.get('cd_grade')] = cd_grade_web
        registros.append(limpar_registro({
            'cd_grade': cd_grade_web,
            'ds_grade': ds_grade,
            'tenant_id': tenant_id,
            'cd_empresa': cd_empresa,
        }))

    if nomes_ajustados:
        print(f"[INFO] Grade: {nomes_ajustados} descricoes ajustadas para nao colidir no Web.")
    aplicar_limites_texto_web(cursor_web, tabela_web_grade, registros, 'grade')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_grade,
        registros,
        'cd_grade',
        'sp_grade'
    )
    mapa = remover_mapa_com_codigos_invalidos(
        mapa,
        codigos_insert_com_erro(erros_detalhe, 'cd_grade')
    )

    itens_giv = buscar_grade_itens_giv(cursor_giv, cd_empresa_giv)
    tamanhos_por_grade = buscar_tamanhos_por_grade_web(cursor_web, tabela_web_grade_tamanho)
    # O par (A, B) e a PK de "_gradeTotamanho". Alem de pular o que ja existe no
    # Web, precisamos deduplicar dentro do proprio lote: o GIV pode repetir o
    # mesmo (grade, tamanho) e antes isso virava erro fatal de chave duplicada.
    itens = []
    ja_no_lote = set()
    duplicados_giv = 0
    ja_existiam_web = 0
    for item in itens_giv:
        cd_grade_web = mapa.get(item.get('cd_grade'))
        cd_tamanho_web = mapa_tamanhos.get(item.get('cd_tamanho'))
        if not cd_grade_web or not cd_tamanho_web:
            continue
        if cd_tamanho_web in tamanhos_por_grade.get(cd_grade_web, set()):
            ja_existiam_web += 1
            continue
        par = (cd_grade_web, cd_tamanho_web)
        if par in ja_no_lote:
            duplicados_giv += 1
            continue
        ja_no_lote.add(par)
        itens.append({'A': cd_grade_web, 'B': cd_tamanho_web})

    if ja_existiam_web:
        print(f"[OK] grade/tamanho: {ja_existiam_web} vinculos ja existiam no Web e foram reaproveitados.")
    if duplicados_giv:
        print(f"[OK] grade/tamanho: {duplicados_giv} vinculos duplicados no GIV foram ignorados.")

    _, erros_relacao, erros_relacao_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_grade_tamanho,
        itens,
        'A',
        'sp_grade_tamanho',
        # "_gradeTotamanho" e uma tabela de vinculo com PK (A, B) e sem outras
        # colunas: um par que ja exista nao e falha, e apenas reaproveitamento.
        ignorar_conflito=True
    )

    for item in itens:
        tamanhos_por_grade.setdefault(item['A'], set()).add(item['B'])

    erros_detalhe.extend(erros_relacao_detalhe)
    return mapa, tamanhos_por_grade, {
        'tabela': 'grade',
        'lidos': len(grades_giv),
        'inseridos': inseridos,
        'existentes': 0,
        'erros': erros + erros_relacao,
        'erros_detalhe': erros_detalhe,
    }


def chave_nome_produto(valor):
    """Chave normalizada para relacionar cadastros auxiliares por nome."""
    return normalizar_nome_cidade(valor)


def buscar_mapa_nome_web(cursor_web, tabela_web, coluna_codigo, coluna_nome, tenant_id=None, cd_empresa=None):
    """Busca nome normalizado -> codigo web, mantendo o maior codigo quando houver duplicidade."""
    filtros = []
    params = []
    if tenant_id is not None:
        filtros.append(f"{quote_identificador('tenant_id')} = %s")
        params.append(tenant_id)
    if cd_empresa is not None:
        filtros.append(f"{quote_identificador('cd_empresa')} = %s")
        params.append(cd_empresa)

    where_sql = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador(coluna_codigo)}, {quote_identificador(coluna_nome)}
          FROM {tabela_web}
          {where_sql}
         ORDER BY {quote_identificador(coluna_codigo)}
        """,
        params
    )
    mapa = {}
    for row in cursor_web.fetchall():
        chave = chave_nome_produto(row[1])
        if chave and chave not in mapa:
            mapa[chave] = row[0]
    return mapa


def buscar_mapa_giv_para_web_por_nome(
    cursor_giv,
    cursor_web,
    sql_giv,
    coluna_codigo_giv,
    coluna_nome_giv,
    tabela_web,
    coluna_codigo_web,
    coluna_nome_web,
    tenant_id=None,
    cd_empresa=None
):
    """Relaciona codigo GIV -> codigo Web comparando descricao/nome."""
    registros_giv = buscar_registros_giv(cursor_giv, sql_giv)
    filtros = []
    params = []
    if tenant_id is not None:
        filtros.append(f"{quote_identificador('tenant_id')} = %s")
        params.append(tenant_id)
    if cd_empresa is not None:
        filtros.append(f"{quote_identificador('cd_empresa')} = %s")
        params.append(cd_empresa)

    where_sql = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador(coluna_codigo_web)}, {quote_identificador(coluna_nome_web)}
          FROM {tabela_web}
          {where_sql}
         ORDER BY {quote_identificador(coluna_codigo_web)}
        """,
        params
    )
    mapa_web_nome = {}
    mapa_web_codigo_origem = {}
    for row in cursor_web.fetchall():
        cd_web = row[0]
        chave = chave_nome_produto(row[1])
        if not chave:
            continue
        if chave not in mapa_web_nome:
            mapa_web_nome[chave] = cd_web
        match = re.search(r'(?:^| )GIV(\d+)(?: \d+)?$', chave)
        if match and int(match.group(1)) not in mapa_web_codigo_origem:
            mapa_web_codigo_origem[int(match.group(1))] = cd_web

    mapa = {}
    for reg in registros_giv:
        cd_origem = normalizar_codigo_cidade(reg.get(coluna_codigo_giv))
        if cd_origem in mapa_web_codigo_origem:
            mapa[reg.get(coluna_codigo_giv)] = mapa_web_codigo_origem[cd_origem]
            mapa[cd_origem] = mapa_web_codigo_origem[cd_origem]
            continue

        chave = chave_nome_produto(reg.get(coluna_nome_giv))
        if chave in mapa_web_nome:
            mapa[reg.get(coluna_codigo_giv)] = mapa_web_nome[chave]
            mapa[cd_origem] = mapa_web_nome[chave]
    return mapa


def buscar_mapa_grupo_produto_para_web(
    cursor_giv,
    cursor_web,
    tabela_web_grupo,
    tenant_id,
    cd_empresa,
    mapa_grupos_convertidos=None,
    cd_empresa_giv=None
):
    """Mapeia qualquer cd_grupo do grupo_produto para o grupo pai Web."""
    grupos_produto = buscar_grupo_produto_giv(cursor_giv, cd_empresa_giv)
    codigos_pai = {
        codigo_grupo_produto(reg.get('cd_grupo'))
        for reg in grupos_produto
        if valor_flag(reg.get('id_tipo'), '') == 'P'
    }
    mapa_grupos_web = buscar_mapa_grupos_web(cursor_web, tabela_web_grupo, tenant_id, cd_empresa)

    mapa_pais = {}
    mapa_grupos_convertidos = mapa_grupos_convertidos or {}
    for reg in grupos_produto:
        if valor_flag(reg.get('id_tipo'), '') != 'P':
            continue
        cd_grupo = codigo_grupo_produto(reg.get('cd_grupo'))
        cd_web = mapa_grupos_convertidos.get(cd_grupo)
        if cd_web is None:
            cd_web = mapa_grupos_convertidos.get(reg.get('cd_grupo'))
        if cd_web is None:
            cd_web = mapa_grupos_web.get(chave_texto(reg.get('ds_grupo')))
        if cd_web is not None:
            mapa_pais[cd_grupo] = cd_web

    mapa = {}
    for reg in grupos_produto:
        cd_grupo = codigo_grupo_produto(reg.get('cd_grupo'))
        cd_pai = encontrar_pai_grupo_produto(cd_grupo, codigos_pai)
        cd_web = mapa_pais.get(cd_pai)
        if cd_web is not None:
            mapa[cd_grupo] = cd_web
            mapa[reg.get('cd_grupo')] = cd_web

    return mapa


def buscar_mapa_subgrupo_produto_para_web(cursor_giv, cursor_web, tabela_web_sub_grupo, tenant_id, cd_empresa, cd_empresa_giv=None):
    """Mapeia cd_grupo folha do grupo_produto para sub_grupo Web."""
    subgrupos = buscar_subgrupos_produto_filho_giv(cursor_giv, cd_empresa_giv)
    mapa_web = buscar_mapa_nome_web(
        cursor_web,
        tabela_web_sub_grupo,
        'cd_sub_grupo',
        'ds_sub_grupo',
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    mapa = {}
    for reg in subgrupos:
        chave = chave_nome_produto(reg.get('ds_subgrupo'))
        cd_web = mapa_web.get(chave)
        if cd_web is not None:
            cd_subgrupo = codigo_grupo_produto(reg.get('cd_subgrupo'))
            mapa[cd_subgrupo] = cd_web
            mapa[reg.get('cd_subgrupo')] = cd_web
    return mapa


def sql_mapa_giv_auxiliar(cursor_giv, tabela, coluna_codigo, coluna_nome, cd_empresa_giv=None):
    where_sql = ""
    if cd_empresa_giv is not None and tabela_giv_tem_coluna(cursor_giv, tabela, 'cd_empresa'):
        where_sql = f" WHERE cd_empresa = {int(cd_empresa_giv)}"
    return f"SELECT {coluna_codigo}, {coluna_nome} FROM {tabela}{where_sql} ORDER BY {coluna_codigo}"


def carregar_mapas_auxiliares_produto(cursor_giv, cursor_web, tabelas_web, tenant_id, cd_empresa, cd_empresa_giv=None):
    """Carrega de/para dos auxiliares de produto que ja existem no Web."""
    mapas = {}

    if tabelas_web.get('cor'):
        mapas['cor'] = buscar_mapa_giv_para_web_por_nome(
            cursor_giv,
            cursor_web,
            sql_mapa_giv_auxiliar(cursor_giv, 'cor', 'cd_cor', 'ds_cor', cd_empresa_giv),
            'cd_cor',
            'ds_cor',
            tabelas_web['cor'],
            'cd_cor',
            'ds_cor',
            tenant_id=tenant_id
        )

    if tabelas_web.get('tamanho'):
        mapas['tamanho'] = buscar_mapa_giv_para_web_por_nome(
            cursor_giv,
            cursor_web,
            sql_mapa_giv_auxiliar(cursor_giv, 'tamanho', 'cd_tamanho', 'ds_tamanho', cd_empresa_giv),
            'cd_tamanho',
            'ds_tamanho',
            tabelas_web['tamanho'],
            'cd_tamanho',
            'ds_tamanho',
            tenant_id=tenant_id
        )
    if tabelas_web.get('unidade'):
        mapas['unidade'] = buscar_mapa_giv_para_web_por_nome(
            cursor_giv,
            cursor_web,
            sql_mapa_giv_auxiliar(cursor_giv, 'unidade', 'cd_unidade', 'ds_unidade', cd_empresa_giv),
            'cd_unidade',
            'ds_unidade',
            tabelas_web['unidade'],
            'cd_unidade',
            'ds_unidade',
            tenant_id=tenant_id
        )
    if tabelas_web.get('colecao'):
        mapas['colecao'] = buscar_mapa_giv_para_web_por_nome(
            cursor_giv,
            cursor_web,
            sql_mapa_giv_auxiliar(cursor_giv, 'colecao', 'cd_colecao', 'ds_colecao', cd_empresa_giv),
            'cd_colecao',
            'ds_colecao',
            tabelas_web['colecao'],
            'cd_colecao',
            'ds_colecao',
            tenant_id=tenant_id,
            cd_empresa=cd_empresa
        )
    if tabelas_web.get('grade'):
        mapas['grade'] = buscar_mapa_giv_para_web_por_nome(
            cursor_giv,
            cursor_web,
            sql_mapa_giv_auxiliar(cursor_giv, 'grade', 'cd_grade', 'ds_grade', cd_empresa_giv),
            'cd_grade',
            'ds_grade',
            tabelas_web['grade'],
            'cd_grade',
            'ds_grade',
            tenant_id=tenant_id,
            cd_empresa=cd_empresa
        )
    for chave in AUXILIARES_PRODUTO:
        mapas.setdefault(chave, {})
    return mapas


def buscar_tamanhos_por_grade_web(cursor_web, tabela_web_grade_tamanho):
    """Carrega relacao grade x tamanho ja existente no Web."""
    if not tabela_web_grade_tamanho:
        return {}

    cursor_web.execute("SAVEPOINT sp_mapa_grade_tamanho")
    try:
        cursor_web.execute(
            f"""
            SELECT {quote_identificador('A')}, {quote_identificador('B')}
              FROM {tabela_web_grade_tamanho}
            """
        )
        tamanhos_por_grade = {}
        for row in cursor_web.fetchall():
            tamanhos_por_grade.setdefault(row[0], set()).add(row[1])
        cursor_web.execute("RELEASE SAVEPOINT sp_mapa_grade_tamanho")
        return tamanhos_por_grade
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_mapa_grade_tamanho")
            cursor_web.execute("RELEASE SAVEPOINT sp_mapa_grade_tamanho")
        except Exception:
            pass
        print(f"[AVISO] Nao foi possivel carregar grade x tamanho do Web: {e}")
        return {}


def buscar_mapa_fornecedor_produto(cursor_giv, cursor_web, tabela_web_fornecedor, tenant_id, cd_empresa_giv=None):
    """
    Relaciona fornecedor GIV -> fornecedor Web por documento e nome.

    O GIV repete o mesmo CNPJ em cadastros diferentes (nesta base, 12 documentos
    aparecem em mais de um fornecedor; por exemplo "LAFORT MALHAS INDUSTRIA E
    COMERCIO LTDA" e "O MESMO" compartilham 75165399000160). Indexar
    documento -> um unico codigo fazia o ultimo sobrescrever os demais, e todos
    os titulos/produtos do fornecedor certo passavam a apontar para o cadastro
    errado. O total financeiro continuava correto, mas o rateio por fornecedor
    ficava trocado.

    Por isso guardamos TODOS os candidatos por documento e por nome e
    desempatamos assim:
      1. documento + nome batendo (empate resolvido pelo nome);
      2. documento, preferindo um cadastro Web ainda nao usado;
      3. nome exato, tambem preferindo um cadastro ainda nao usado.
    """
    fornecedores_giv = buscar_fornecedores_giv(cursor_giv, cd_empresa_giv)
    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_fornecedor')},
            {quote_identificador('id_cadastro_nacional')},
            {quote_identificador('razao_social')},
            {quote_identificador('nm_fantasia_fornecedor')}
          FROM {tabela_web_fornecedor}
         WHERE {quote_identificador('tenant_id')} = %s
         ORDER BY {quote_identificador('cd_fornecedor')}
        """,
        (tenant_id,)
    )
    por_documento = {}
    por_nome = {}
    nomes_do_web = {}
    for row in cursor_web.fetchall():
        cd_fornecedor = row[0]
        chaves_nome_web = {
            chave_nome_produto(nome)
            for nome in (row[2], row[3])
            if chave_nome_produto(nome)
        }
        nomes_do_web[cd_fornecedor] = chaves_nome_web
        for documento in (normalizar_cadastro_nacional(row[1]), somente_digitos(row[1])):
            if documento:
                por_documento.setdefault(documento, [])
                if cd_fornecedor not in por_documento[documento]:
                    por_documento[documento].append(cd_fornecedor)
        for chave in chaves_nome_web:
            por_nome.setdefault(chave, [])
            if cd_fornecedor not in por_nome[chave]:
                por_nome[chave].append(cd_fornecedor)

    def escolher(candidatos, chaves_nome_giv, usados):
        """Desempata candidatos preferindo nome igual e cadastro ainda livre."""
        if not candidatos:
            return None
        por_nome_igual = [
            cd for cd in candidatos
            if nomes_do_web.get(cd, set()) & chaves_nome_giv
        ]
        for grupo in (por_nome_igual, candidatos):
            if not grupo:
                continue
            livres = [cd for cd in grupo if cd not in usados]
            if livres:
                return livres[0]
        return por_nome_igual[0] if por_nome_igual else candidatos[0]

    mapa = {}
    usados = set()
    ambiguos = 0
    por_documento_ok = 0
    por_nome_ok = 0
    for reg in fornecedores_giv:
        chaves_nome_giv = {
            chave_nome_produto(nome)
            for nome in (
                nome_fornecedor_origem(reg),
                reg.get('nm_fornecedor'),
                reg.get('razao_social'),
            )
            if chave_nome_produto(nome)
        }
        candidatos = []
        for documento in (normalizar_cadastro_nacional(reg.get('cgc')), somente_digitos(reg.get('cgc'))):
            if documento:
                for cd in por_documento.get(documento, []):
                    if cd not in candidatos:
                        candidatos.append(cd)

        origem_doc = bool(candidatos)
        if not candidatos:
            for chave_nome in chaves_nome_giv:
                for cd in por_nome.get(chave_nome, []):
                    if cd not in candidatos:
                        candidatos.append(cd)

        if len(candidatos) > 1:
            ambiguos += 1
        cd_fornecedor_web = escolher(candidatos, chaves_nome_giv, usados)
        if cd_fornecedor_web is not None:
            usados.add(cd_fornecedor_web)
            if origem_doc:
                por_documento_ok += 1
            else:
                por_nome_ok += 1
            mapa[reg.get('cd_fornecedor')] = cd_fornecedor_web
            mapa[normalizar_codigo_cidade(reg.get('cd_fornecedor'))] = cd_fornecedor_web

    print(
        f"[OK] Mapa fornecedor: {por_documento_ok + por_nome_ok}/{len(fornecedores_giv)} "
        f"fornecedores GIV ligados ao Web ({por_documento_ok} por documento, "
        f"{por_nome_ok} por nome; {ambiguos} tiveram mais de um candidato e foram "
        "desempatados pelo nome)."
    )
    return mapa


def primeiro_codigo_mapa(mapa):
    """Retorna um codigo padrao estavel a partir de um mapa GIV -> Web."""
    valores = [
        valor
        for valor in (mapa or {}).values()
        if valor is not None
    ]
    if not valores:
        return None
    try:
        return min(valores)
    except TypeError:
        return valores[0]


def valor_mapa_ou_padrao(mapa, chave, padrao=None):
    """Busca no mapa; usa padrao somente quando a origem veio vazia."""
    if chave is None:
        return padrao

    valor = (mapa or {}).get(chave)
    if valor is not None:
        return valor

    chave_normalizada = normalizar_codigo_cidade(chave)
    if chave_normalizada != chave:
        valor = (mapa or {}).get(chave_normalizada)
        if valor is not None:
            return valor

    return None


def codigos_usados_produtos(produtos_giv, campo):
    """Retorna codigos distintos usados nos produtos para um campo de origem."""
    return {
        normalizar_codigo_cidade(reg.get(campo))
        for reg in produtos_giv
        if reg.get(campo) is not None
    }


def imprimir_diagnostico_de_para_produto(nome, campo_origem, mapa, produtos_giv, obrigatorio=False):
    """Mostra quanto do de/para foi resolvido para os codigos usados em produto."""
    usados = codigos_usados_produtos(produtos_giv, campo_origem)
    if not usados:
        texto_obrigatorio = " obrigatorio" if obrigatorio else ""
        print(f"[INFO] De/para produto {nome}{texto_obrigatorio}: nenhum codigo informado no GIV.")
        return

    mapeados = {
        codigo
        for codigo in usados
        if codigo in (mapa or {}) and (mapa or {}).get(codigo) is not None
    }
    faltantes = sorted(usados - mapeados)
    texto_obrigatorio = " obrigatorio" if obrigatorio else ""
    print(
        f"[INFO] De/para produto {nome}{texto_obrigatorio}: "
        f"{len(mapeados)}/{len(usados)} codigos usados nos produtos mapeados."
    )
    if faltantes:
        amostra = ', '.join(str(codigo) for codigo in faltantes[:10])
        print(f"  [AVISO] {nome}: codigos GIV sem de/para Web: {amostra}")


def atualizar_padroes_produto(mapas):
    """Prepara padroes somente para auxiliares obrigatorios do produto."""
    padroes = mapas.setdefault('_padroes', {})
    for chave, campo in (
        ('marca', 'cd_marca'),
        ('grupo', 'cd_grupo'),
        ('fornecedor', 'cd_fornecedor'),
    ):
        codigo = primeiro_codigo_mapa(mapas.get(chave))
        if codigo is not None:
            padroes[campo] = codigo

    return padroes


def buscar_codigo_padrao_web(cursor_web, tabela_web, coluna_codigo, tenant_id=None, cd_empresa=None, preferido=None):
    """Busca um codigo existente no Web para usar como padrao quando nao houver de/para."""
    if not tabela_web:
        return None

    filtros = []
    params = []
    if tenant_id is not None:
        filtros.append(f"{quote_identificador('tenant_id')} = %s")
        params.append(tenant_id)
    if cd_empresa is not None:
        filtros.append(f"{quote_identificador('cd_empresa')} = %s")
        params.append(cd_empresa)

    where_sql = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    coluna_sql = quote_identificador(coluna_codigo)

    cursor_web.execute("SAVEPOINT sp_padrao_produto")
    try:
        if preferido is not None:
            filtros_preferido = list(filtros)
            params_preferido = list(params)
            filtros_preferido.append(f"{coluna_sql} = %s")
            params_preferido.append(preferido)
            where_preferido = f" WHERE {' AND '.join(filtros_preferido)}"
            cursor_web.execute(
                f"SELECT {coluna_sql} FROM {tabela_web}{where_preferido} ORDER BY {coluna_sql} LIMIT 1",
                params_preferido
            )
            row = cursor_web.fetchone()
            if row:
                cursor_web.execute("RELEASE SAVEPOINT sp_padrao_produto")
                return row[0]

        cursor_web.execute(
            f"SELECT {coluna_sql} FROM {tabela_web}{where_sql} ORDER BY {coluna_sql} LIMIT 1",
            params
        )
        row = cursor_web.fetchone()
        if not row and cd_empresa is not None:
            filtros_sem_empresa = []
            params_sem_empresa = []
            if tenant_id is not None:
                filtros_sem_empresa.append(f"{quote_identificador('tenant_id')} = %s")
                params_sem_empresa.append(tenant_id)
            where_sem_empresa = (
                f" WHERE {' AND '.join(filtros_sem_empresa)}"
                if filtros_sem_empresa
                else ""
            )
            cursor_web.execute(
                f"SELECT {coluna_sql} FROM {tabela_web}{where_sem_empresa} ORDER BY {coluna_sql} LIMIT 1",
                params_sem_empresa
            )
            row = cursor_web.fetchone()
        cursor_web.execute("RELEASE SAVEPOINT sp_padrao_produto")
        return row[0] if row else None
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_padrao_produto")
            cursor_web.execute("RELEASE SAVEPOINT sp_padrao_produto")
        except Exception:
            pass
        print(f"[AVISO] Nao foi possivel buscar padrao em {tabela_web}.{coluna_codigo}: {e}")
        return None


def preencher_padroes_produto_web(cursor_web, tabelas_web, padroes, tenant_id, cd_empresa):
    """Completa padroes de marca/grupo com codigos existentes no Web."""
    defs = (
        ('cd_marca', 'marca', 'cd_marca', tenant_id, None, None),
        ('cd_grupo', 'grupo', 'cd_grupo', tenant_id, cd_empresa, None),
        ('cd_fornecedor', 'fornecedor', 'cd_fornecedor', tenant_id, None, None),
    )

    for campo, chave_tabela, coluna, tenant_filtro, empresa_filtro, preferido in defs:
        if padroes.get(campo) is not None:
            continue
        codigo = buscar_codigo_padrao_web(
            cursor_web,
            tabelas_web.get(chave_tabela),
            coluna,
            tenant_filtro,
            empresa_filtro,
            preferido
        )
        if codigo is not None:
            padroes[campo] = codigo

    return padroes


def processar_fornecedores(
    cursor_giv,
    cursor_web,
    tabela_web_fornecedor,
    cidades_giv,
    cidades_web,
    tenant_id,
    session_api_cep,
    base_url_api_cep,
    cd_empresa_giv=None
):
    print()
    print("[INFO] Fornecedor: registros existentes no Web nao serao usados para pular a importacao.")
    if cd_empresa_giv is not None and tabela_giv_tem_coluna(cursor_giv, 'fornecedor', 'cd_empresa'):
        print(f"[INFO] Fornecedor: filtrando origem GIV por cd_empresa={cd_empresa_giv}.")

    sequence_fornecedor = buscar_sequence_coluna_web(
        cursor_web,
        tabela_web_fornecedor,
        'cd_fornecedor'
    )
    max_fornecedor = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_fornecedor,
        'cd_fornecedor',
        sequence_fornecedor
    )
    print(f"[OK] Sequence de fornecedor: {sequence_fornecedor} (sincronizada com max={max_fornecedor}).")

    print()
    print("[...] Buscando fornecedores do banco GIV...")
    fornecedores_giv = buscar_fornecedores_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(fornecedores_giv)} fornecedores encontrados no GIV.")

    fornecedores_web = []
    pulados = 0
    codigos_fornecedor = iter(reservar_valores_sequence(cursor_web, sequence_fornecedor, len(fornecedores_giv)))
    for reg in fornecedores_giv:
        cd_fornecedor_web = next(codigos_fornecedor)
        fornecedores_web.append(
            converter_fornecedor(
                reg,
                cidades_giv,
                cidades_web,
                cd_fornecedor_web,
                tenant_id
            )
        )

    numeros_extraidos = 0
    numeros_ausentes = 0
    for registro in fornecedores_web:
        if registro.pop('_numero_extraido_do_endereco', False):
            numeros_extraidos += 1
        if not registro.get('ds_numero'):
            numeros_ausentes += 1

    print(
        f"[OK] {len(fornecedores_web)} fornecedores para inserir "
        f"(nenhum fornecedor foi pulado por ja existir no Web)."
    )
    if numeros_extraidos:
        print(
            f"[INFO] Fornecedor: numero extraido do fim do logradouro em {numeros_extraidos} "
            "registros (logradouro preservado nos demais)."
        )
    if numeros_ausentes:
        print(
            f"[INFO] Fornecedor: {numeros_ausentes} registros ficaram sem numero e usaram o "
            "padrao do Web (ds_numero = string vazia, nunca NULL)."
        )
    garantir_ceps_fornecedor_api(fornecedores_web, cursor_web, session_api_cep, base_url_api_cep)

    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_fornecedor,
        fornecedores_web,
        'cd_fornecedor',
        'sp_fornecedor'
    )

    return {
        'tabela': 'fornecedor',
        'lidos': len(fornecedores_giv),
        'inseridos': inseridos,
        'existentes': pulados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_usuarios_giv(cursor_giv, cd_empresa_giv=None):
    """Busca usuarios do GIV, ignorando sempre os codigos reservados 0 e 1."""
    filtros = ["u.cd_usuario NOT IN (0, 1)"]
    params = []
    if cd_empresa_giv is not None:
        filtros.append("u.cd_empresa = ?")
        params.append(cd_empresa_giv)

    sql = """
        SELECT
            u.cd_usuario,
            u.id_situacao,
            u.cd_empresa,
            u.nm_usuario,
            u.nm_login,
            u.senha,
            u.dt_nascimento,
            u.cpf,
            u.rg,
            u.telefone,
            u.celular,
            u.cd_cidade,
            u.endereco,
            u.bairro,
            u.email
        FROM usuario u
        WHERE {where_sql}
        ORDER BY u.cd_usuario
    """.format(where_sql=" AND ".join(filtros))
    return buscar_registros_giv(cursor_giv, sql, params=params)


def converter_usuario(registro_giv, tenant_id, cd_empresa, cidades_giv, cidades_web, cd_usuario_web):
    """Converte usuario do GIV para usuario do Web."""
    cd_usuario_origem = registro_giv.get('cd_usuario')
    nm_usuario = limpar_valor(registro_giv.get('nm_usuario')) or f"USUARIO {cd_usuario_origem}"
    nm_login = limpar_valor(registro_giv.get('nm_login')) or nm_usuario
    senha = limpar_valor(registro_giv.get('senha')) or ''

    id_situacao = limpar_valor(registro_giv.get('id_situacao'))
    id_status = 'A' if id_situacao and str(id_situacao).upper() == 'L' else 'I'

    cd_cidade_web = resolver_cidade_trab(
        registro_giv.get('cd_cidade'),
        cidades_giv,
        cidades_web,
        f"usuario cd_usuario={cd_usuario_origem}"
    )
    ddd_cidade = resolver_ddd_cidade_giv(registro_giv.get('cd_cidade'), cidades_giv)

    registro_web = {
        'cd_usuario': cd_usuario_web,
        'id_status': id_status,
        'cd_empresa': cd_empresa,
        'nm_usuario': nm_usuario,
        'nm_login': nm_login,
        'senha': senha,
        'dt_nascimento': registro_giv.get('dt_nascimento'),
        'cpf': normalizar_cpf_web(registro_giv.get('cpf')),
        'rg': registro_giv.get('rg'),
        'fone': normalizar_telefone_web(registro_giv.get('telefone'), ddd=ddd_cidade),
        'celular': normalizar_celular_web(registro_giv.get('celular'), ddd=ddd_cidade),
        'cd_cidade': cd_cidade_web,
        'cep': None,
        'endereco': registro_giv.get('endereco'),
        'bairro': registro_giv.get('bairro'),
        # A coluna no Web esta como NOT NULL; branco representa o "null" solicitado.
        'ds_numero': '',
        'email': registro_giv.get('email'),
        'vl_salario': None,
        'carteira_profissional': None,
        'escolaridade': None,
        'tenant_id': tenant_id,
        'funcao_id': FUNCAO_USUARIO_PADRAO,
    }

    for chave, valor in registro_web.items():
        if isinstance(valor, str):
            valor = valor.strip()
            if chave == 'ds_numero':
                registro_web[chave] = valor
            else:
                registro_web[chave] = valor or None

    return registro_web


def processar_usuarios(cursor_giv, cursor_web, tabela_web_usuario, cidades_giv, cidades_web, tenant_id, cd_empresa, cd_empresa_giv):
    print()
    print("[INFO] Usuario: os codigos GIV 0 e 1 nunca serao convertidos.")
    print(f"[INFO] Usuario: origem GIV cd_empresa={cd_empresa_giv}; destino Web cd_empresa={cd_empresa}.")

    sequence_usuario = buscar_sequence_coluna_web(
        cursor_web,
        tabela_web_usuario,
        'cd_usuario'
    )
    max_usuario = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_usuario,
        'cd_usuario',
        sequence_usuario
    )
    print(f"[OK] Sequence de usuario: {sequence_usuario} (sincronizada com max={max_usuario}).")

    print()
    print("[...] Buscando usuarios do banco GIV...")
    usuarios_giv = buscar_usuarios_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(usuarios_giv)} usuarios encontrados no GIV (0 e 1 ignorados).")

    usuarios_web = []
    mapa_usuarios = {}
    codigos_usuario = iter(reservar_valores_sequence(cursor_web, sequence_usuario, len(usuarios_giv)))
    for reg in usuarios_giv:
        cd_usuario_web = next(codigos_usuario)
        usuarios_web.append(
            converter_usuario(
                reg,
                tenant_id,
                cd_empresa,
                cidades_giv,
                cidades_web,
                cd_usuario_web
            )
        )
        mapa_usuarios[reg.get('cd_usuario')] = cd_usuario_web

    print(f"[OK] {len(usuarios_web)} usuarios para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_usuario, usuarios_web, 'usuario')

    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_usuario,
        usuarios_web,
        'cd_usuario',
        'sp_usuario'
    )

    cds_com_erro = set()
    for detalhe in erros_detalhe:
        match = re.search(r"cd_usuario=(\d+)", detalhe)
        if match:
            cds_com_erro.add(int(match.group(1)))

    mapa_usuarios_ok = {
        cd_origem: cd_web
        for cd_origem, cd_web in mapa_usuarios.items()
        if cd_web not in cds_com_erro
    }

    return {
        'tabela': 'usuario',
        'lidos': len(usuarios_giv),
        'inseridos': inseridos,
        'existentes': 0,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
        'mapa_usuarios': mapa_usuarios_ok,
    }


def mapear_ativo(cd_categoria):
    """
    Mapeia cd_categoria do GIV para o campo 'ativo' do Web.
    2 = normal (ativo) → True/1
    5 = inativo → False/0
    Outros valores → True/1 (assume ativo por padrão)
    """
    if cd_categoria == 5:
        return 'N'
    return 'S'


def buscar_clientes_giv(cursor_giv, cd_empresa_giv=None):
    """Busca todos os clientes do banco GIV."""
    where_sql = ""
    params = []
    if cd_empresa_giv is not None:
        where_sql = "WHERE c.cd_empresa = ?"
        params.append(cd_empresa_giv)

    coluna_cgc = "c.cgc" if tabela_giv_tem_coluna(cursor_giv, 'cliente', 'cgc') else "NULL"
    coluna_cnpj = "c.cnpj" if tabela_giv_tem_coluna(cursor_giv, 'cliente', 'cnpj') else "NULL"
    coluna_ie = (
        "c.inscricao_estadual"
        if tabela_giv_tem_coluna(cursor_giv, 'cliente', 'inscricao_estadual')
        else "c.rg"
    )
    coluna_profissao = "c.profissao" if tabela_giv_tem_coluna(cursor_giv, 'cliente', 'profissao') else "NULL"
    tem_cd_profissao = tabela_giv_tem_coluna(cursor_giv, 'cliente', 'cd_profissao')
    tem_tabela_profissao = tabela_giv_tem_coluna(cursor_giv, 'profissao', 'ds_profissao')
    join_profissao = ""
    profissao_sql = coluna_profissao
    if tem_cd_profissao and tem_tabela_profissao:
        join_profissao = "LEFT JOIN profissao p ON p.cd_profissao = c.cd_profissao"
        profissao_sql = (
            f"CASE WHEN p.ds_profissao IS NOT NULL AND TRIM(p.ds_profissao) <> '' "
            f"THEN p.ds_profissao ELSE {coluna_profissao} END"
        )

    coluna_conjuge = "c.conjuge" if tabela_giv_tem_coluna(cursor_giv, 'cliente', 'conjuge') else "NULL"
    tem_cliente_conjuge = tabela_giv_tem_coluna(cursor_giv, 'cliente_conjuge', 'nm_conjuge')
    tem_cd_conjuge = (
        tabela_giv_tem_coluna(cursor_giv, 'cliente', 'cd_conjuge')
        and tabela_giv_tem_coluna(cursor_giv, 'cliente', 'cd_empresa_conjuge')
    )
    joins_conjuge = []
    conjuge_fallbacks = []
    if tem_cliente_conjuge:
        joins_conjuge.append(
            "LEFT JOIN cliente_conjuge cc "
            "ON cc.cd_cliente = c.cd_cliente AND cc.cd_empresa = c.cd_empresa"
        )
        conjuge_fallbacks.append("cc.nm_conjuge")
        if tem_cd_conjuge:
            joins_conjuge.append(
                "LEFT JOIN cliente_conjuge cc_ref "
                "ON cc_ref.cd_cliente = c.cd_conjuge AND cc_ref.cd_empresa = c.cd_empresa_conjuge"
            )
            conjuge_fallbacks.append("cc_ref.nm_conjuge")

    conjuge_sql = coluna_conjuge
    for fallback in conjuge_fallbacks:
        conjuge_sql = (
            f"CASE WHEN {conjuge_sql} IS NOT NULL AND TRIM({conjuge_sql}) <> '' "
            f"THEN {conjuge_sql} ELSE {fallback} END"
        )

    sql = """
        SELECT
            c.cd_cliente,
            c.cd_categoria,
            c.cd_empresa,
            c.cd_cidade,
            c.cep,
            c.cd_usuario,
            c.nm_cliente,
            c.rg,
            {coluna_ie} AS inscricao_estadual_giv,
            c.ds_numero,
            c.tp_pessoa,
            c.endereco,
            c.ds_complemento,
            c.bairro,
            c.fone,
            c.celular,
            c.dt_nascimento,
            c.cpf,
            {coluna_cgc} AS cgc,
            {coluna_cnpj} AS cnpj,
            c.filiacao_pai,
            c.filiacao_mae,
            c.naturalidade,
            c.ds_residencia_ant,
            c.dt_residencia,
            c.id_residencia,
            c.vl_aluguel,
            c.dt_cadastro,
            c.estado_civil,
            {conjuge_sql} AS conjuge,
            c.empresa_trab,
            c.endereco_trab,
            c.bairro_trab,
            c.cd_cidade_trab,
            c.cep_trab,
            c.fone_trab,
            c.fax_trab,
            {profissao_sql} AS profissao,
            c.dt_admissao,
            c.vl_salario,
            c.vl_outras_rendas,
            c.obs1,
            c.spc,
            c.razao_social,
            c.vl_limite_disponivel,
            c.proprietario_casa,
            c.email,
            c.dt_atualizacao,
            c.id_sexo
        FROM cliente c
        {join_profissao}
        {join_conjuge}
        {where_sql}
        ORDER BY c.cd_cliente
    """.format(
        where_sql=where_sql,
        coluna_cgc=coluna_cgc,
        coluna_cnpj=coluna_cnpj,
        coluna_ie=coluna_ie,
        profissao_sql=profissao_sql,
        conjuge_sql=conjuge_sql,
        join_profissao=join_profissao,
        join_conjuge="\n        ".join(joins_conjuge)
    )
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def converter_cliente(
    registro_giv,
    tenant_id,
    cidades_giv,
    cidades_web,
    cd_cliente_web,
    cd_empresa,
    ddd_por_cidade=None
):
    """
    Converte um registro do GIV para o formato do Web (PostgreSQL),
    seguindo o mapeamento definido no cliente.txt.
    """
    tp_pessoa = registro_giv.get('tp_pessoa')

    tipo_pessoa = str(tp_pessoa).strip().upper() if tp_pessoa is not None else ''

    # Para PJ no Web: rg guarda a IE e tp_contribuinte guarda I/NC/C.
    inscricao_estadual = None
    tp_contribuinte = None
    rg_web = None
    if tipo_pessoa == 'J':
        tp_contribuinte, rg_web = classificar_contribuinte_cliente_pj(
            registro_giv.get('inscricao_estadual_giv') or registro_giv.get('rg')
        )

    documento_nacional = normalizar_documento_cliente(
        tp_pessoa,
        registro_giv.get('cpf'),
        registro_giv.get('cgc'),
        registro_giv.get('cnpj')
    )

    # rg no web = rg do giv para pessoa física.
    if tipo_pessoa == 'F':
        rg_web = registro_giv.get('rg')

    # cd_cidade_trab: converter sequencial GIV -> codigo IBGE Web
    cd_cidade_trab_web = resolver_cidade_trab(
        registro_giv.get('cd_cidade_trab'),
        cidades_giv,
        cidades_web,
        f"cliente cd_cliente={registro_giv.get('cd_cliente')} cidade_trab"
    )

    # cd_cidade: tambem precisa converter sequencial GIV -> codigo IBGE Web
    cd_cidade_web = resolver_cidade_trab(
        registro_giv.get('cd_cidade'),
        cidades_giv,
        cidades_web,
        f"cliente cd_cliente={registro_giv.get('cd_cliente')}"
    )
    ddd_cidade = resolver_ddd_cidade_giv(
        registro_giv.get('cd_cidade'),
        cidades_giv,
        ddd_por_cidade
    )
    ddd_cidade_trab = resolver_ddd_cidade_giv(
        registro_giv.get('cd_cidade_trab'),
        cidades_giv,
        ddd_por_cidade
    ) if registro_giv.get('cd_cidade_trab') is not None else ddd_cidade

    # ativo: baseado em cd_categoria
    ativo = mapear_ativo(registro_giv.get('cd_categoria'))

    registro_web = {
        'cd_cliente':           cd_cliente_web,
        'ativo':                ativo,
        'cd_empresa':           cd_empresa,
        'cd_cidade':            cd_cidade_web,
        'cep':                  normalizar_cep(registro_giv.get('cep')),
        'cd_usuario':           registro_giv.get('cd_usuario'),
        'nm_cliente':           registro_giv.get('nm_cliente'),
        'inscricao_estadual':   inscricao_estadual,
        'tp_contribuinte':      tp_contribuinte,
        'nr_residencia':        registro_giv.get('ds_numero'),
        'tp_pessoa':            tp_pessoa,
        'endereco':             registro_giv.get('endereco'),
        'ds_complemento':       registro_giv.get('ds_complemento'),
        'bairro':               registro_giv.get('bairro'),
        'fone':                 normalizar_telefone_web(registro_giv.get('fone'), ddd=ddd_cidade),
        'celular':              normalizar_celular_web(registro_giv.get('celular'), ddd=ddd_cidade),
        'dt_nascimento':        data_fundacao_cliente_pj(registro_giv)
                                if tipo_pessoa == 'J'
                                else registro_giv.get('dt_nascimento'),
        'cpf':                  documento_nacional,
        'rg':                   rg_web,
        'filiacao_pai':         registro_giv.get('filiacao_pai'),
        'filiacao_mae':         registro_giv.get('filiacao_mae'),
        'naturalidade':         registro_giv.get('naturalidade'),
        'ds_residencia_ant':    registro_giv.get('ds_residencia_ant'),
        'dt_residencia':        registro_giv.get('dt_residencia'),
        'id_residencia':        registro_giv.get('id_residencia'),
        'vl_aluguel':           registro_giv.get('vl_aluguel'),
        'dt_cadastro':          registro_giv.get('dt_cadastro'),
        'estado_civil':         normalizar_estado_civil_cliente(registro_giv.get('estado_civil')),
        'conjuge':              registro_giv.get('conjuge'),
        'empresa_trab':         registro_giv.get('empresa_trab'),
        'endereco_trab':        registro_giv.get('endereco_trab'),
        'bairro_trab':          registro_giv.get('bairro_trab'),
        'cd_cidade_trab':       cd_cidade_trab_web,
        'cep_trab':             normalizar_cep(registro_giv.get('cep_trab')),
        'fone_trab':            normalizar_telefone_web(registro_giv.get('fone_trab'), ddd=ddd_cidade_trab),
        'fax_trab':             registro_giv.get('fax_trab'),
        'profissao':            registro_giv.get('profissao'),
        'dt_admissao':          registro_giv.get('dt_admissao'),
        'vl_salario':           registro_giv.get('vl_salario'),
        'vl_outras_rendas':     registro_giv.get('vl_outras_rendas'),
        'cd_categoria':         None,  # sempre null conforme mapeamento
        'obs1':                 registro_giv.get('obs1'),
        'spc':                  registro_giv.get('spc'),
        'razao_social':         registro_giv.get('razao_social'),
        'vl_limite_disponivel': registro_giv.get('vl_limite_disponivel'),
        'proprietario_casa':    registro_giv.get('proprietario_casa'),
        'tenant_id':            tenant_id,
        'email':                registro_giv.get('email'),
        'dt_atualizacao':       registro_giv.get('dt_atualizacao'),
        'sexo':                 registro_giv.get('id_sexo'),
    }

    # Limpar espacos extras de campos string (SQL Anywhere preenche CHAR com espacos)
    for chave, valor in registro_web.items():
        if isinstance(valor, str):
            registro_web[chave] = valor.strip() or None

    return registro_web


def buscar_clientes_existentes_web(cursor_web):
    """Busca os cd_cliente que ja existem no banco Web para evitar duplicatas."""
    try:
        cursor_web.execute("SELECT cd_cliente, cd_empresa FROM cliente")
        existentes = set()
        for row in cursor_web.fetchall():
            existentes.add((row[0], row[1]))
        return existentes
    except Exception as e:
        print(f"[AVISO] Erro ao buscar clientes existentes: {e}")
        return set()


def buscar_clientes_existentes_web_sequencial(cursor_web, tabela_web_cliente, tenant_id):
    """Busca documentos e nomes ja existentes para importar cliente com codigo sequencial."""
    cursor_web.execute("SAVEPOINT sp_docs_cliente")
    try:
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador('cpf')},
                {quote_identificador('nm_cliente')}
              FROM {tabela_web_cliente}
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('cd_empresa')} = %s
            """,
            (tenant_id, CD_EMPRESA_PADRAO)
        )
        documentos = set()
        nomes = set()
        for row in cursor_web.fetchall():
            documento = normalizar_cadastro_nacional(row[0])
            nome = chave_texto(row[1])
            if documento:
                documentos.add(documento)
            if nome:
                nomes.add(nome)
        cursor_web.execute("RELEASE SAVEPOINT sp_docs_cliente")
        return documentos, nomes
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_docs_cliente")
            cursor_web.execute("RELEASE SAVEPOINT sp_docs_cliente")
        except Exception:
            pass
        print(f"[AVISO] Erro ao buscar clientes existentes em {tabela_web_cliente}: {e}")
        return set(), set()


def inserir_clientes_web(cursor_web, clientes_web):
    """Compatibilidade com fluxo antigo: usa o insert em lote atual."""
    return inserir_registros_web(
        cursor_web,
        'cliente',
        clientes_web,
        'cd_cliente',
        'sp_cliente'
    )


def processar_clientes(
    cursor_giv,
    cursor_web,
    tabela_web_cliente,
    cidades_giv,
    cidades_web,
    tenant_id,
    cd_empresa,
    session_api_cep,
    base_url_api_cep,
    mapa_usuarios=None,
    cd_empresa_giv=None
):
    print()
    print("[INFO] Cliente: registros existentes no Web nao serao usados para pular a importacao.")
    if cd_empresa_giv is not None:
        print(f"[INFO] Cliente: filtrando origem GIV por cd_empresa={cd_empresa_giv}.")

    sequence_cliente = buscar_sequence_coluna_web(
        cursor_web,
        tabela_web_cliente,
        'cd_cliente'
    )
    max_cliente = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_cliente,
        'cd_cliente',
        sequence_cliente
    )
    print(f"[OK] Sequence de cliente: {sequence_cliente} (sincronizada com max={max_cliente}).")

    usuarios_existentes = buscar_usuarios_existentes_web(cursor_web)
    print(f"[OK] {len(usuarios_existentes)} usuarios carregados para validar clientes.")
    if mapa_usuarios:
        print(f"[OK] {len(mapa_usuarios)} usuarios convertidos nesta execucao serao usados no cliente.")

    print()
    print("[...] Buscando clientes do banco GIV...")
    clientes_giv = buscar_clientes_giv(cursor_giv, cd_empresa_giv)
    print(f"[OK] {len(clientes_giv)} clientes encontrados no GIV.")
    ddd_por_cidade = montar_mapa_ddd_por_cidade_clientes(clientes_giv)
    if ddd_por_cidade:
        print(f"[OK] DDD inferido para {len(ddd_por_cidade)} cidades a partir dos telefones do GIV.")

    clientes_web = []
    pulados = 0
    usuarios_anulados = 0
    codigos_cliente = iter(reservar_valores_sequence(cursor_web, sequence_cliente, len(clientes_giv)))
    for reg in clientes_giv:
        cd_cliente_web = next(codigos_cliente)
        cliente = converter_cliente(
            reg,
            tenant_id,
            cidades_giv,
            cidades_web,
            cd_cliente_web,
            cd_empresa,
            ddd_por_cidade
        )
        cd_usuario_origem = cliente.get('cd_usuario')
        if mapa_usuarios is not None:
            cliente['cd_usuario'] = mapa_usuarios.get(cd_usuario_origem)
            if cd_usuario_origem is not None and cliente['cd_usuario'] is None:
                usuarios_anulados += 1
        elif cliente.get('cd_usuario') not in usuarios_existentes:
            if cliente.get('cd_usuario') is not None:
                usuarios_anulados += 1
            cliente['cd_usuario'] = None
        clientes_web.append(cliente)

    print(f"[OK] {len(clientes_web)} clientes para inserir (nenhum cliente foi pulado por ja existir no Web).")
    if usuarios_anulados:
        print(f"[AVISO] {usuarios_anulados} clientes estavam com cd_usuario inexistente no Web; cd_usuario ficou NULL.")

    garantir_ceps_cliente_api(clientes_web, cursor_web, session_api_cep, base_url_api_cep)
    aplicar_limites_texto_web(cursor_web, tabela_web_cliente, clientes_web, 'cliente')

    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_cliente,
        clientes_web,
        'cd_cliente',
        'sp_cliente'
    )

    return {
        'tabela': 'cliente',
        'lidos': len(clientes_giv),
        'inseridos': inseridos,
        'existentes': pulados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def buscar_produtos_giv(cursor_giv):
    """Busca campos necessarios da tabela produto GIV."""
    sql = """
        SELECT
            p.cd_produto,
            p.ds_produto,
            p.ds_abreviacao,
            p.cd_marca,
            p.cd_cor,
            p.cd_fornecedor,
            p.id_situacao,
            p.pr_icms,
            p.cd_classificacao_fiscal,
            p.cd_unidade,
            p.id_saldo_negativo,
            p.cd_grupo,
            p.cd_referencia,
            p.cd_tamanho,
            p.obs,
            p.pr_comissao,
            p.cd_situacao_tributaria,
            p.pr_margem_substituicao,
            p.id_sexo,
            p.cd_grade,
            p.cd_produto_pai,
            p.id_decimal,
            p.id_libera_qtd,
            p.cd_moeda,
            p.peso_bruto,
            p.dt_entrada,
            p.foto_produto,
            p.colecao,
            p.autor,
            p.cd_genero,
            p.cd_editora,
            p.peso_liquido,
            p.id_tipo_produto,
            p.cubagem,
            p.pr_reducao_icms,
            p.pr_icms_original,
            p.cd_situacao_pis,
            p.cd_situacao_cofins,
            p.fator_venda,
            p.cd_situacao_ipi,
            p.cd_lista_servico,
            p.cd_unidade_embalagem,
            p.ano_colecao,
            p.cd_grupo_moda,
            p.cd_subgrupo_moda,
            p.cd_genero_moda,
            p.nr_volume,
            p.cd_cest,
            p.id_produto_montado,
            p.cd_barra_loja_virtual,
            p.cd_colecao,
            p.id_sob_encomenda,
            p.id_desconto,
            p.id_pre_cadastro,
            p.cd_beneficio_fiscal
        FROM produto p
        ORDER BY
            CASE WHEN COALESCE(p.cd_produto_pai, 0) = 0 THEN 0 ELSE 1 END,
            p.cd_produto
    """
    produtos = buscar_registros_giv(cursor_giv, sql)
    return aplicar_limite_produtos(produtos)


def aplicar_limite_produtos(produtos):
    """
    Aplica --limit-products mantendo a familia intacta: pega as N primeiras
    raizes e leva junto todos os filhos delas, para o teste cobrir produto
    simples, produto pai e produtos filhos.
    """
    if not LIMITE_PRODUTOS_CONVERSAO or LIMITE_PRODUTOS_CONVERSAO <= 0:
        return produtos

    raizes = []
    for reg in produtos:
        if not reg.get('cd_produto_pai'):
            raizes.append(reg.get('cd_produto'))
            if len(raizes) >= LIMITE_PRODUTOS_CONVERSAO:
                break

    codigos_raiz = set(raizes)
    selecionados = [
        reg for reg in produtos
        if reg.get('cd_produto') in codigos_raiz
        or reg.get('cd_produto_pai') in codigos_raiz
    ]
    print(
        f"[INFO] --limit-products={LIMITE_PRODUTOS_CONVERSAO}: {len(selecionados)} produtos "
        f"selecionados ({len(codigos_raiz)} raizes + filhos) de {len(produtos)} do GIV."
    )
    return selecionados


def buscar_precos_produto_giv(cursor_giv):
    """Busca itens de tabela de preco do GIV agrupados por produto."""
    sql = """
        SELECT
            t.cd_tabela_preco,
            t.cd_produto,
            t.vl_compra,
            t.vl_ult_compra,
            t.pr_desconto1,
            t.pr_desconto2,
            t.pr_desconto3,
            t.pr_desconto4,
            t.pr_desconto5,
            t.pr_ipi,
            t.vl_frete,
            t.vl_outras_despesas,
            t.vl_custo,
            t.vl_custo_medio,
            t.pr_margem_lucro,
            t.vl_venda,
            t.vl_promocao,
            t.vl_promocao_aprazo,
            t.dt_promocao_inicio,
            t.dt_promocao_fim,
            t.cd_condicao_pagto,
            t.cd_condicao_pagto_promocao,
            t.pr_juros,
            t.vl_parcela,
            t.pr_juros_promocao,
            t.vl_parcela_promocao,
            t.vl_venda_atacado,
            t.pr_margem_atacado
        FROM tabela_preco_item t
        ORDER BY t.cd_produto, t.cd_tabela_preco
    """
    precos = {}
    for reg in buscar_registros_giv(cursor_giv, sql):
        precos.setdefault(reg.get('cd_produto'), []).append(reg)
    return precos


def buscar_estoques_produto_giv(cursor_giv, cd_empresa_origem):
    """Busca saldo_estoque do GIV somente da empresa selecionada."""
    sql = f"""
        SELECT
            s.cd_empresa,
            s.cd_produto,
            s.qt_fisico,
            s.qt_disponivel,
            s.qt_reservado,
            s.qt_transito,
            s.qt_pendente,
            s.qt_especial,
            s.qt_minimo_compra,
            s.qt_maximo_compra,
            s.qt_minimo_reposicao,
            s.qt_maximo_reposicao
        FROM saldo_estoque s
        WHERE s.cd_empresa = {int(cd_empresa_origem)}
        ORDER BY s.cd_produto
    """
    return {
        reg.get('cd_produto'): reg
        for reg in buscar_registros_giv(cursor_giv, sql)
    }


def normalizar_barcode(valor):
    """Normaliza barcode aceito pelo cadastro web."""
    digitos = somente_digitos(valor)
    if digitos and 6 <= len(digitos) <= 14:
        return digitos
    return None


def buscar_barcodes_gtin_giv(cursor_giv):
    """Busca GTIN ativo por produto; mantido como fallback quando produto_barra nao tiver codigo."""
    sql = """
        SELECT
            g.cd_produto,
            g.cd_gtin,
            g.id_ativo
        FROM produto_barra_gtin g
        ORDER BY g.cd_produto, g.cd_gtin
    """
    candidatos = {}
    conflitos = set()
    for reg in buscar_registros_giv(cursor_giv, sql):
        if valor_flag(reg.get('id_ativo')) not in ('S', 'A', 'T', '1'):
            continue
        barcode = normalizar_barcode(reg.get('cd_gtin'))
        if not barcode:
            continue
        cd_produto = reg.get('cd_produto')
        atual = candidatos.get(cd_produto)
        if atual and atual != barcode:
            conflitos.add(cd_produto)
            continue
        candidatos[cd_produto] = barcode

    for cd_produto in conflitos:
        candidatos.pop(cd_produto, None)
    if conflitos:
        print(f"[AVISO] {len(conflitos)} produtos com mais de um GTIN ativo ficaram sem barcode.")
    return candidatos


def buscar_barcodes_produto_barra_giv(cursor_giv):
    """Busca codigo de barras da tabela produto_barra do GIV."""
    sql = """
        SELECT
            pb.cd_produto,
            pb.cd_barra,
            pb.dt_manutencao
        FROM produto_barra pb
        WHERE pb.cd_barra IS NOT NULL
        ORDER BY pb.cd_produto, pb.dt_manutencao DESC, pb.cd_barra
    """
    candidatos = {}
    alternativos = 0
    barcode_para_produtos = {}

    for reg in buscar_registros_giv(cursor_giv, sql):
        barcode = normalizar_barcode(reg.get('cd_barra'))
        if not barcode:
            continue
        cd_produto = reg.get('cd_produto')
        if cd_produto not in candidatos:
            candidatos[cd_produto] = barcode
            barcode_para_produtos.setdefault(barcode, set()).add(cd_produto)
            continue

        if candidatos[cd_produto] != barcode:
            alternativos += 1

    duplicados = {
        barcode
        for barcode, produtos in barcode_para_produtos.items()
        if len(produtos) > 1
    }
    if duplicados:
        candidatos = {
            cd_produto: barcode
            for cd_produto, barcode in candidatos.items()
            if barcode not in duplicados
        }
        print(
            f"[AVISO] {len(duplicados)} codigos de barras repetidos em mais de um produto "
            "foram ignorados."
        )
    if alternativos:
        print(
            f"[AVISO] {alternativos} codigos alternativos em produto_barra foram ignorados; "
            "foi usado o mais recente por produto."
        )

    print(f"[OK] {len(candidatos)} barcodes carregados de produto_barra.")
    return candidatos


def buscar_barcodes_produto_giv(cursor_giv):
    """Busca barcodes do GIV priorizando produto_barra e usando GTIN como fallback."""
    barcodes = buscar_barcodes_produto_barra_giv(cursor_giv)
    barcodes_gtin = buscar_barcodes_gtin_giv(cursor_giv)
    adicionados_gtin = 0
    for cd_produto, barcode in barcodes_gtin.items():
        if cd_produto not in barcodes:
            barcodes[cd_produto] = barcode
            adicionados_gtin += 1
    if adicionados_gtin:
        print(f"[OK] {adicionados_gtin} barcodes complementados via produto_barra_gtin.")

    por_barcode = {}
    for cd_produto, barcode in barcodes.items():
        por_barcode.setdefault(barcode, set()).add(cd_produto)
    duplicados = {
        barcode
        for barcode, produtos in por_barcode.items()
        if len(produtos) > 1
    }
    if duplicados:
        barcodes = {
            cd_produto: barcode
            for cd_produto, barcode in barcodes.items()
            if barcode not in duplicados
        }
        print(
            f"[AVISO] {len(duplicados)} barcodes duplicados apos fallback foram ignorados."
        )
    return barcodes


def filtrar_barcodes_existentes_web(cursor_web, tabelas_web, tenant_id, barcodes_por_produto):
    """Remove barcodes que ja existem no Web para evitar conflito de importacao."""
    if not barcodes_por_produto:
        return barcodes_por_produto

    existentes = set()
    consultas = [
        (tabelas_web.get('produto'), 'cd_produto_barra_ean'),
        (tabelas_web.get('produto_filho'), 'cd_produto_barra_ean'),
    ]
    cursor_web.execute("SAVEPOINT sp_barcodes_web")
    try:
        for tabela_web, coluna in consultas:
            if not tabela_web:
                continue
            cursor_web.execute(
                f"""
                SELECT {quote_identificador(coluna)}
                  FROM {tabela_web}
                 WHERE {quote_identificador('tenant_id')} = %s
                   AND {quote_identificador(coluna)} IS NOT NULL
                """,
                (tenant_id,)
            )
            for row in cursor_web.fetchall():
                barcode = normalizar_barcode(row[0])
                if barcode:
                    existentes.add(barcode)
        cursor_web.execute("RELEASE SAVEPOINT sp_barcodes_web")
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_barcodes_web")
            cursor_web.execute("RELEASE SAVEPOINT sp_barcodes_web")
        except Exception:
            pass
        print(f"[AVISO] Nao foi possivel validar barcodes existentes no Web: {e}")
        return barcodes_por_produto

    filtrados = {
        cd_produto: barcode
        for cd_produto, barcode in barcodes_por_produto.items()
        if barcode not in existentes
    }
    removidos = len(barcodes_por_produto) - len(filtrados)
    if removidos:
        print(f"[AVISO] {removidos} barcodes ja existentes no Web foram ignorados.")
    return filtrados


def classificar_produtos_giv(produtos_giv):
    """Classifica produtos em raiz/filho e tipo web."""
    codigos_com_filhos = {
        reg.get('cd_produto_pai')
        for reg in produtos_giv
        if reg.get('cd_produto_pai') not in (None, 0)
    }
    classificacao = {}
    for reg in produtos_giv:
        cd_produto = reg.get('cd_produto')
        cd_pai = reg.get('cd_produto_pai') or 0
        if cd_pai:
            classificacao[cd_produto] = {
                'tipo_origem': 'filho',
                'tipo_web': 'F',
                'eh_filho': True,
                'eh_raiz': False,
            }
            continue

        cd_grade = reg.get('cd_grade')
        eh_pai_grade = cd_produto in codigos_com_filhos or cd_grade in (2, 9, 10)
        classificacao[cd_produto] = {
            'tipo_origem': 'pai_grade' if eh_pai_grade else 'simples',
            'tipo_web': 'F' if eh_pai_grade else 'P',
            'eh_filho': False,
            'eh_raiz': True,
        }
    return classificacao


def carregar_mapas_cadastros_produto(
    cursor_giv,
    cursor_web,
    tabelas_web,
    tenant_id,
    cd_empresa,
    mapa_grupos_convertidos=None,
    cd_empresa_giv=None
):
    """Carrega mapas GIV -> Web dos cadastros auxiliares ja existentes/convertidos."""
    mapas = {}
    mapas['marca'] = buscar_mapa_giv_para_web_por_nome(
        cursor_giv,
        cursor_web,
        sql_mapa_giv_auxiliar(cursor_giv, 'marca', 'cd_marca', 'ds_marca', cd_empresa_giv),
        'cd_marca',
        'ds_marca',
        tabelas_web['marca'],
        'cd_marca',
        'ds_marca',
        tenant_id=tenant_id
    )
    mapas['cor'] = buscar_mapa_giv_para_web_por_nome(
        cursor_giv,
        cursor_web,
        sql_mapa_giv_auxiliar(cursor_giv, 'cor', 'cd_cor', 'ds_cor', cd_empresa_giv),
        'cd_cor',
        'ds_cor',
        tabelas_web['cor'],
        'cd_cor',
        'ds_cor',
        tenant_id=tenant_id
    )
    mapas['grupo'] = buscar_mapa_giv_para_web_por_nome(
        cursor_giv,
        cursor_web,
        sql_mapa_giv_auxiliar(cursor_giv, 'Grupo_moda', 'cd_grupo', 'ds_grupo', cd_empresa_giv),
        'cd_grupo',
        'ds_grupo',
        tabelas_web['grupo'],
        'cd_grupo',
        'ds_grupo',
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    mapas['grupo'].update(
        buscar_mapa_grupo_produto_para_web(
            cursor_giv,
            cursor_web,
            tabelas_web['grupo'],
            tenant_id,
            cd_empresa,
            mapa_grupos_convertidos,
            cd_empresa_giv
        )
    )
    if mapa_grupos_convertidos:
        mapas['grupo'].update(mapa_grupos_convertidos)

    mapas['departamento'] = buscar_mapa_giv_para_web_por_nome(
        cursor_giv,
        cursor_web,
        sql_mapa_giv_auxiliar(cursor_giv, 'genero_moda', 'cd_genero_moda', 'ds_genero_moda', cd_empresa_giv),
        'cd_genero_moda',
        'ds_genero_moda',
        tabelas_web['departamento'],
        'cd_departamento',
        'ds_departamento',
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    mapas['genero_por_grupo_produto'] = buscar_genero_por_grupo_produto_giv(cursor_giv, cd_empresa_giv)
    mapas['sub_grupo'] = buscar_mapa_giv_para_web_por_nome(
        cursor_giv,
        cursor_web,
        sql_mapa_giv_auxiliar(cursor_giv, 'subgrupo_moda', 'cd_subgrupo', 'ds_subgrupo', cd_empresa_giv),
        'cd_subgrupo',
        'ds_subgrupo',
        tabelas_web['sub_grupo'],
        'cd_sub_grupo',
        'ds_sub_grupo',
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    mapas['sub_grupo'].update(
        buscar_mapa_subgrupo_produto_para_web(
            cursor_giv,
            cursor_web,
            tabelas_web['sub_grupo'],
            tenant_id,
            cd_empresa,
            cd_empresa_giv
        )
    )
    mapas['fornecedor'] = buscar_mapa_fornecedor_produto(
        cursor_giv,
        cursor_web,
        tabelas_web['fornecedor'],
        tenant_id,
        cd_empresa_giv
    )
    print(
        "[OK] Mapas produto: "
        f"marca={len(mapas['marca'])}, cor={len(mapas['cor'])}, "
        f"grupo={len(mapas['grupo'])}, departamento={len(mapas['departamento'])}, "
        f"genero_por_grupo={len(mapas.get('genero_por_grupo_produto', {}))}, "
        f"sub_grupo={len(mapas['sub_grupo'])}, fornecedor={len(mapas['fornecedor'])}."
    )
    return mapas


def converter_produto_preco(preco_giv, cd_tabela_preco_web, cd_produto_web, tenant_id):
    """Converte um item de preco GIV para produto_preco Web."""
    return limpar_registro({
        'cd_tabela_preco': cd_tabela_preco_web,
        'cd_produto': cd_produto_web,
        'id_status': 'A',
        'vl_compra': valor_decimal_ou_zero(preco_giv.get('vl_compra')),
        'vl_ult_compra': valor_decimal_ou_zero(preco_giv.get('vl_ult_compra')),
        'pr_desconto1': valor_decimal_ou_zero(preco_giv.get('pr_desconto1')),
        'pr_desconto2': valor_decimal_ou_zero(preco_giv.get('pr_desconto2')),
        'pr_desconto3': valor_decimal_ou_zero(preco_giv.get('pr_desconto3')),
        'pr_desconto4': valor_decimal_ou_zero(preco_giv.get('pr_desconto4')),
        'pr_desconto5': valor_decimal_ou_zero(preco_giv.get('pr_desconto5')),
        'pr_ipi': valor_decimal_ou_zero(preco_giv.get('pr_ipi')),
        'vl_frete': valor_decimal_ou_zero(preco_giv.get('vl_frete')),
        'vl_venda': valor_decimal_ou_zero(preco_giv.get('vl_venda')),
        'tenant_id': tenant_id,
        'cd_condicao_pagto': preco_giv.get('cd_condicao_pagto'),
        'cd_condicao_pagto_promocao': preco_giv.get('cd_condicao_pagto_promocao'),
        'dt_promocao_fim': preco_giv.get('dt_promocao_fim'),
        'dt_promocao_inicio': preco_giv.get('dt_promocao_inicio'),
        'pr_acrescimo_custo': Decimal('0'),
        'pr_juros': valor_decimal_ou_zero(preco_giv.get('pr_juros')),
        'pr_juros_promocao': valor_decimal_ou_zero(preco_giv.get('pr_juros_promocao')),
        'pr_margem_atacado': valor_decimal_ou_zero(preco_giv.get('pr_margem_atacado')),
        'pr_margem_lucro': valor_decimal_ou_zero(preco_giv.get('pr_margem_lucro')),
        'vl_custo': valor_decimal_ou_zero(preco_giv.get('vl_custo')),
        'vl_custo_medio': valor_decimal_ou_zero(preco_giv.get('vl_custo_medio')),
        'vl_outras_despesas': valor_decimal_ou_zero(preco_giv.get('vl_outras_despesas')),
        'vl_parcela': valor_decimal_ou_zero(preco_giv.get('vl_parcela')),
        'vl_parcela_promocao': valor_decimal_ou_zero(preco_giv.get('vl_parcela_promocao')),
        'vl_promocao_aprazo': valor_decimal_ou_zero(preco_giv.get('vl_promocao_aprazo')),
        'vl_promocao_avista': valor_decimal_ou_zero(preco_giv.get('vl_promocao')),
        'vl_venda_atacado': valor_decimal_ou_zero(preco_giv.get('vl_venda_atacado')),
    })


def converter_produto_estoque(estoque_giv, cd_produto_web, tenant_id, cd_empresa):
    """Converte saldo_estoque GIV para produto_estoque Web."""
    estoque_giv = estoque_giv or {}
    return limpar_registro({
        'cd_empresa': cd_empresa,
        'cd_produto': cd_produto_web,
        'id_status': 'A',
        'qt_fisico': valor_decimal_ou_zero(estoque_giv.get('qt_fisico')),
        'qt_disponivel': valor_decimal_ou_zero(estoque_giv.get('qt_disponivel')),
        'qt_reservado': valor_decimal_ou_zero(estoque_giv.get('qt_reservado')),
        'qt_transito': valor_decimal_ou_zero(estoque_giv.get('qt_transito')),
        'qt_pendente': valor_decimal_ou_zero(estoque_giv.get('qt_pendente')),
        'qt_especial': valor_decimal_ou_zero(estoque_giv.get('qt_especial')),
        'qt_minimo_compra': valor_decimal_ou_zero(estoque_giv.get('qt_minimo_compra')),
        'qt_maximo_compra': valor_decimal_ou_zero(estoque_giv.get('qt_maximo_compra')),
        'qt_minimo_reposicao': valor_decimal_ou_zero(estoque_giv.get('qt_minimo_reposicao')),
        'qt_maximo_reposicao': valor_decimal_ou_zero(estoque_giv.get('qt_maximo_reposicao')),
        'tenant_id': tenant_id,
    })


def montar_produto_raiz_web(
    reg,
    cd_produto_web,
    tenant_id,
    mapas,
    mapa_status_produto,
    classificacao,
    barcode
):
    """Monta produto/produto_info de uma raiz ou simples, retornando erros bloqueantes."""
    cd_produto_giv = reg.get('cd_produto')
    tipo_web = classificacao[cd_produto_giv]['tipo_web']
    erros = []
    padroes = mapas.get('_padroes', {})

    cd_marca = valor_mapa_ou_padrao(mapas['marca'], reg.get('cd_marca'), padroes.get('cd_marca'))
    if cd_marca is None:
        cd_marca = padroes.get('cd_marca')
    cd_cor = valor_mapa_ou_padrao(mapas['cor'], reg.get('cd_cor'))
    cd_tamanho = valor_mapa_ou_padrao(mapas['tamanho'], reg.get('cd_tamanho'))
    cd_unidade = valor_mapa_ou_padrao(mapas['unidade'], reg.get('cd_unidade'))
    cd_unidade_embalagem = (
        valor_mapa_ou_padrao(mapas['unidade'], reg.get('cd_unidade_embalagem'))
        if reg.get('cd_unidade_embalagem') is not None
        else None
    )
    if cd_unidade_embalagem is None:
        cd_unidade_embalagem = cd_unidade
    cd_colecao = valor_mapa_ou_padrao(mapas['colecao'], reg.get('cd_colecao'))
    cd_fornecedor = valor_mapa_ou_padrao(mapas['fornecedor'], reg.get('cd_fornecedor'), padroes.get('cd_fornecedor'))
    if cd_fornecedor is None:
        cd_fornecedor = padroes.get('cd_fornecedor')
    cd_grupo = (
        valor_mapa_ou_padrao(mapas['grupo'], reg.get('cd_grupo_moda'))
        if reg.get('cd_grupo_moda') is not None
        else None
    )
    if cd_grupo is None:
        cd_grupo = valor_mapa_ou_padrao(mapas['grupo'], reg.get('cd_grupo'), padroes.get('cd_grupo'))
    if cd_grupo is None:
        cd_grupo = padroes.get('cd_grupo')
    cd_genero_moda = reg.get('cd_genero_moda')
    if cd_genero_moda is None:
        cd_genero_moda = valor_mapa_ou_padrao(
            mapas.get('genero_por_grupo_produto', {}),
            reg.get('cd_grupo')
        )
    cd_departamento = valor_mapa_ou_padrao(
        mapas['departamento'],
        cd_genero_moda
    )
    cd_sub_grupo = valor_mapa_ou_padrao(
        mapas['sub_grupo'],
        reg.get('cd_subgrupo_moda')
    )
    if cd_sub_grupo is None:
        cd_sub_grupo = valor_mapa_ou_padrao(mapas['sub_grupo'], reg.get('cd_grupo'))
    cd_grade = None
    if tipo_web == 'F':
        cd_grade = valor_mapa_ou_padrao(mapas['grade'], reg.get('cd_grade'))

    ds_produto = limpar_valor(reg.get('ds_produto')) or f"PRODUTO {cd_produto_giv}"
    ds_abreviacao = limpar_valor(reg.get('ds_abreviacao')) or ds_produto
    cd_ncm = normalizar_codigo_fiscal(reg.get('cd_classificacao_fiscal'), 8)
    cd_cest = normalizar_codigo_fiscal(reg.get('cd_cest'), 7)
    cd_situacao_tributaria = limpar_valor(reg.get('cd_situacao_tributaria'))
    if cd_situacao_tributaria:
        cd_situacao_tributaria = str(cd_situacao_tributaria)[:3]
    cd_situacao_tributaria = cd_situacao_tributaria or CD_SITUACAO_TRIBUTARIA_PRODUTO_PADRAO

    status_origem = valor_flag(reg.get('id_situacao'), 'I')
    id_status = mapa_status_produto.get(status_origem) or 'I'

    obrigatorios = {
        'ds_produto': ds_produto,
        'cd_marca': cd_marca,
        'grupoCd_grupo': cd_grupo,
        'cd_fornecedor': cd_fornecedor,
        'cd_situacao_tributaria': cd_situacao_tributaria,
        'id_status': id_status,
    }
    for campo, valor in obrigatorios.items():
        if valor is None:
            erros.append(f"{campo} sem mapeamento/valor")

    produto = limpar_registro({
        'cd_produto': cd_produto_web,
        'tenant_id': tenant_id,
        'id_tipo': tipo_web,
        'corCd_cor': cd_cor,
        'marcaCd_marca': cd_marca,
        'tamanhoCd_tamanho': cd_tamanho,
        'tenantId': tenant_id,
        'unidadeCd_unidade': cd_unidade,
        'cd_produto_barra_ean': barcode if tipo_web == 'P' else None,
        'colecaoCd_colecao': cd_colecao,
        'departamentoCd_departamento': cd_departamento,
        'grupoCd_grupo': cd_grupo,
    })
    produto_info = limpar_registro({
        'cd_produto': cd_produto_web,
        'id_status': id_status,
        'ds_produto': ds_produto,
        'ds_abreviacao': ds_abreviacao,
        'cd_genero_moda': cd_genero_moda,
        'cd_grupo_moda': reg.get('cd_grupo_moda'),
        'cd_subgrupo_moda': reg.get('cd_subgrupo_moda'),
        'cd_referencia': reg.get('cd_referencia'),
        'cd_colecao': cd_colecao,
        'ano_colecao': reg.get('ano_colecao'),
        'cd_marca': cd_marca,
        'cd_fornecedor': cd_fornecedor,
        'cd_cor': cd_cor,
        'cd_tamanho': cd_tamanho,
        'cd_unidade': cd_unidade,
        'cd_unidade_embalagem': cd_unidade_embalagem,
        'cd_ncm': cd_ncm,
        'cd_cest': cd_cest,
        'cd_beneficio_fiscal': reg.get('cd_beneficio_fiscal'),
        'cd_situacao_tributaria': cd_situacao_tributaria,
        'cd_grade': cd_grade,
        'cd_moeda': valor_inteiro_ou_zero(reg.get('cd_moeda')),
        'cd_situacao_pis': limpar_valor(reg.get('cd_situacao_pis')) or '00',
        'cd_situacao_cofins': limpar_valor(reg.get('cd_situacao_cofins')) or '00',
        'cd_situacao_ipi': limpar_valor(reg.get('cd_situacao_ipi')) or '00',
        'cd_lista_servico': valor_inteiro_ou_zero(reg.get('cd_lista_servico')),
        'id_saldo_negativo': valor_flag(reg.get('id_saldo_negativo')),
        'id_sexo': valor_flag(reg.get('id_sexo'), 'A'),
        'id_decimal': valor_flag(reg.get('id_decimal')),
        'id_libera_qtd': valor_flag(reg.get('id_libera_qtd')),
        'id_tipo_produto': valor_flag(reg.get('id_tipo_produto'), 'V'),
        'id_produto_montado': valor_flag(reg.get('id_produto_montado')),
        'id_sob_encomenda': valor_flag(reg.get('id_sob_encomenda')),
        'id_desconto': valor_flag(reg.get('id_desconto'), 'S'),
        'id_pre_cadastro': valor_flag(reg.get('id_pre_cadastro')),
        'pr_icms': valor_decimal_ou_zero(reg.get('pr_icms')),
        'pr_icms_original': valor_decimal_ou_zero(reg.get('pr_icms_original')),
        'pr_reducao_icms': valor_decimal_ou_zero(reg.get('pr_reducao_icms')),
        'pr_margem_substituicao': valor_decimal_ou_zero(reg.get('pr_margem_substituicao')),
        'pr_comissao': valor_decimal_ou_zero(reg.get('pr_comissao')),
        'cd_genero': reg.get('cd_genero'),
        'cd_editora': reg.get('cd_editora'),
        'colecao': reg.get('colecao'),
        'autor': reg.get('autor'),
        'obs': reg.get('obs'),
        'dt_entrada': reg.get('dt_entrada'),
        'foto_produto': reg.get('foto_produto'),
        'peso_bruto': valor_decimal_ou_zero(reg.get('peso_bruto')),
        'peso_liquido': valor_decimal_ou_zero(reg.get('peso_liquido')),
        'cubagem': valor_decimal_ou_zero(reg.get('cubagem')),
        'fator_venda': reg.get('fator_venda') or Decimal('1'),
        'nr_volume': valor_inteiro_ou_zero(reg.get('nr_volume')),
        'tenant_id': tenant_id,
        'produtoCd_produto': cd_produto_web,
        'produtoTenant_id': tenant_id,
        'cd_produto_barra_ean': barcode if tipo_web == 'P' else None,
        'cd_sub_grupo': cd_sub_grupo,
    })
    return produto, produto_info, erros


def montar_produto_filho_web(reg, cd_produto_web, tenant_id, mapas, mapa_produtos, barcode):
    """Monta produto/produto_filho de uma variacao."""
    erros = []
    padroes = mapas.get('_padroes', {})
    cd_pai_giv = reg.get('cd_produto_pai')
    cd_pai_web = mapa_produtos.get(cd_pai_giv)
    cd_cor = valor_mapa_ou_padrao(mapas['cor'], reg.get('cd_cor'))
    cd_tamanho = valor_mapa_ou_padrao(mapas['tamanho'], reg.get('cd_tamanho'))
    if cd_pai_web is None:
        erros.append(f"pai GIV {cd_pai_giv} sem de/para web")

    produto = limpar_registro({
        'cd_produto': cd_produto_web,
        'tenant_id': tenant_id,
        'id_tipo': 'F',
        'tenantId': tenant_id,
    })
    produto_filho = limpar_registro({
        'cd_produto_filho': cd_produto_web,
        'cd_produto_pai': cd_pai_web,
        'tenant_id': tenant_id,
        'cd_tamanho': cd_tamanho,
        'cd_cor': cd_cor,
        'cd_produto_barra_ean': barcode,
    })
    return produto, produto_filho, erros


def inserir_produto_raiz_transacional(
    cursor_web,
    tabelas_web,
    produto,
    produto_info,
    precos,
    estoque,
    produto_colecao,
    limites
):
    """Insere produto raiz e seus dependentes dentro de um savepoint unico."""
    aplicar_limites_texto_registro(produto, limites.get('produto'))
    aplicar_limites_texto_registro(produto_info, limites.get('produto_info'))
    for preco in precos:
        aplicar_limites_texto_registro(preco, limites.get('produto_preco'))
    aplicar_limites_texto_registro(estoque, limites.get('produto_estoque'))

    cursor_web.execute("SAVEPOINT sp_produto_raiz")
    etapa = 'produto'
    try:
        etapa = 'produto'
        inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto'], produto)
        etapa = 'produto_info'
        inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_info'], produto_info)
        for preco in precos:
            etapa = 'produto_preco'
            inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_preco'], preco)
        etapa = 'produto_estoque'
        inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_estoque'], estoque)
        if produto_colecao:
            etapa = 'produto_colecao'
            inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_colecao'], produto_colecao)
        cursor_web.execute("RELEASE SAVEPOINT sp_produto_raiz")
        return None
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_produto_raiz")
            cursor_web.execute("RELEASE SAVEPOINT sp_produto_raiz")
        except Exception:
            pass
        return f"{etapa}: {e}"


def inserir_produto_filho_transacional(
    cursor_web,
    tabelas_web,
    produto,
    produto_filho,
    precos,
    estoque,
    limites
):
    """Insere produto filho e seus dependentes dentro de um savepoint unico."""
    aplicar_limites_texto_registro(produto, limites.get('produto'))
    aplicar_limites_texto_registro(produto_filho, limites.get('produto_filho'))
    for preco in precos:
        aplicar_limites_texto_registro(preco, limites.get('produto_preco'))
    aplicar_limites_texto_registro(estoque, limites.get('produto_estoque'))

    cursor_web.execute("SAVEPOINT sp_produto_filho")
    etapa = 'produto'
    try:
        etapa = 'produto'
        inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto'], produto)
        etapa = 'produto_filho'
        inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_filho'], produto_filho)
        etapa = 'produto_preco'
        for preco in precos:
            inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_preco'], preco)
        etapa = 'produto_estoque'
        inserir_registro_web_sem_savepoint(cursor_web, tabelas_web['produto_estoque'], estoque)
        cursor_web.execute("RELEASE SAVEPOINT sp_produto_filho")
        return None
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_produto_filho")
            cursor_web.execute("RELEASE SAVEPOINT sp_produto_filho")
        except Exception:
            pass
        return f"{etapa}: {e}"


def preparar_item_produto_raiz_para_insert(item, limites):
    """Aplica limites de texto nos registros de um produto raiz antes do lote."""
    aplicar_limites_texto_registro(item['produto'], limites.get('produto'))
    aplicar_limites_texto_registro(item['produto_info'], limites.get('produto_info'))
    for preco in item['precos']:
        aplicar_limites_texto_registro(preco, limites.get('produto_preco'))
    aplicar_limites_texto_registro(item['estoque'], limites.get('produto_estoque'))


def preparar_item_produto_filho_para_insert(item, limites):
    """Aplica limites de texto nos registros de um produto filho antes do lote."""
    aplicar_limites_texto_registro(item['produto'], limites.get('produto'))
    aplicar_limites_texto_registro(item['produto_filho'], limites.get('produto_filho'))
    for preco in item['precos']:
        aplicar_limites_texto_registro(preco, limites.get('produto_preco'))
    aplicar_limites_texto_registro(item['estoque'], limites.get('produto_estoque'))


def inserir_produtos_raiz_lote_transacional(cursor_web, tabelas_web, itens, limites, tamanho_lote):
    """Insere produtos raiz/simples em lotes, mantendo fallback por produto."""
    inseridos = 0
    erros = 0
    erros_detalhe = []
    sucessos = []
    inicio = time.monotonic()
    ultimo_log_tempo = inicio
    ultimo_log_qtd = 0
    total = len(itens)

    for inicio_lote, lote in lotes(itens, tamanho_lote):
        fim_lote = inicio_lote + len(lote)
        for item in lote:
            preparar_item_produto_raiz_para_insert(item, limites)

        try:
            cursor_web.execute("SAVEPOINT sp_produto_raiz_lote")
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto'],
                [item['produto'] for item in lote]
            )
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto_info'],
                [item['produto_info'] for item in lote]
            )
            precos = []
            for item in lote:
                precos.extend(item['precos'])
            inserir_registros_multi_sem_savepoint(cursor_web, tabelas_web['produto_preco'], precos)
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto_estoque'],
                [item['estoque'] for item in lote]
            )
            colecoes = [item['produto_colecao'] for item in lote if item.get('produto_colecao')]
            inserir_registros_multi_sem_savepoint(cursor_web, tabelas_web['produto_colecao'], colecoes)
            cursor_web.execute("RELEASE SAVEPOINT sp_produto_raiz_lote")

            inseridos += len(lote)
            sucessos.extend(lote)
            agora = time.monotonic()
            if (
                fim_lote == total
                or fim_lote - ultimo_log_qtd >= LOG_PRODUTO_INTERVALO_REGISTROS
                or agora - ultimo_log_tempo >= LOG_PRODUTO_INTERVALO_SEGUNDOS
            ):
                print(
                    f"  Lotes raiz: {fim_lote}/{total} processados, "
                    f"{inseridos} inseridos em {int(agora - inicio)}s.",
                    flush=True
                )
                ultimo_log_tempo = agora
                ultimo_log_qtd = fim_lote
        except Exception as e:
            try:
                cursor_web.execute("ROLLBACK TO SAVEPOINT sp_produto_raiz_lote")
                cursor_web.execute("RELEASE SAVEPOINT sp_produto_raiz_lote")
            except Exception:
                pass

            global_, motivo = erro_e_global(e)
            if global_:
                raise ErroConfiguracaoConversao(
                    f"Lote raiz {inicio_lote + 1}-{fim_lote} falhou por causa global ({motivo}). "
                    f"Nenhum produto seria inserido item a item, entao a etapa foi interrompida "
                    f"antes de processar os {total} produtos. Causa raiz: {e}"
                )

            print(
                f"  [AVISO] Lote raiz {inicio_lote + 1}-{fim_lote} falhou em lote; "
                f"tentando item a item... Causa: {e}",
                flush=True
            )
            for item in lote:
                erro_insert = inserir_produto_raiz_transacional(
                    cursor_web,
                    tabelas_web,
                    item['produto'],
                    item['produto_info'],
                    item['precos'],
                    item['estoque'],
                    item.get('produto_colecao'),
                    limites
                )
                if erro_insert:
                    erros += 1
                    detalhe = f"cd_produto_giv={item['cd_produto_giv']}: {erro_insert}"
                    erros_detalhe.append(detalhe)
                    if erros <= 10:
                        print(f"  [PULO] produto raiz {detalhe}")
                    elif erros == 11:
                        print("  ... suprimindo demais erros de insert de produto")
                    continue

                inseridos += 1
                sucessos.append(item)

            print(
                f"  Lote raiz {fim_lote}/{total}: fallback concluido, "
                f"inseridos acumulados={inseridos}, erros_lote={erros}.",
                flush=True
            )
            ultimo_log_tempo = time.monotonic()
            ultimo_log_qtd = fim_lote

    return inseridos, erros, erros_detalhe, sucessos


def inserir_produtos_filhos_lote_transacional(cursor_web, tabelas_web, itens, limites, tamanho_lote):
    """Insere produtos filhos em lotes, mantendo fallback por produto."""
    inseridos = 0
    erros = 0
    erros_detalhe = []
    sucessos = []
    inicio = time.monotonic()
    ultimo_log_tempo = inicio
    ultimo_log_qtd = 0
    total = len(itens)

    for inicio_lote, lote in lotes(itens, tamanho_lote):
        fim_lote = inicio_lote + len(lote)
        for item in lote:
            preparar_item_produto_filho_para_insert(item, limites)

        try:
            cursor_web.execute("SAVEPOINT sp_produto_filho_lote")
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto'],
                [item['produto'] for item in lote]
            )
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto_filho'],
                [item['produto_filho'] for item in lote]
            )
            precos = []
            for item in lote:
                precos.extend(item['precos'])
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto_preco'],
                precos
            )
            inserir_registros_multi_sem_savepoint(
                cursor_web,
                tabelas_web['produto_estoque'],
                [item['estoque'] for item in lote]
            )
            cursor_web.execute("RELEASE SAVEPOINT sp_produto_filho_lote")

            inseridos += len(lote)
            sucessos.extend(lote)
            agora = time.monotonic()
            if (
                fim_lote == total
                or fim_lote - ultimo_log_qtd >= LOG_PRODUTO_INTERVALO_REGISTROS
                or agora - ultimo_log_tempo >= LOG_PRODUTO_INTERVALO_SEGUNDOS
            ):
                print(
                    f"  Lotes filho: {fim_lote}/{total} processados, "
                    f"{inseridos} inseridos em {int(agora - inicio)}s.",
                    flush=True
                )
                ultimo_log_tempo = agora
                ultimo_log_qtd = fim_lote
        except Exception as e:
            try:
                cursor_web.execute("ROLLBACK TO SAVEPOINT sp_produto_filho_lote")
                cursor_web.execute("RELEASE SAVEPOINT sp_produto_filho_lote")
            except Exception:
                pass

            global_, motivo = erro_e_global(e)
            if global_:
                raise ErroConfiguracaoConversao(
                    f"Lote filho {inicio_lote + 1}-{fim_lote} falhou por causa global ({motivo}). "
                    f"A etapa foi interrompida antes de processar os {total} produtos filhos. "
                    f"Causa raiz: {e}"
                )

            print(
                f"  [AVISO] Lote filho {inicio_lote + 1}-{fim_lote} falhou em lote; "
                f"tentando item a item... Causa: {e}",
                flush=True
            )
            for item in lote:
                erro_insert = inserir_produto_filho_transacional(
                    cursor_web,
                    tabelas_web,
                    item['produto'],
                    item['produto_filho'],
                    item['precos'],
                    item['estoque'],
                    limites
                )
                if erro_insert:
                    erros += 1
                    detalhe = f"cd_produto_giv={item['cd_produto_giv']}: {erro_insert}"
                    erros_detalhe.append(detalhe)
                    if erros <= 10:
                        print(f"  [PULO] produto filho {detalhe}")
                    elif erros == 11:
                        print("  ... suprimindo demais erros de insert de filho")
                    continue

                inseridos += 1
                sucessos.append(item)

            print(
                f"  Lote filho {fim_lote}/{total}: fallback concluido, "
                f"inseridos acumulados={inseridos}, erros_lote={erros}.",
                flush=True
            )
            ultimo_log_tempo = time.monotonic()
            ultimo_log_qtd = fim_lote

    return inseridos, erros, erros_detalhe, sucessos


class ErroConfiguracaoConversao(Exception):
    """Pre-requisito critico ausente no Web; a conversao deve parar antes de gravar."""


def _consultar_config_tabela_preco_empresa(cursor_web, tabela_config, tenant_id, cd_empresa):
    """Le sys_config_values('tabela_preco_padrao', escopo empresa). Mesma query do Web."""
    cursor_web.execute("SAVEPOINT sp_cfg_tabela_preco")
    try:
        cursor_web.execute(
            f"""
            SELECT {quote_identificador('value')}
              FROM {tabela_config}
             WHERE {quote_identificador('key')} = %s
               AND {quote_identificador('scope_type')} = 'empresa'
               AND {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('cd_empresa')} = %s
             ORDER BY {quote_identificador('value')}
             LIMIT 1
            """,
            (CONFIG_TABELA_PRECO_PADRAO, tenant_id, cd_empresa)
        )
        row = cursor_web.fetchone()
        cursor_web.execute("RELEASE SAVEPOINT sp_cfg_tabela_preco")
    except Exception as e:
        try:
            cursor_web.execute("ROLLBACK TO SAVEPOINT sp_cfg_tabela_preco")
            cursor_web.execute("RELEASE SAVEPOINT sp_cfg_tabela_preco")
        except Exception:
            pass
        print(f"[AVISO] Nao foi possivel ler {CONFIG_TABELA_PRECO_PADRAO} em sys_config_values: {e}")
        return None

    if not row:
        return None
    texto = str(row[0] or '').strip()
    if not texto.isdigit():
        return None
    codigo = int(texto)
    return codigo if codigo > 0 else None


def _tabela_preco_ativa_do_tenant(cursor_web, tabela_web_preco, tenant_id, cd_tabela_preco):
    """Confere o par (tenant_id, cd_tabela_preco) exigido pela FK, exigindo id_status='A'."""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador('cd_tabela_preco')}
          FROM {tabela_web_preco}
         WHERE {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_tabela_preco')} = %s
           AND COALESCE({quote_identificador('id_status')}, 'A') = 'A'
         LIMIT 1
        """,
        (tenant_id, cd_tabela_preco)
    )
    row = cursor_web.fetchone()
    return int(row[0]) if row else None


def _primeira_tabela_preco_ativa_do_tenant(cursor_web, tabela_web_preco, tenant_id):
    """Mesma regra do fallback da migration 20260715210000 (primeira tabela ativa do tenant)."""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador('cd_tabela_preco')},
               {quote_identificador('ds_tabela_preco')}
          FROM {tabela_web_preco}
         WHERE {quote_identificador('tenant_id')} = %s
           AND COALESCE({quote_identificador('id_status')}, 'A') = 'A'
         ORDER BY {quote_identificador('cd_tabela_preco')}
         LIMIT 1
        """,
        (tenant_id,)
    )
    row = cursor_web.fetchone()
    return (int(row[0]), row[1]) if row else (None, None)


def _gravar_config_tabela_preco_empresa(cursor_web, tabela_config, tenant_id, cd_empresa, cd_tabela_preco):
    """Grava sys_config_values no escopo 'empresa' respeitando a unique ux_config_scope."""
    cursor_web.execute(
        f"""
        UPDATE {tabela_config}
           SET {quote_identificador('value')} = %s
         WHERE {quote_identificador('key')} = %s
           AND {quote_identificador('scope_type')} = 'empresa'
           AND {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_empresa')} = %s
        """,
        (str(cd_tabela_preco), CONFIG_TABELA_PRECO_PADRAO, tenant_id, cd_empresa)
    )
    if cursor_web.rowcount:
        return

    cursor_web.execute(
        f"""
        INSERT INTO {tabela_config} (
            {quote_identificador('key')},
            {quote_identificador('scope_type')},
            {quote_identificador('tenant_id')},
            {quote_identificador('cd_empresa')},
            {quote_identificador('value')}
        ) VALUES (%s, 'empresa', %s, %s, %s)
        """,
        (CONFIG_TABELA_PRECO_PADRAO, tenant_id, cd_empresa, str(cd_tabela_preco))
    )


def _criar_tabela_preco_padrao(cursor_web, tabela_web_preco, tenant_id):
    """Cria a tabela de preco padrao do tenant com os mesmos campos usados no cadastro de empresa."""
    sequence_preco = buscar_sequence_coluna_web(cursor_web, tabela_web_preco, 'cd_tabela_preco')
    sincronizar_sequence_com_max(cursor_web, tabela_web_preco, 'cd_tabela_preco', sequence_preco)
    cd_tabela_preco = proximo_valor_sequence(cursor_web, sequence_preco)
    inserir_registro_web_sem_savepoint(cursor_web, tabela_web_preco, {
        'cd_tabela_preco': cd_tabela_preco,
        'ds_tabela_preco': DS_TABELA_PRECO_PADRAO_CONVERSAO,
        'id_status': 'A',
        'pr_margem_lucro': Decimal('0'),
        'tenant_id': tenant_id,
    })
    return cd_tabela_preco


def resolver_tabela_preco_padrao_web(
    cursor_web,
    tenant_id,
    cd_empresa,
    permitir_criar=True,
    simular=False
):
    """
    Resolve o cd_tabela_preco que sera gravado em produto_preco, seguindo a regra
    oficial do Web. Ordem:
      1. sys_config_values('tabela_preco_padrao', escopo empresa) validado em tabela_preco;
      2. primeira tabela ativa do tenant (mesmo fallback da migration oficial), gravando a config;
      3. criacao de "TABELA PADRAO" quando o tenant nao possui nenhuma tabela ativa.
    Levanta ErroConfiguracaoConversao quando nao for seguro seguir.
    Retorna (cd_tabela_preco, origem).
    """
    tabela_web_preco = resolver_tabela_web_opcional(cursor_web, "tabela_preco")
    if not tabela_web_preco:
        raise ErroConfiguracaoConversao(
            "Tabela \"tabela_preco\" nao existe no banco Web. "
            "Rode as migrations do projeto Web antes de converter produtos."
        )
    tabela_config = resolver_tabela_web_opcional(cursor_web, "sys_config_values")

    cd_config = (
        _consultar_config_tabela_preco_empresa(cursor_web, tabela_config, tenant_id, cd_empresa)
        if tabela_config else None
    )
    if cd_config:
        cd_valido = _tabela_preco_ativa_do_tenant(cursor_web, tabela_web_preco, tenant_id, cd_config)
        if cd_valido:
            print(
                f"[OK] Tabela de preco padrao da empresa: cd_tabela_preco={cd_valido} "
                f"(sys_config_values.{CONFIG_TABELA_PRECO_PADRAO}, tenant_id={tenant_id}, "
                f"cd_empresa={cd_empresa})."
            )
            return cd_valido, 'config_empresa'
        print(
            f"[AVISO] sys_config_values aponta cd_tabela_preco={cd_config}, mas ele nao existe "
            f"ativo no tenant_id={tenant_id}. A configuracao sera corrigida."
        )

    cd_primeira, ds_primeira = _primeira_tabela_preco_ativa_do_tenant(
        cursor_web, tabela_web_preco, tenant_id
    )
    if cd_primeira:
        if simular:
            print(
                f"[DRY-RUN] Usaria a primeira tabela ativa do tenant: cd_tabela_preco={cd_primeira} "
                f"({ds_primeira}); a config de empresa seria gravada."
            )
            return cd_primeira, 'primeira_ativa_simulada'
        if not tabela_config:
            raise ErroConfiguracaoConversao(
                "Tabela \"sys_config_values\" nao existe no banco Web; nao e possivel definir a "
                "tabela de preco padrao da empresa conforme a regra atual do sistema."
            )
        _gravar_config_tabela_preco_empresa(
            cursor_web, tabela_config, tenant_id, cd_empresa, cd_primeira
        )
        print(
            f"[OK] Tabela de preco padrao definida para a empresa: cd_tabela_preco={cd_primeira} "
            f"({ds_primeira}) - primeira tabela ativa do tenant, mesma regra da migration oficial."
        )
        return cd_primeira, 'primeira_ativa'

    if not permitir_criar:
        raise ErroConfiguracaoConversao(
            f"O tenant_id={tenant_id} nao possui nenhuma tabela de preco ativa e a criacao "
            "automatica esta desabilitada. Cadastre uma tabela de preco no Web e defina-a como "
            "padrao da empresa em Configuracoes antes de converter produtos."
        )

    if simular:
        print(
            f"[DRY-RUN] O tenant_id={tenant_id} nao possui tabela de preco ativa; seria criada "
            f"\"{DS_TABELA_PRECO_PADRAO_CONVERSAO}\" e vinculada a cd_empresa={cd_empresa}."
        )
        return None, 'criaria'

    if not tabela_config:
        raise ErroConfiguracaoConversao(
            "Tabela \"sys_config_values\" nao existe no banco Web; nao e possivel definir a "
            "tabela de preco padrao da empresa conforme a regra atual do sistema."
        )

    cd_nova = _criar_tabela_preco_padrao(cursor_web, tabela_web_preco, tenant_id)
    _gravar_config_tabela_preco_empresa(cursor_web, tabela_config, tenant_id, cd_empresa, cd_nova)
    print(
        f"[OK] Tabela de preco criada para o tenant_id={tenant_id}: cd_tabela_preco={cd_nova} "
        f"(\"{DS_TABELA_PRECO_PADRAO_CONVERSAO}\", id_status=A) e definida como padrao da "
        f"cd_empresa={cd_empresa}."
    )
    return cd_nova, 'criada'


def definir_tabela_preco_padrao_produto(
    cursor_web,
    tenant_id,
    cd_empresa,
    permitir_criar=True,
    simular=False
):
    """Resolve e publica o cd_tabela_preco usado por montar_precos_web()."""
    global CD_TABELA_PRECO_PRODUTO_PADRAO
    cd_tabela_preco, origem = resolver_tabela_preco_padrao_web(
        cursor_web, tenant_id, cd_empresa, permitir_criar=permitir_criar, simular=simular
    )
    CD_TABELA_PRECO_PRODUTO_PADRAO = cd_tabela_preco
    return cd_tabela_preco, origem


def montar_precos_web(precos_giv, cd_produto_web, tenant_id):
    """Monta produto_preco usando sempre a tabela de preco padrao resolvida para a empresa."""
    if not CD_TABELA_PRECO_PRODUTO_PADRAO:
        raise ErroConfiguracaoConversao(
            "Tabela de preco padrao nao resolvida. definir_tabela_preco_padrao_produto() precisa "
            "rodar antes de montar qualquer produto_preco."
        )
    if not precos_giv:
        precos_giv = [{}]
    return [
        converter_produto_preco(
            precos_giv[0],
            CD_TABELA_PRECO_PRODUTO_PADRAO,
            cd_produto_web,
            tenant_id
        )
    ]


def carregar_variacoes_por_pai_web(cursor_web, tabela_web_produto_filho, tenant_id):
    """
    Carrega as combinacoes (cd_produto_pai, cd_cor, cd_tamanho) que ja existem
    no Web para o tenant. A unique ux_produto_filho_familia_variacao impede que
    uma familia tenha duas variacoes com a mesma cor e tamanho, entao precisamos
    conhecer o que ja existe antes de montar os filhos.
    """
    variacoes = set()
    if not tabela_web_produto_filho:
        return variacoes
    cursor_web.execute(
        f"""
        SELECT {quote_identificador('cd_produto_pai')},
               {quote_identificador('cd_cor')},
               {quote_identificador('cd_tamanho')}
          FROM {tabela_web_produto_filho}
         WHERE {quote_identificador('tenant_id')} = %s
        """,
        (tenant_id,)
    )
    for row in cursor_web.fetchall():
        variacoes.add((row[0], row[1], row[2]))
    return variacoes


def carregar_mapa_produto_existente_tenant(cursor_web, tabelas_web, tenant_id, produtos_giv, barcodes_giv, mapas, classificacao):
    """Relaciona produtos GIV com produtos ja existentes no Web dentro do mesmo tenant."""
    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_produto')},
            {quote_identificador('ds_produto')},
            {quote_identificador('cd_produto_barra_ean')},
            {quote_identificador('cd_referencia')},
            {quote_identificador('cd_grade')}
          FROM {tabelas_web['produto_info']}
         WHERE {quote_identificador('tenant_id')} = %s
        """,
        (tenant_id,)
    )
    por_nome = {}
    por_barcode = {}
    por_referencia = {}
    por_referencia_nome = {}
    grade_por_produto = {}
    for cd_produto, ds_produto, barcode, cd_referencia, cd_grade in cursor_web.fetchall():
        chave = chave_texto(ds_produto)
        if chave:
            por_nome.setdefault(chave, set()).add(cd_produto)
        barcode = somente_digitos(barcode)
        if barcode:
            por_barcode.setdefault(barcode, set()).add(cd_produto)
        referencia = chave_texto(cd_referencia)
        if referencia:
            por_referencia.setdefault(referencia, set()).add(cd_produto)
            if chave:
                por_referencia_nome.setdefault((referencia, chave), set()).add(cd_produto)
        grade_por_produto[cd_produto] = cd_grade

    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_produto')},
            {quote_identificador('cd_produto_barra_ean')}
          FROM {tabelas_web['produto']}
         WHERE {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_produto_barra_ean')} IS NOT NULL
        """,
        (tenant_id,)
    )
    for cd_produto, barcode in cursor_web.fetchall():
        barcode = somente_digitos(barcode)
        if barcode:
            por_barcode.setdefault(barcode, set()).add(cd_produto)

    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_produto_filho')},
            {quote_identificador('cd_produto_pai')},
            {quote_identificador('cd_cor')},
            {quote_identificador('cd_tamanho')},
            {quote_identificador('cd_produto_barra_ean')}
          FROM {tabelas_web['produto_filho']}
         WHERE {quote_identificador('tenant_id')} = %s
        """,
        (tenant_id,)
    )
    filhos_por_barcode = {}
    filhos_por_pai_cor_tamanho = {}
    for cd_filho, cd_pai, cd_cor, cd_tamanho, barcode in cursor_web.fetchall():
        barcode = somente_digitos(barcode)
        if barcode:
            filhos_por_barcode.setdefault(barcode, set()).add((cd_filho, cd_pai, cd_cor, cd_tamanho))
        filhos_por_pai_cor_tamanho.setdefault((cd_pai, cd_cor, cd_tamanho), set()).add(cd_filho)

    mapa = {}
    grade_por_pai = {}
    por_barcode_ok = 0
    por_referencia_ok = 0
    por_nome_ok = 0
    filhos_barcode_ok = 0
    pais_por_filho_ok = 0
    filhos_grade_ok = 0
    ambiguos = 0

    raizes = [reg for reg in produtos_giv if classificacao[reg.get('cd_produto')]['eh_raiz']]
    filhos = [reg for reg in produtos_giv if classificacao[reg.get('cd_produto')]['eh_filho']]

    for produto in raizes:
        cd_origem = produto.get('cd_produto')
        barcode = somente_digitos(barcodes_giv.get(cd_origem))
        referencia = chave_texto(produto.get('cd_referencia'))
        nome = chave_texto(produto.get('ds_produto'))
        cd_web = None

        candidatos_barcode = por_barcode.get(barcode) if barcode else None
        if candidatos_barcode and len(candidatos_barcode) == 1:
            cd_web = next(iter(candidatos_barcode))
            por_barcode_ok += 1
        else:
            candidatos_referencia_nome = (
                por_referencia_nome.get((referencia, nome))
                if referencia and nome
                else None
            )
            candidatos_referencia = por_referencia.get(referencia) if referencia else None
            candidatos_nome = por_nome.get(nome) if nome else None
            if candidatos_referencia_nome and len(candidatos_referencia_nome) == 1:
                cd_web = next(iter(candidatos_referencia_nome))
                por_referencia_ok += 1
            elif candidatos_referencia and len(candidatos_referencia) == 1:
                cd_web = next(iter(candidatos_referencia))
                por_referencia_ok += 1
            elif candidatos_nome and len(candidatos_nome) == 1:
                cd_web = next(iter(candidatos_nome))
                por_nome_ok += 1
            elif candidatos_barcode or candidatos_referencia_nome or candidatos_referencia or candidatos_nome:
                ambiguos += 1

        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web
            grade_por_pai[cd_web] = grade_por_produto.get(cd_web)

    for produto in filhos:
        cd_origem = produto.get('cd_produto')
        barcode = somente_digitos(barcodes_giv.get(cd_origem))
        cd_web = None

        candidatos_barcode = filhos_por_barcode.get(barcode) if barcode else None
        if candidatos_barcode and len(candidatos_barcode) == 1:
            cd_web, cd_pai_web, _cd_cor_web, _cd_tamanho_web = next(iter(candidatos_barcode))
            filhos_barcode_ok += 1
            cd_pai_giv = produto.get('cd_produto_pai')
            if cd_pai_giv is not None and cd_pai_web is not None and cd_pai_giv not in mapa:
                mapa[cd_pai_giv] = cd_pai_web
                mapa[normalizar_codigo_cidade(cd_pai_giv)] = cd_pai_web
                grade_por_pai[cd_pai_web] = grade_por_produto.get(cd_pai_web)
                pais_por_filho_ok += 1
        else:
            cd_pai_web = mapa.get(produto.get('cd_produto_pai'))
            cd_cor_web = valor_mapa_ou_padrao(mapas.get('cor', {}), produto.get('cd_cor'))
            cd_tamanho_web = valor_mapa_ou_padrao(mapas.get('tamanho', {}), produto.get('cd_tamanho'))
            candidatos_grade = (
                filhos_por_pai_cor_tamanho.get((cd_pai_web, cd_cor_web, cd_tamanho_web))
                if cd_pai_web is not None and cd_cor_web is not None and cd_tamanho_web is not None
                else None
            )
            if candidatos_grade and len(candidatos_grade) == 1:
                cd_web = next(iter(candidatos_grade))
                filhos_grade_ok += 1
            elif candidatos_barcode or candidatos_grade:
                ambiguos += 1

        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web

    chaves_giv = {chave for chave in mapa if isinstance(chave, int)}
    print(
        f"[OK] Produtos ja existentes no tenant: {len(set(mapa.values()))} Web / "
        f"{len(chaves_giv)} GIV mapeados "
        f"(raiz: {por_barcode_ok} barcode, {por_referencia_ok} referencia, {por_nome_ok} nome; "
        f"filhos: {filhos_barcode_ok} barcode, {filhos_grade_ok} pai+cor+tamanho; "
        f"pais inferidos por filho={pais_por_filho_ok}; "
        f"{ambiguos} ambiguos)."
    )
    return mapa, grade_por_pai


def carregar_produtos_com_estoque_web(cursor_web, tabela_web_produto_estoque, tenant_id, cd_empresa):
    cursor_web.execute(
        f"""
        SELECT {quote_identificador('cd_produto')}
          FROM {tabela_web_produto_estoque}
         WHERE {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_empresa')} = %s
        """,
        (tenant_id, cd_empresa)
    )
    return {row[0] for row in cursor_web.fetchall()}


def inserir_estoque_produtos_reaproveitados(
    cursor_web,
    tabelas_web,
    produtos_giv,
    mapa_produtos,
    estoques_por_produto,
    tenant_id,
    cd_empresa,
    limites
):
    existentes_estoque = carregar_produtos_com_estoque_web(
        cursor_web,
        tabelas_web['produto_estoque'],
        tenant_id,
        cd_empresa
    )
    registros = []
    produtos_web_processados = set()
    for produto in produtos_giv:
        cd_giv = produto.get('cd_produto')
        cd_web = mapa_produtos.get(cd_giv)
        if cd_web is None or cd_web in produtos_web_processados:
            continue
        produtos_web_processados.add(cd_web)
        if cd_web in existentes_estoque:
            continue
        estoque = converter_produto_estoque(
            estoques_por_produto.get(cd_giv),
            cd_web,
            tenant_id,
            cd_empresa
        )
        aplicar_limites_texto_registro(estoque, limites.get('produto_estoque'))
        registros.append(estoque)

    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['produto_estoque'],
        registros,
        'cd_produto',
        'sp_produto_estoque_reaproveitado'
    )
    existentes = len(produtos_web_processados) - len(registros)
    if produtos_web_processados:
        print(
            f"[OK] Produto_estoque para produtos reaproveitados: "
            f"{inseridos} inseridos para empresa {cd_empresa}, "
            f"{existentes} ja existiam, {erros} erros."
        )
    return {
        'tabela': 'produto_estoque',
        'lidos': len(produtos_web_processados),
        'inseridos': inseridos,
        'existentes': existentes,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
        'motivo_existentes': 'estoque do produto ja existia para a empresa Web',
    }


def validar_tenant_empresa_web(cursor_web, tenant_id, cd_empresa):
    """Confere que o tenant existe e que a empresa pertence a esse tenant."""
    tabela_tenant = resolver_tabela_web_opcional(cursor_web, "tenant")
    tabela_empresa = resolver_tabela_web_opcional(cursor_web, "empresa")
    if not tabela_tenant or not tabela_empresa:
        raise ErroConfiguracaoConversao(
            "Tabelas \"tenant\"/\"empresa\" nao encontradas no banco Web."
        )

    cursor_web.execute(
        f"SELECT 1 FROM {tabela_tenant} WHERE {quote_identificador('id')} = %s",
        (tenant_id,)
    )
    if not cursor_web.fetchone():
        raise ErroConfiguracaoConversao(
            f"tenant_id={tenant_id} nao existe no banco Web."
        )

    cursor_web.execute(
        f"""
        SELECT {quote_identificador('tenant_id')}
          FROM {tabela_empresa}
         WHERE {quote_identificador('cd_empresa')} = %s
        """,
        (cd_empresa,)
    )
    row = cursor_web.fetchone()
    if not row:
        raise ErroConfiguracaoConversao(
            f"cd_empresa={cd_empresa} nao existe no banco Web."
        )
    if int(row[0]) != int(tenant_id):
        raise ErroConfiguracaoConversao(
            f"cd_empresa={cd_empresa} pertence ao tenant_id={row[0]}, e nao ao "
            f"tenant_id={tenant_id} informado. Corrija os parametros da conversao."
        )
    print(f"[OK] Preflight: tenant_id={tenant_id} existe e cd_empresa={cd_empresa} pertence a ele.")


def preflight_produto(cursor_web, tabelas_web, tenant_id, cd_empresa, simular=False):
    """
    Valida os pre-requisitos criticos ANTES de reservar codigos e montar produtos.
    Levanta ErroConfiguracaoConversao para interromper a etapa sem gravar nada.
    """
    print()
    print("[...] Preflight de produto: validando pre-requisitos no Web...")

    validar_tenant_empresa_web(cursor_web, tenant_id, cd_empresa)

    obrigatorias = (
        'produto', 'produto_info', 'produto_filho', 'produto_preco',
        'produto_estoque', 'produto_colecao',
    )
    faltando = [
        nome for nome in obrigatorias
        if not tabelas_web.get(nome)
        or not resolver_tabela_web_opcional(cursor_web, nome)
    ]
    if faltando:
        raise ErroConfiguracaoConversao(
            f"Tabelas ausentes no banco Web: {', '.join(faltando)}. "
            "Rode as migrations do projeto Web."
        )

    cd_tabela_preco, origem = definir_tabela_preco_padrao_produto(
        cursor_web, tenant_id, cd_empresa, simular=simular
    )
    if not simular:
        if not cd_tabela_preco:
            raise ErroConfiguracaoConversao(
                "Nao foi possivel resolver a tabela de preco padrao da empresa."
            )
        # Revalida o par exigido pela FK fk_produto_preco_tabela_tenant.
        tabela_web_preco = resolver_tabela_web_opcional(cursor_web, "tabela_preco")
        if not _tabela_preco_ativa_do_tenant(cursor_web, tabela_web_preco, tenant_id, cd_tabela_preco):
            raise ErroConfiguracaoConversao(
                f"O par (tenant_id={tenant_id}, cd_tabela_preco={cd_tabela_preco}) nao esta "
                "presente e ativo em tabela_preco. produto_preco violaria "
                "fk_produto_preco_tabela_tenant."
            )
        print(
            f"[OK] Preflight: produto_preco usara cd_tabela_preco={cd_tabela_preco} "
            f"(origem: {origem}); par (tenant_id, cd_tabela_preco) confirmado em tabela_preco."
        )

    if not buscar_sequence_coluna_web(cursor_web, tabelas_web['produto'], 'cd_produto'):
        raise ErroConfiguracaoConversao(
            "Sequence de produto.cd_produto nao encontrada no banco Web."
        )
    print("[OK] Preflight: tabelas e sequences de produto confirmadas.")
    return cd_tabela_preco


def validar_padroes_produto_preflight(padroes_produto):
    """Confere que os padroes obrigatorios de produto foram resolvidos no Web."""
    obrigatorios = {
        'cd_marca': 'marca',
        'cd_grupo': 'grupo',
        'cd_fornecedor': 'fornecedor',
    }
    ausentes = [
        rotulo for chave, rotulo in obrigatorios.items()
        if not padroes_produto.get(chave)
    ]
    if ausentes:
        raise ErroConfiguracaoConversao(
            "Cadastros padrao obrigatorios ausentes no Web para este tenant/empresa: "
            f"{', '.join(ausentes)}. Converta esses cadastros antes de produto."
        )


def processar_produtos(
    cursor_giv,
    cursor_web,
    tabelas_web,
    tenant_id,
    cd_empresa,
    mapa_status_produto,
    mapa_grupos_convertidos=None,
    mapas_auxiliares_convertidos=None,
    tamanhos_por_grade=None,
    cd_empresa_giv=None
):
    print()
    resumos = []

    preflight_produto(cursor_web, tabelas_web, tenant_id, cd_empresa)

    print("[...] Carregando mapas de cadastros auxiliares para produto...")
    mapas = carregar_mapas_cadastros_produto(
        cursor_giv,
        cursor_web,
        tabelas_web,
        tenant_id,
        cd_empresa,
        mapa_grupos_convertidos,
        cd_empresa_giv
    )
    mapas_auxiliares = carregar_mapas_auxiliares_produto(
        cursor_giv,
        cursor_web,
        tabelas_web,
        tenant_id,
        cd_empresa,
        cd_empresa_giv
    )
    mapas_auxiliares_convertidos = mapas_auxiliares_convertidos or {}
    for chave, mapa_convertido in mapas_auxiliares_convertidos.items():
        mapas_auxiliares.setdefault(chave, {}).update(mapa_convertido or {})
    mapas.update(mapas_auxiliares)
    padroes_produto = atualizar_padroes_produto(mapas)
    preencher_padroes_produto_web(cursor_web, tabelas_web, padroes_produto, tenant_id, cd_empresa)

    if tamanhos_por_grade is None:
        tamanhos_por_grade = buscar_tamanhos_por_grade_web(cursor_web, tabelas_web.get('grade_tamanho'))

    print(
        "[OK] Mapas auxiliares produto: "
        f"tamanho={len(mapas.get('tamanho', {}))}, "
        f"unidade={len(mapas.get('unidade', {}))}, "
        f"colecao={len(mapas.get('colecao', {}))}, "
        f"grade={len(mapas.get('grade', {}))}."
    )
    print(
        "[INFO] Produto: padrao Web sera usado para marca/grupo vazios e fornecedor obrigatorio sem de/para: "
        f"marca={padroes_produto.get('cd_marca')}, grupo={padroes_produto.get('cd_grupo')}, "
        f"fornecedor={padroes_produto.get('cd_fornecedor')}."
    )
    validar_padroes_produto_preflight(padroes_produto)
    print(
        f"[INFO] Produto: produto_preco gravara cd_tabela_preco={CD_TABELA_PRECO_PRODUTO_PADRAO} "
        f"(tabela de preco padrao da cd_empresa={cd_empresa} no tenant_id={tenant_id})."
    )

    sequence_produto = buscar_sequence_coluna_web(
        cursor_web,
        tabelas_web['produto'],
        'cd_produto'
    )
    max_produto = sincronizar_sequence_produto_global(
        cursor_web,
        tabelas_web['produto'],
        tabelas_web['produto_filho'],
        sequence_produto
    )
    print(f"[OK] Sequence global de produto: {sequence_produto} (sincronizada com max={max_produto}).")

    print()
    print("[...] Buscando produtos, precos, estoque e barcodes do GIV...")
    produtos_giv = buscar_produtos_giv(cursor_giv)
    precos_por_produto = buscar_precos_produto_giv(cursor_giv)
    cd_empresa_giv = cd_empresa_giv if cd_empresa_giv is not None else cd_empresa
    print(f"[INFO] Produto: saldo sera buscado no GIV com cd_empresa={cd_empresa_giv}.")
    estoques_por_produto = buscar_estoques_produto_giv(cursor_giv, cd_empresa_giv)
    barcodes_originais_por_produto = buscar_barcodes_produto_giv(cursor_giv)
    classificacao = classificar_produtos_giv(produtos_giv)
    mapa_produtos, grade_por_pai_web = carregar_mapa_produto_existente_tenant(
        cursor_web,
        tabelas_web,
        tenant_id,
        produtos_giv,
        barcodes_originais_por_produto,
        mapas,
        classificacao
    )
    produtos_reaproveitados_tenant = len({
        cd_web
        for cd_giv, cd_web in mapa_produtos.items()
        if isinstance(cd_giv, int)
    })
    barcodes_por_produto = filtrar_barcodes_existentes_web(
        cursor_web,
        tabelas_web,
        tenant_id,
        barcodes_originais_por_produto
    )
    fornecedores_produtos = {
        reg.get('cd_fornecedor')
        for reg in produtos_giv
        if reg.get('cd_fornecedor') is not None
    }
    fornecedores_mapeados = {
        cd_fornecedor
        for cd_fornecedor in fornecedores_produtos
        if cd_fornecedor in mapas.get('fornecedor', {})
    }
    print(
        f"[OK] {len(produtos_giv)} produtos, {len(precos_por_produto)} precos, "
        f"{len(estoques_por_produto)} saldos e {len(barcodes_por_produto)} barcodes livres carregados."
    )
    imprimir_diagnostico_de_para_produto('marca', 'cd_marca', mapas.get('marca'), produtos_giv, obrigatorio=True)
    imprimir_diagnostico_de_para_produto('fornecedor', 'cd_fornecedor', mapas.get('fornecedor'), produtos_giv)
    imprimir_diagnostico_de_para_produto('cor', 'cd_cor', mapas.get('cor'), produtos_giv)
    imprimir_diagnostico_de_para_produto('tamanho', 'cd_tamanho', mapas.get('tamanho'), produtos_giv)
    imprimir_diagnostico_de_para_produto('unidade', 'cd_unidade', mapas.get('unidade'), produtos_giv)
    imprimir_diagnostico_de_para_produto('colecao', 'cd_colecao', mapas.get('colecao'), produtos_giv)
    imprimir_diagnostico_de_para_produto('grupo_moda', 'cd_grupo_moda', mapas.get('grupo'), produtos_giv, obrigatorio=True)
    imprimir_diagnostico_de_para_produto('grupo', 'cd_grupo', mapas.get('grupo'), produtos_giv, obrigatorio=True)
    imprimir_diagnostico_de_para_produto('departamento', 'cd_genero_moda', mapas.get('departamento'), produtos_giv)
    imprimir_diagnostico_de_para_produto('sub_grupo', 'cd_subgrupo_moda', mapas.get('sub_grupo'), produtos_giv)
    print(
        f"[OK] Fornecedores nos produtos: {len(fornecedores_mapeados)}/"
        f"{len(fornecedores_produtos)} codigos GIV mapeados para fornecedor Web."
    )
    if len(fornecedores_mapeados) < len(fornecedores_produtos):
        sem_de_para = sorted(fornecedores_produtos - fornecedores_mapeados)
        produtos_com_padrao = sum(
            1 for reg in produtos_giv
            if reg.get('cd_fornecedor') is None
            or reg.get('cd_fornecedor') not in mapas.get('fornecedor', {})
        )
        # produto_info.cd_fornecedor e NOT NULL no Web (schema.prisma), entao o
        # produto NUNCA fica sem fornecedor: usa o fornecedor padrao do tenant.
        print(
            f"[INFO] Produtos sem de/para de fornecedor usarao o fornecedor padrao do Web "
            f"(cd_fornecedor={padroes_produto.get('cd_fornecedor')}): {produtos_com_padrao} produtos."
        )
        print(
            f"[INFO] Fornecedores GIV sem correspondencia no Web ({len(sem_de_para)}): "
            f"{', '.join(str(c) for c in sem_de_para[:20])}"
            + (" ..." if len(sem_de_para) > 20 else "")
        )

    limites = {
        'produto': buscar_limites_texto_web(cursor_web, tabelas_web['produto']),
        'produto_info': buscar_limites_texto_web(cursor_web, tabelas_web['produto_info']),
        'produto_preco': buscar_limites_texto_web(cursor_web, tabelas_web['produto_preco']),
        'produto_estoque': buscar_limites_texto_web(cursor_web, tabelas_web['produto_estoque']),
        'produto_filho': buscar_limites_texto_web(cursor_web, tabelas_web['produto_filho']),
    }

    erros_detalhe = []
    erros = 0

    resumo_estoque_reaproveitado = inserir_estoque_produtos_reaproveitados(
        cursor_web,
        tabelas_web,
        produtos_giv,
        mapa_produtos,
        estoques_por_produto,
        tenant_id,
        cd_empresa,
        limites
    )

    raizes = [
        reg
        for reg in produtos_giv
        if classificacao[reg.get('cd_produto')]['eh_raiz']
        and reg.get('cd_produto') not in mapa_produtos
    ]
    filhos = [
        reg
        for reg in produtos_giv
        if classificacao[reg.get('cd_produto')]['eh_filho']
        and reg.get('cd_produto') not in mapa_produtos
    ]

    print()
    total_codigos = len(raizes) + len(filhos)
    print(
        f"[INFO] Produtos no tenant: {produtos_reaproveitados_tenant} reaproveitados, "
        f"{total_codigos} novos para cadastrar."
    )
    print(
        f"[...] Reservando codigos da sequence de produto sob demanda, em blocos de "
        f"{TAMANHO_BLOCO_SEQUENCE_PRODUTO} (ate {total_codigos})..."
    )
    codigos_produto = gerador_codigos_sequence(
        cursor_web, sequence_produto, total_codigos, TAMANHO_BLOCO_SEQUENCE_PRODUTO
    )

    print()
    print(f"[...] Montando {len(raizes)} produtos raiz/simples para insert em lote...")
    itens_raiz = []
    inicio_raizes = time.monotonic()
    ultimo_log_raizes_tempo = inicio_raizes
    ultimo_log_raizes_qtd = 0
    for i, reg in enumerate(raizes, start=1):
        cd_produto_giv = reg.get('cd_produto')
        cd_produto_web = next(codigos_produto)
        barcode = barcodes_por_produto.get(cd_produto_giv)

        produto, produto_info, erros_validacao = montar_produto_raiz_web(
            reg,
            cd_produto_web,
            tenant_id,
            mapas,
            mapa_status_produto,
            classificacao,
            barcode
        )
        precos = montar_precos_web(
            precos_por_produto.get(cd_produto_giv, []),
            cd_produto_web,
            tenant_id
        )

        estoque = converter_produto_estoque(
            estoques_por_produto.get(cd_produto_giv),
            cd_produto_web,
            tenant_id,
            cd_empresa
        )
        produto_colecao = None
        if produto_info.get('cd_colecao'):
            produto_colecao = {
                'cd_produto': cd_produto_web,
                'cd_colecao': produto_info.get('cd_colecao'),
                'tenant_id': tenant_id,
            }

        if erros_validacao:
            erros += 1
            detalhe = f"cd_produto_giv={cd_produto_giv}: {'; '.join(erros_validacao)}"
            erros_detalhe.append(detalhe)
            if erros <= 10:
                print(f"  [PULO] produto raiz {detalhe}")
            elif erros == 11:
                print("  ... suprimindo demais erros de validacao de produto")
            continue

        itens_raiz.append({
            'cd_produto_giv': cd_produto_giv,
            'cd_produto_web': cd_produto_web,
            'cd_grade': produto_info.get('cd_grade'),
            'produto': produto,
            'produto_info': produto_info,
            'precos': precos,
            'estoque': estoque,
            'produto_colecao': produto_colecao,
        })
        agora = time.monotonic()
        if (
            i == len(raizes)
            or i - ultimo_log_raizes_qtd >= LOG_PRODUTO_INTERVALO_REGISTROS
            or agora - ultimo_log_raizes_tempo >= LOG_PRODUTO_INTERVALO_SEGUNDOS
        ):
            print(
                f"  Montagem raiz: {i}/{len(raizes)} processados, "
                f"{len(itens_raiz)} validos, {erros} erros.",
                flush=True
            )
            ultimo_log_raizes_tempo = agora
            ultimo_log_raizes_qtd = i

    print(
        f"[OK] Montagem raiz concluida: {len(itens_raiz)} validos, "
        f"{erros} erros em {int(time.monotonic() - inicio_raizes)}s."
    )

    print()
    print(
        f"[...] Inserindo {len(itens_raiz)} produtos raiz/simples "
        f"em lotes de {TAMANHO_LOTE_PRODUTO}..."
    )
    inseridos_raiz, erros_insert_raiz, erros_insert_raiz_detalhe, itens_raiz_sucesso = (
        inserir_produtos_raiz_lote_transacional(
            cursor_web,
            tabelas_web,
            itens_raiz,
            limites,
            TAMANHO_LOTE_PRODUTO
        )
    )
    erros += erros_insert_raiz
    erros_detalhe.extend(erros_insert_raiz_detalhe)
    for item in itens_raiz_sucesso:
        mapa_produtos[item['cd_produto_giv']] = item['cd_produto_web']
        grade_por_pai_web[item['cd_produto_web']] = item.get('cd_grade')
    print(
        f"[OK] Produtos raiz/simples: {inseridos_raiz} inseridos, "
        f"{erros_insert_raiz} erros de insert.",
        flush=True
    )

    print()
    print(f"[...] Montando/inserindo {len(filhos)} produtos filhos em rodadas...")
    variacoes_por_pai_web = carregar_variacoes_por_pai_web(
        cursor_web, tabelas_web.get('produto_filho'), tenant_id
    )
    if variacoes_por_pai_web:
        print(
            f"[OK] {len(variacoes_por_pai_web)} variacoes (pai+cor+tamanho) ja existentes "
            "no Web carregadas para nao colidir com a unique da familia."
        )
    variacoes_duplicadas = 0
    pendentes_filhos = list(filhos)
    inseridos_filhos = 0
    rodada_filhos = 1
    inicio_filhos = time.monotonic()

    while pendentes_filhos:
        itens_filhos = []
        proximos_pendentes = []
        ultimo_log_filhos_tempo = time.monotonic()
        ultimo_log_filhos_qtd = 0

        for i, reg in enumerate(pendentes_filhos, start=1):
            cd_produto_giv = reg.get('cd_produto')
            cd_pai_giv = reg.get('cd_produto_pai')
            if cd_pai_giv not in mapa_produtos:
                proximos_pendentes.append(reg)
                continue

            cd_produto_web = next(codigos_produto)
            barcode = barcodes_por_produto.get(cd_produto_giv)
            produto, produto_filho, erros_validacao = montar_produto_filho_web(
                reg,
                cd_produto_web,
                tenant_id,
                mapas,
                mapa_produtos,
                barcode
            )

            estoque = converter_produto_estoque(
                estoques_por_produto.get(cd_produto_giv),
                cd_produto_web,
                tenant_id,
                cd_empresa
            )
            precos = montar_precos_web(
                precos_por_produto.get(cd_produto_giv, []),
                cd_produto_web,
                tenant_id
            )

            if erros_validacao:
                erros += 1
                detalhe = f"cd_produto_giv={cd_produto_giv}: {'; '.join(erros_validacao)}"
                erros_detalhe.append(detalhe)
                if erros <= 10:
                    print(f"  [PULO] produto filho {detalhe}")
                elif erros == 11:
                    print("  ... suprimindo demais erros de validacao de filho")
                continue

            # ux_produto_filho_familia_variacao: (tenant_id, cd_produto_pai,
            # cd_cor, cd_tamanho) e unico. O GIV repete variacoes dentro da mesma
            # familia; isso e duplicidade da origem, nao falha de conversao.
            variacao = (
                produto_filho.get('cd_produto_pai'),
                produto_filho.get('cd_cor'),
                produto_filho.get('cd_tamanho'),
            )
            if variacao in variacoes_por_pai_web:
                variacoes_duplicadas += 1
                if variacoes_duplicadas <= 5:
                    print(
                        f"  [PULO] produto filho cd_produto_giv={cd_produto_giv}: variacao "
                        f"pai={variacao[0]} cor={variacao[1]} tamanho={variacao[2]} ja existe "
                        "nessa familia."
                    )
                elif variacoes_duplicadas == 6:
                    print("  ... suprimindo demais avisos de variacao duplicada")
                continue
            variacoes_por_pai_web.add(variacao)

            itens_filhos.append({
                'cd_produto_giv': cd_produto_giv,
                'cd_produto_web': cd_produto_web,
                'produto': produto,
                'produto_filho': produto_filho,
                'precos': precos,
                'estoque': estoque,
            })
            agora = time.monotonic()
            if (
                i == len(pendentes_filhos)
                or i - ultimo_log_filhos_qtd >= LOG_PRODUTO_INTERVALO_REGISTROS
                or agora - ultimo_log_filhos_tempo >= LOG_PRODUTO_INTERVALO_SEGUNDOS
            ):
                print(
                    f"  Rodada filho {rodada_filhos}: {i}/{len(pendentes_filhos)} analisados, "
                    f"{len(itens_filhos)} prontos, {len(proximos_pendentes)} aguardando pai.",
                    flush=True
                )
                ultimo_log_filhos_tempo = agora
                ultimo_log_filhos_qtd = i

        if not itens_filhos:
            print(
                f"[AVISO] {len(proximos_pendentes)} filhos continuaram sem pai Web; "
                "serao cadastrados como produtos independentes para nao perder cadastro."
            )
            itens_orfaos = []
            for reg in proximos_pendentes:
                cd_produto_giv = reg.get('cd_produto')
                cd_produto_web = next(codigos_produto)
                barcode = barcodes_por_produto.get(cd_produto_giv)
                produto, produto_info, erros_validacao = montar_produto_raiz_web(
                    reg,
                    cd_produto_web,
                    tenant_id,
                    mapas,
                    mapa_status_produto,
                    classificacao,
                    barcode
                )
                precos = montar_precos_web(
                    precos_por_produto.get(cd_produto_giv, []),
                    cd_produto_web,
                    tenant_id
                )
                estoque = converter_produto_estoque(
                    estoques_por_produto.get(cd_produto_giv),
                    cd_produto_web,
                    tenant_id,
                    cd_empresa
                )
                produto_colecao = None
                if produto_info.get('cd_colecao'):
                    produto_colecao = {
                        'cd_produto': cd_produto_web,
                        'cd_colecao': produto_info.get('cd_colecao'),
                        'tenant_id': tenant_id,
                    }
                if erros_validacao:
                    erros += 1
                    detalhe = f"cd_produto_giv={cd_produto_giv}: {'; '.join(erros_validacao)}"
                    erros_detalhe.append(detalhe)
                    if erros <= 10:
                        print(f"  [PULO] produto independente {detalhe}")
                    elif erros == 11:
                        print("  ... suprimindo demais erros de validacao de produto independente")
                    continue
                itens_orfaos.append({
                    'cd_produto_giv': cd_produto_giv,
                    'cd_produto_web': cd_produto_web,
                    'cd_grade': produto_info.get('cd_grade'),
                    'produto': produto,
                    'produto_info': produto_info,
                    'precos': precos,
                    'estoque': estoque,
                    'produto_colecao': produto_colecao,
                })

            inseridos_orfaos, erros_orfaos, erros_orfaos_detalhe, itens_orfaos_sucesso = (
                inserir_produtos_raiz_lote_transacional(
                    cursor_web,
                    tabelas_web,
                    itens_orfaos,
                    limites,
                    TAMANHO_LOTE_PRODUTO
                )
            )
            inseridos_filhos += inseridos_orfaos
            erros += erros_orfaos
            erros_detalhe.extend(erros_orfaos_detalhe)
            for item in itens_orfaos_sucesso:
                mapa_produtos[item['cd_produto_giv']] = item['cd_produto_web']
                grade_por_pai_web[item['cd_produto_web']] = item.get('cd_grade')
            break

        print(
            f"[...] Inserindo rodada {rodada_filhos}: {len(itens_filhos)} produtos filhos "
            f"em lotes de {TAMANHO_LOTE_PRODUTO}..."
        )
        inseridos_rodada, erros_insert_filhos, erros_insert_filhos_detalhe, itens_filhos_sucesso = (
            inserir_produtos_filhos_lote_transacional(
                cursor_web,
                tabelas_web,
                itens_filhos,
                limites,
                TAMANHO_LOTE_PRODUTO
            )
        )
        inseridos_filhos += inseridos_rodada
        erros += erros_insert_filhos
        erros_detalhe.extend(erros_insert_filhos_detalhe)
        for item in itens_filhos_sucesso:
            mapa_produtos[item['cd_produto_giv']] = item['cd_produto_web']

        print(
            f"[OK] Rodada filho {rodada_filhos}: {inseridos_rodada} inseridos, "
            f"{erros_insert_filhos} erros, {len(proximos_pendentes)} aguardando proxima rodada.",
            flush=True
        )
        pendentes_filhos = proximos_pendentes
        rodada_filhos += 1

    if variacoes_duplicadas:
        print(
            f"[OK] Produto filho: {variacoes_duplicadas} variacoes duplicadas no GIV "
            "(mesmo pai + cor + tamanho) foram ignoradas; nao sao erro de conversao."
        )
    print(
        f"[OK] Produtos filhos/pendentes: {inseridos_filhos} inseridos "
        f"em {int(time.monotonic() - inicio_filhos)}s.",
        flush=True
    )

    resumos.append({
        'tabela': 'produto',
        'lidos': len(produtos_giv),
        'inseridos': inseridos_raiz + inseridos_filhos,
        'existentes': produtos_reaproveitados_tenant,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
        'motivo_existentes': 'produto ja existia no mesmo tenant e foi reaproveitado',
    })
    if resumo_estoque_reaproveitado.get('lidos') or resumo_estoque_reaproveitado.get('inseridos'):
        resumos.append(resumo_estoque_reaproveitado)
    return resumos


# ============================================================
# ROTINAS FINANCEIRAS/DOCUMENTOS - GIV -> WEB
# ============================================================

def chave_data(valor):
    """Normaliza datas para chaves de de/para."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        return valor.date().isoformat()
    if isinstance(valor, datetime.date):
        return valor.isoformat()
    texto = limpar_valor(valor)
    if not texto:
        return None
    return str(texto)[:10]


def chave_composta(*partes):
    return tuple('' if parte is None else str(parte).strip() for parte in partes)


def serie_doc(valor):
    return limpar_valor(valor) or ''


def valor_data_ou_agora(valor):
    return valor or datetime.datetime.now()


def buscar_registros_giv_tabela(cursor_giv, tabela, order_by=None, cd_empresa_giv=None):
    sql = f"SELECT * FROM {tabela}"
    params = []
    if cd_empresa_giv is not None and tabela_giv_tem_coluna(cursor_giv, tabela, 'cd_empresa'):
        sql += " WHERE cd_empresa = ?"
        params.append(cd_empresa_giv)
    if order_by:
        sql += f" ORDER BY {order_by}"
    return buscar_registros_giv(cursor_giv, sql, params if params else None)


def buscar_set_coluna_web(cursor_web, tabela_web, coluna, tenant_id=None, cd_empresa=None):
    filtros = []
    params = []
    if tenant_id is not None:
        filtros.append(f"{quote_identificador('tenant_id')} = %s")
        params.append(tenant_id)
    if cd_empresa is not None:
        filtros.append(f"{quote_identificador('cd_empresa')} = %s")
        params.append(cd_empresa)
    where_sql = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    cursor_web.execute(
        f"SELECT {quote_identificador(coluna)} FROM {tabela_web}{where_sql}",
        params
    )
    return {row[0] for row in cursor_web.fetchall()}


def proximo_numero_disponivel(usados, inicio):
    numero = int(inicio or 0) + 1
    while numero in usados:
        numero += 1
    usados.add(numero)
    return numero


def remover_mapa_por_codigos_erro(mapa, erros_detalhe, campo_web):
    codigos = codigos_insert_com_erro(erros_detalhe, campo_web)
    if not codigos:
        return mapa
    return {
        chave: valor
        for chave, valor in mapa.items()
        if valor not in codigos
    }


def registrar_erro_validacao(erros_detalhe, contexto, chave, motivo, limite_log=10):
    detalhe = f"{chave}: {motivo}"
    erros_detalhe.append(detalhe)
    qtd = len(erros_detalhe)
    if qtd <= limite_log:
        print(f"  [PULO] {contexto} {detalhe}")
    elif qtd == limite_log + 1:
        print(f"  ... suprimindo demais erros de validacao de {contexto}")


def map_status_pedido_compra(status):
    status = (limpar_valor(status) or '').upper()
    return {'A': 'A', 'T': 'F', 'C': 'C', 'P': 'F'}.get(status, 'F')


def map_status_prevenda(status):
    status = (limpar_valor(status) or '').upper()
    if status == 'CX':
        return 'F'
    if status == 'CA':
        return 'C'
    return 'P'


def map_status_condicional(status):
    status = (limpar_valor(status) or '').upper()
    if status in ('CA', 'C', 'CN'):
        return 'C'
    if status in ('EN', 'EC', 'FE', 'F', 'BA'):
        return 'E'
    if status in ('ME', 'MS'):
        return 'M'
    return 'A'


def map_status_nf_saida(status):
    status = (limpar_valor(status) or '').upper()
    if status == 'CA':
        return 'C'
    return 'A'


def map_status_titulo_receber(status, vl_titulo, vl_pago):
    status = (limpar_valor(status) or '').upper()
    if status == 'AB':
        return 'A'
    if status == 'BA':
        return 'L'
    if status == 'BP':
        return 'P'
    if status == 'CA':
        return 'C'
    if status == 'DV':
        return 'D'
    if status == 'NG':
        return 'C'
    return 'A'


def calcular_valores_titulo_receber(titulo):
    """
    Monta os valores do contas a receber com a MESMA composicao usada pelos
    relatorios do proprio GIV:

        saldo = vl_titulo - vl_desconto + vl_acrescimo - vl_pago

    (procedures pr_rel_balanco_patrimonial e pr_rel_fluxo_caixa_novo).

    Ponto critico: no GIV, "vl_original" e o valor TOTAL da venda, repetido
    identico em todas as parcelas do mesmo documento, enquanto "vl_titulo" e o
    valor DESTA parcela. Exemplo real (titulo 2969/1): vl_original=9428,00 e
    vl_titulo=530,00. Como o Web tem uma linha por parcela, gravar vl_original
    multiplicava o contas a receber pelo numero de parcelas.

    "vl_juros_venda" nao entra na conta: nenhuma procedure do GIV o soma ao
    valor do titulo (e ele e zero em toda a base).
    """
    vl_titulo = valor_decimal_ou_zero(titulo.get('vl_titulo'))
    vl_desconto = valor_decimal_ou_zero(titulo.get('vl_desconto'))
    vl_acrescimo = valor_decimal_ou_zero(titulo.get('vl_acrescimo'))
    vl_liquido = vl_titulo - vl_desconto + vl_acrescimo
    # vl_pago vai como esta no GIV; os relatorios de la nao presumem baixa.
    vl_pago = valor_decimal_ou_zero(titulo.get('vl_pago'))

    return {
        'vl_titulo': vl_titulo,
        'vl_desconto': vl_desconto,
        'vl_acrescimo': vl_acrescimo,
        'vl_liquido': vl_liquido,
        'vl_pago': vl_pago,
    }


def map_status_titulo_pagar(status, vl_titulo, vl_pago):
    status = (limpar_valor(status) or '').upper()
    if status == 'AB':
        return 'A'
    if status in ('BA', 'B'):
        return 'L'
    if status == 'BP':
        return 'P'
    if status in ('CA', 'C', 'CN'):
        return 'C'
    return 'A'


def map_tp_acao_historico(tp_historico, historico, dt_anterior=None, dt_atual=None):
    texto = chave_texto(historico) or ''
    if 'LIQUID' in texto or tp_historico == 13:
        return 'LIQUIDACAO'
    if 'INCLUSAO' in texto or 'INCLUS' in texto or tp_historico == 12:
        return 'CRIACAO'
    if 'CANCEL' in texto:
        return 'CANCELAMENTO'
    if dt_anterior != dt_atual:
        return 'ALTERACAO_VENCIMENTO'
    return 'ALTERACAO'


def buscar_codigo_minimo_web(cursor_web, tabela_web, coluna, tenant_id=None, cd_empresa=None):
    filtros = []
    params = []
    if tenant_id is not None:
        filtros.append(f"{quote_identificador('tenant_id')} = %s")
        params.append(tenant_id)
    if cd_empresa is not None:
        filtros.append(f"{quote_identificador('cd_empresa')} = %s")
        params.append(cd_empresa)
    where_sql = f" WHERE {' AND '.join(filtros)}" if filtros else ""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador(coluna)}
          FROM {tabela_web}
          {where_sql}
         ORDER BY {quote_identificador(coluna)}
         LIMIT 1
        """,
        params
    )
    row = cursor_web.fetchone()
    return row[0] if row else None


def buscar_cliente_padrao_web(cursor_web, tabela_web_cliente, tenant_id, cd_empresa):
    """Busca o cadastro CONSUMIDOR usado para NFs sem cliente cadastrado."""
    cursor_web.execute(
        f"""
        SELECT {quote_identificador('cd_cliente')}, {quote_identificador('nm_cliente')}
          FROM {tabela_web_cliente}
         WHERE {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_empresa')} = %s
           AND UPPER(TRIM({quote_identificador('nm_cliente')})) LIKE 'CONSUMIDOR%'
         ORDER BY {quote_identificador('cd_cliente')}
         LIMIT 1
        """,
        (tenant_id, cd_empresa)
    )
    row = cursor_web.fetchone()
    if row:
        print(
            f"[OK] Cliente padrao para NFs sem cadastro: "
            f"cd_cliente={row[0]} ({limpar_valor(row[1])})."
        )
        return row[0]

    print(
        f"[AVISO] Nenhum cliente CONSUMIDOR encontrado no tenant={tenant_id}, "
        f"empresa={cd_empresa}; NFs sem de/para continuarao sendo reportadas."
    )
    return None


def carregar_mapa_usuario_rotinas(cursor_giv, cursor_web, tabela_web_usuario, tenant_id, cd_empresa, cd_empresa_giv=None):
    where_sql = ""
    params = []
    if cd_empresa_giv is not None:
        where_sql = "WHERE cd_empresa = ?"
        params.append(cd_empresa_giv)
    usuarios_giv = buscar_registros_giv(
        cursor_giv,
        f"""
        SELECT
            cd_usuario,
            cd_empresa,
            nm_usuario,
            nm_login
          FROM usuario
         {where_sql}
         ORDER BY cd_usuario
        """,
        params if params else None
    )
    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_usuario')},
            {quote_identificador('nm_login')},
            {quote_identificador('nm_usuario')}
          FROM {tabela_web_usuario}
         WHERE {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_empresa')} = %s
         ORDER BY {quote_identificador('cd_usuario')}
        """,
        (tenant_id, cd_empresa)
    )
    por_login = {}
    por_nome = {}
    for cd_usuario, login, nome in cursor_web.fetchall():
        chave_login = chave_texto(login)
        chave_nome = chave_texto(nome)
        if chave_login:
            por_login.setdefault(chave_login, cd_usuario)
        if chave_nome:
            por_nome.setdefault(chave_nome, cd_usuario)

    mapa = {}
    for usuario in usuarios_giv:
        cd_origem = usuario.get('cd_usuario')
        cd_web = por_nome.get(chave_texto(usuario.get('nm_usuario')))
        if cd_web is None:
            cd_web = por_login.get(chave_texto(usuario.get('nm_login')))
        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web

    padrao = buscar_codigo_minimo_web(
        cursor_web,
        tabela_web_usuario,
        'cd_usuario',
        tenant_id=tenant_id,
        cd_empresa=cd_empresa
    )
    print(
        f"[OK] Mapa usuario rotinas: {len(mapa)} usuarios por nome/login "
        f"no tenant={tenant_id}, empresa={cd_empresa}; usuario padrao={padrao}."
    )
    return mapa, padrao


def carregar_mapa_cliente_rotinas(cursor_giv, cursor_web, tabela_web_cliente, tenant_id, cd_empresa, cd_empresa_giv=None):
    clientes_giv = buscar_clientes_giv(cursor_giv, cd_empresa_giv)
    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_cliente')},
            {quote_identificador('nm_cliente')},
            {quote_identificador('dt_cadastro')},
            {quote_identificador('cpf')}
          FROM {tabela_web_cliente}
         WHERE {quote_identificador('tenant_id')} = %s
           AND {quote_identificador('cd_empresa')} = %s
        """,
        (tenant_id, cd_empresa)
    )
    por_doc = {}
    por_nome_data = {}
    por_nome = {}
    for cd_cliente, nome, dt_cadastro, cpf in cursor_web.fetchall():
        doc = normalizar_cadastro_nacional(cpf)
        if doc:
            por_doc.setdefault(doc, set()).add(cd_cliente)
        chave_nome = chave_texto(nome)
        chave = (chave_texto(nome), chave_data(dt_cadastro))
        if chave[0] and chave[1]:
            por_nome_data.setdefault(chave, set()).add(cd_cliente)
        if chave_nome:
            por_nome.setdefault(chave_nome, set()).add(cd_cliente)

    mapa = {}
    ambiguos = 0
    resolvidos_duplicados = 0
    for cliente in clientes_giv:
        cd_origem = cliente.get('cd_cliente')
        cd_web = None
        doc = normalizar_documento_cliente(
            cliente.get('tp_pessoa'),
            cliente.get('cpf'),
            cliente.get('cgc'),
            cliente.get('cnpj')
        )
        candidatos_doc = por_doc.get(doc) if doc else None
        if candidatos_doc and len(candidatos_doc) == 1:
            cd_web = next(iter(candidatos_doc))
        elif candidatos_doc:
            cd_web = max(candidatos_doc)
            resolvidos_duplicados += 1
        else:
            chave = (chave_texto(cliente.get('nm_cliente')), chave_data(cliente.get('dt_cadastro')))
            candidatos_nome = por_nome_data.get(chave)
            if candidatos_nome and len(candidatos_nome) == 1:
                cd_web = next(iter(candidatos_nome))
            elif candidatos_nome:
                cd_web = max(candidatos_nome)
                resolvidos_duplicados += 1
            else:
                candidatos_so_nome = por_nome.get(chave[0]) if chave[0] else None
                if candidatos_so_nome and len(candidatos_so_nome) == 1:
                    cd_web = next(iter(candidatos_so_nome))
                elif candidatos_so_nome:
                    cd_web = max(candidatos_so_nome)
                    resolvidos_duplicados += 1

        if cd_web is not None:
            mapa[(cliente.get('cd_empresa'), cd_origem)] = cd_web
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web
        elif candidatos_doc or candidatos_nome:
            ambiguos += 1

    print(
        f"[OK] Mapa cliente rotinas: {len(mapa)} chaves; "
        f"{resolvidos_duplicados} duplicados resolvidos pelo maior codigo Web; "
        f"{ambiguos} clientes ficaram sem mapa."
    )
    return mapa


def carregar_mapa_produto_rotinas(
    cursor_giv,
    cursor_web,
    tabelas_web_produto,
    tenant_id,
    mapas_auxiliares=None
):
    produtos_giv = buscar_produtos_giv(cursor_giv)
    barcodes_giv = buscar_barcodes_produto_giv(cursor_giv)
    mapas_auxiliares = mapas_auxiliares or {}
    produtos_por_codigo = {
        produto.get('cd_produto'): produto
        for produto in produtos_giv
    }
    cursor_web.execute(
        f"""
        SELECT
            {quote_identificador('cd_produto')},
            {quote_identificador('ds_produto')},
            {quote_identificador('cd_produto_barra_ean')},
            {quote_identificador('cd_referencia')}
          FROM {tabelas_web_produto['produto_info']}
         WHERE {quote_identificador('tenant_id')} = %s
        """,
        (tenant_id,)
    )
    por_nome = {}
    por_barcode = {}
    por_referencia = {}
    por_referencia_nome = {}
    for cd_produto, ds_produto, barcode, cd_referencia in cursor_web.fetchall():
        chave = chave_texto(ds_produto)
        if chave:
            por_nome.setdefault(chave, set()).add(cd_produto)
        barcode = somente_digitos(barcode)
        if barcode:
            por_barcode.setdefault(barcode, set()).add(cd_produto)
        referencia = chave_texto(cd_referencia)
        if referencia:
            por_referencia.setdefault(referencia, set()).add(cd_produto)
            if chave:
                por_referencia_nome.setdefault((referencia, chave), set()).add(cd_produto)

    for tabela_web, coluna_codigo in (
        (tabelas_web_produto.get('produto'), 'cd_produto'),
        (tabelas_web_produto.get('produto_filho'), 'cd_produto_filho'),
    ):
        if not tabela_web:
            continue
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador(coluna_codigo)},
                {quote_identificador('cd_produto_barra_ean')}
              FROM {tabela_web}
             WHERE {quote_identificador('tenant_id')} = %s
               AND {quote_identificador('cd_produto_barra_ean')} IS NOT NULL
            """,
            (tenant_id,)
        )
        for cd_produto, barcode in cursor_web.fetchall():
            barcode = somente_digitos(barcode)
            if barcode:
                por_barcode.setdefault(barcode, set()).add(cd_produto)

    mapa = {}
    ambiguos = 0
    por_barcode_ok = 0
    por_referencia_ok = 0
    por_nome_ok = 0
    for produto in produtos_giv:
        cd_origem = produto.get('cd_produto')
        cd_web = None
        barcode = somente_digitos(barcodes_giv.get(cd_origem))
        referencia = chave_texto(produto.get('cd_referencia'))
        nome = chave_texto(produto.get('ds_produto'))
        candidatos_barcode = por_barcode.get(barcode) if barcode else None
        if candidatos_barcode and len(candidatos_barcode) == 1:
            cd_web = next(iter(candidatos_barcode))
            por_barcode_ok += 1
        else:
            candidatos_referencia_nome = (
                por_referencia_nome.get((referencia, nome))
                if referencia and nome
                else None
            )
            candidatos_referencia = por_referencia.get(referencia) if referencia else None
            candidatos_nome = por_nome.get(nome) if nome else None
            if candidatos_referencia_nome and len(candidatos_referencia_nome) == 1:
                cd_web = next(iter(candidatos_referencia_nome))
                por_referencia_ok += 1
            elif candidatos_referencia and len(candidatos_referencia) == 1:
                cd_web = next(iter(candidatos_referencia))
                por_referencia_ok += 1
            elif candidatos_nome and len(candidatos_nome) == 1:
                cd_web = next(iter(candidatos_nome))
                por_nome_ok += 1
            elif candidatos_barcode or candidatos_referencia_nome or candidatos_referencia or candidatos_nome:
                ambiguos += 1
        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web

    # Variacoes podem ter o mesmo nome/referencia no Web. Nesses casos, o
    # de/para correto e a chave funcional da variacao: pai + cor + tamanho.
    # Isso reaproveita a variacao que ja existe e evita criar produto duplicado
    # durante uma retomada de documentos.
    variacoes_por_pai = defaultdict(list)
    pais_web = set()
    tabela_web_filho = tabelas_web_produto.get('produto_filho')
    if tabela_web_filho:
        cursor_web.execute(
            f"""
            SELECT
                {quote_identificador('cd_produto_filho')},
                {quote_identificador('cd_produto_pai')},
                {quote_identificador('cd_cor')},
                {quote_identificador('cd_tamanho')}
              FROM {tabela_web_filho}
             WHERE {quote_identificador('tenant_id')} = %s
            """,
            (tenant_id,)
        )
        for cd_filho, cd_pai, cd_cor, cd_tamanho in cursor_web.fetchall():
            variacoes_por_pai[(cd_pai, cd_cor, cd_tamanho)].append(cd_filho)
            pais_web.add(cd_pai)

    def codigo_mapeado(mapa_auxiliar, valor):
        if valor is None:
            return None
        return mapa_auxiliar.get(valor) or mapa_auxiliar.get(normalizar_codigo_cidade(valor))

    def candidatos_produto_web(produto):
        """Retorna candidatos existentes para o produto por seus dados estaveis."""
        barcode = somente_digitos(barcodes_giv.get(produto.get('cd_produto')))
        referencia = chave_texto(produto.get('cd_referencia'))
        nome = chave_texto(produto.get('ds_produto'))
        candidatos = set()
        if barcode:
            candidatos.update(por_barcode.get(barcode, set()))
        if referencia and nome:
            candidatos.update(por_referencia_nome.get((referencia, nome), set()))
        if referencia:
            candidatos.update(por_referencia.get(referencia, set()))
        if nome:
            candidatos.update(por_nome.get(nome, set()))
        return candidatos

    mapa_cor = mapas_auxiliares.get('cor', {})
    mapa_tamanho = mapas_auxiliares.get('tamanho', {})
    pais_reaproveitados = 0
    por_variacao_ok = 0
    pais_giv_necessarios = {
        produto.get('cd_produto_pai')
        for produto in produtos_giv
        if produto.get('cd_produto_pai') not in (None, 0)
        and mapa.get(produto.get('cd_produto_pai')) is None
    }

    # Quando ja existem varias familias iguais no Web, escolhemos a familia
    # de maior codigo para que os filhos possam ser ligados sem recadastrar o
    # mesmo pai. A escolha e feita apenas para pais realmente usados por
    # variacoes, nao para todo cadastro ambiguo.
    for produto in produtos_giv:
        cd_origem = produto.get('cd_produto')
        if cd_origem not in pais_giv_necessarios or mapa.get(cd_origem) is not None:
            continue
        candidatos_pai = candidatos_produto_web(produto).intersection(pais_web)
        if candidatos_pai:
            cd_web = max(candidatos_pai)
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web
            pais_reaproveitados += 1

    for produto in produtos_giv:
        cd_origem = produto.get('cd_produto')
        if mapa.get(cd_origem) is not None:
            continue
        cd_pai_web = codigo_mapeado(mapa, produto.get('cd_produto_pai'))
        candidatos_pai = set()
        if cd_pai_web is not None:
            candidatos_pai.add(cd_pai_web)
        pai_giv = produto.get('cd_produto_pai')
        if pai_giv is not None:
            pai_origem = produtos_por_codigo.get(pai_giv)
            if pai_origem:
                candidatos_pai.update(
                    candidatos_produto_web(pai_origem).intersection(pais_web)
                )
        cd_cor_web = codigo_mapeado(mapa_cor, produto.get('cd_cor'))
        cd_tamanho_web = codigo_mapeado(mapa_tamanho, produto.get('cd_tamanho'))
        candidatos_variacao = set()
        for pai_web in candidatos_pai:
            candidatos_variacao.update(
                variacoes_por_pai.get((pai_web, cd_cor_web, cd_tamanho_web), [])
            )
        if candidatos_variacao:
            # Familias duplicadas no Web representam o mesmo item funcional;
            # reutilizamos uma delas, mantendo a unique pai+cor+tamanho.
            cd_web = max(candidatos_variacao)
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web
            por_variacao_ok += 1

    ambiguos_finais = sum(
        1 for produto in produtos_giv if mapa.get(produto.get('cd_produto')) is None
    )
    print(
        f"[OK] Mapa produto rotinas: {len(mapa)} produtos "
        f"({por_barcode_ok} por barcode, {por_referencia_ok} por referencia, "
        f"{por_nome_ok} por nome, {por_variacao_ok} por pai+cor+tamanho, "
        f"{pais_reaproveitados} pais reaproveitados); "
        f"{ambiguos_finais} produtos ficaram sem mapa."
    )
    return mapa


def coletar_codigos_produto_rotinas(
    cursor_giv,
    tabelas_selecionadas,
    cd_empresa_giv=None,
    mapa_nf_saida=None
):
    """Coleta os produtos usados nas rotinas/documentos selecionados."""
    origens = {
        'condicional': 'orcamento_item',
        'pedido_compra': 'pedido_compra_item',
        'nota_fiscal_entrada': 'nota_fiscal_entrada_item',
        'prevenda': 'prevenda_item',
        'nota_fiscal_saida': 'nota_fiscal_saida_item',
    }
    codigos = set()
    for chave, tabela in origens.items():
        if chave not in tabelas_selecionadas:
            continue
        try:
            if chave == 'nota_fiscal_saida' and mapa_nf_saida is not None:
                # Em uma retomada, nao precisamos recadastrar produtos usados
                # pelas NFs que ja existem no Web. Isso evita duplicar produtos
                # auxiliares cadastrados na primeira execucao parcial.
                registros_itens = buscar_registros_giv_tabela(
                    cursor_giv,
                    tabela,
                    'cd_empresa, nr_nota, serie, cd_produto',
                    cd_empresa_giv=cd_empresa_giv
                )
                for reg in registros_itens:
                    chave_nf = (
                        reg.get('cd_empresa'),
                        reg.get('nr_nota'),
                        serie_doc(reg.get('serie')),
                    )
                    if chave_nf in mapa_nf_saida:
                        continue
                    cd_produto = reg.get('cd_produto')
                    if cd_produto is not None:
                        codigos.add(normalizar_codigo_cidade(cd_produto) or cd_produto)
                continue

            where_empresa = ""
            if cd_empresa_giv is not None and tabela_giv_tem_coluna(cursor_giv, tabela, 'cd_empresa'):
                where_empresa = f" AND cd_empresa = {int(cd_empresa_giv)}"
            for reg in buscar_registros_giv(
                cursor_giv,
                f"SELECT DISTINCT cd_produto FROM {tabela} WHERE cd_produto IS NOT NULL{where_empresa}"
            ):
                cd_produto = reg.get('cd_produto')
                if cd_produto is not None:
                    codigos.add(normalizar_codigo_cidade(cd_produto) or cd_produto)
        except Exception as e:
            print(f"[AVISO] Nao foi possivel coletar produtos de {tabela}: {e}")
    return codigos


def expandir_produtos_com_pais(produtos_por_codigo, codigos, mapa_produtos):
    """Inclui pais GIV necessarios para cadastrar filhos faltantes."""
    pendentes = list(codigos)
    expandidos = set()
    while pendentes:
        cd_produto = pendentes.pop()
        if cd_produto in expandidos or cd_produto in mapa_produtos:
            continue
        reg = produtos_por_codigo.get(cd_produto)
        if not reg:
            expandidos.add(cd_produto)
            continue
        expandidos.add(cd_produto)
        cd_pai = reg.get('cd_produto_pai')
        if cd_pai not in (None, 0) and cd_pai not in mapa_produtos and cd_pai not in expandidos:
            pendentes.append(cd_pai)
    return expandidos


def cadastrar_produtos_faltantes_rotinas(
    cursor_giv,
    cursor_web,
    tabelas_web_produto,
    tabelas_selecionadas,
    tenant_id,
    cd_empresa,
    mapa_produtos,
    cd_empresa_giv=None,
    mapas_convertidos=None,
    mapa_nf_saida=None
):
    """Cadastra somente produtos usados nos documentos que ainda nao existem no Web."""
    codigos_usados = coletar_codigos_produto_rotinas(
        cursor_giv,
        tabelas_selecionadas,
        cd_empresa_giv,
        mapa_nf_saida
    )
    if not codigos_usados:
        return {
            'tabela': 'produto_faltante_documento',
            'lidos': 0,
            'inseridos': 0,
            'existentes': 0,
            'erros': 0,
            'erros_detalhe': [],
        }

    codigos_faltantes = {
        cd_produto for cd_produto in codigos_usados
        if mapa_produtos.get(cd_produto) is None
    }
    if not codigos_faltantes:
        print("[OK] Todos os produtos usados nas rotinas/documentos possuem de/para Web.")
        return {
            'tabela': 'produto_faltante_documento',
            'lidos': len(codigos_usados),
            'inseridos': 0,
            'existentes': len(codigos_usados),
            'erros': 0,
            'erros_detalhe': [],
            'motivo_existentes': 'produto usado em documento ja possui de/para Web',
        }

    print()
    print(
        f"[...] Cadastrando {len(codigos_faltantes)} produtos faltantes usados "
        "nas rotinas/documentos..."
    )
    preflight_produto(cursor_web, tabelas_web_produto, tenant_id, cd_empresa)
    produtos_giv = buscar_produtos_giv(cursor_giv)
    produtos_por_codigo = {}
    for reg in produtos_giv:
        cd_produto = reg.get('cd_produto')
        produtos_por_codigo[cd_produto] = reg
        cd_normalizado = normalizar_codigo_cidade(cd_produto)
        if cd_normalizado is not None:
            produtos_por_codigo[cd_normalizado] = reg
    codigos_para_inserir = expandir_produtos_com_pais(
        produtos_por_codigo,
        codigos_faltantes,
        mapa_produtos
    )

    inexistentes_giv = sorted(cd for cd in codigos_para_inserir if cd not in produtos_por_codigo)
    erros_detalhe = []
    for cd_produto in inexistentes_giv:
        registrar_erro_validacao(
            erros_detalhe,
            'produto_faltante_documento',
            f"cd_produto_giv={cd_produto}",
            'produto nao encontrado no GIV'
        )
    codigos_para_inserir = [
        cd for cd in codigos_para_inserir
        if cd in produtos_por_codigo and mapa_produtos.get(cd) is None
    ]
    if not codigos_para_inserir:
        return {
            'tabela': 'produto_faltante_documento',
            'lidos': len(codigos_usados),
            'inseridos': 0,
            'existentes': len(codigos_usados) - len(codigos_faltantes),
            'erros': len(erros_detalhe),
            'erros_detalhe': erros_detalhe,
        }

    mapas = carregar_mapas_cadastros_produto(
        cursor_giv,
        cursor_web,
        tabelas_web_produto,
        tenant_id,
        cd_empresa,
        None,
        cd_empresa_giv
    )
    mapas.update(carregar_mapas_auxiliares_produto(
        cursor_giv,
        cursor_web,
        tabelas_web_produto,
        tenant_id,
        cd_empresa,
        cd_empresa_giv
    ))
    for chave, mapa_convertido in (mapas_convertidos or {}).items():
        mapas.setdefault(chave, {}).update(mapa_convertido or {})
    padroes_produto = atualizar_padroes_produto(mapas)
    preencher_padroes_produto_web(cursor_web, tabelas_web_produto, padroes_produto, tenant_id, cd_empresa)

    sequence_produto = buscar_sequence_coluna_web(
        cursor_web,
        tabelas_web_produto['produto'],
        'cd_produto'
    )
    sincronizar_sequence_produto_global(
        cursor_web,
        tabelas_web_produto['produto'],
        tabelas_web_produto['produto_filho'],
        sequence_produto
    )
    codigos_web = iter(reservar_valores_sequence(
        cursor_web,
        sequence_produto,
        len(codigos_para_inserir)
    ))
    precos_por_produto = buscar_precos_produto_giv(cursor_giv)
    cd_empresa_giv = cd_empresa_giv if cd_empresa_giv is not None else cd_empresa
    print(f"[INFO] Produto faltante: saldo sera buscado no GIV com cd_empresa={cd_empresa_giv}.")
    estoques_por_produto = buscar_estoques_produto_giv(cursor_giv, cd_empresa_giv)
    barcodes_por_produto = filtrar_barcodes_existentes_web(
        cursor_web,
        tabelas_web_produto,
        tenant_id,
        buscar_barcodes_produto_giv(cursor_giv)
    )
    classificacao = classificar_produtos_giv(produtos_giv)
    limites = {
        'produto': buscar_limites_texto_web(cursor_web, tabelas_web_produto['produto']),
        'produto_info': buscar_limites_texto_web(cursor_web, tabelas_web_produto['produto_info']),
        'produto_preco': buscar_limites_texto_web(cursor_web, tabelas_web_produto['produto_preco']),
        'produto_estoque': buscar_limites_texto_web(cursor_web, tabelas_web_produto['produto_estoque']),
        'produto_filho': buscar_limites_texto_web(cursor_web, tabelas_web_produto['produto_filho']),
    }
    mapa_status_produto = dict(MAPA_STATUS_PRODUTO_PADRAO)

    registros = [produtos_por_codigo[cd] for cd in codigos_para_inserir]
    raizes = [reg for reg in registros if classificacao[reg.get('cd_produto')]['eh_raiz']]
    filhos = [reg for reg in registros if classificacao[reg.get('cd_produto')]['eh_filho']]
    inseridos = 0
    erros = len(erros_detalhe)

    itens_raiz = []
    for reg in raizes:
        cd_produto_giv = reg.get('cd_produto')
        cd_produto_web = next(codigos_web)
        produto, produto_info, erros_validacao = montar_produto_raiz_web(
            reg,
            cd_produto_web,
            tenant_id,
            mapas,
            mapa_status_produto,
            classificacao,
            barcodes_por_produto.get(cd_produto_giv)
        )
        if erros_validacao:
            erros += 1
            detalhe = f"cd_produto_giv={cd_produto_giv}: {'; '.join(erros_validacao)}"
            erros_detalhe.append(detalhe)
            if erros <= 10:
                print(f"  [PULO] produto faltante {detalhe}")
            elif erros == 11:
                print("  ... suprimindo demais erros de produto faltante")
            continue
        produto_colecao = None
        if produto_info.get('cd_colecao'):
            produto_colecao = {
                'cd_produto': cd_produto_web,
                'cd_colecao': produto_info.get('cd_colecao'),
                'tenant_id': tenant_id,
            }
        itens_raiz.append({
            'cd_produto_giv': cd_produto_giv,
            'cd_produto_web': cd_produto_web,
            'produto': produto,
            'produto_info': produto_info,
            'precos': montar_precos_web(precos_por_produto.get(cd_produto_giv, []), cd_produto_web, tenant_id),
            'estoque': converter_produto_estoque(
                estoques_por_produto.get(cd_produto_giv),
                cd_produto_web,
                tenant_id,
                cd_empresa
            ),
            'produto_colecao': produto_colecao,
        })

    if itens_raiz:
        inseridos_raiz, erros_raiz, erros_raiz_detalhe, itens_raiz_sucesso = (
            inserir_produtos_raiz_lote_transacional(
                cursor_web,
                tabelas_web_produto,
                itens_raiz,
                limites,
                TAMANHO_LOTE_PRODUTO
            )
        )
        inseridos += inseridos_raiz
        erros += erros_raiz
        erros_detalhe.extend(erros_raiz_detalhe)
        for item in itens_raiz_sucesso:
            mapa_produtos[item['cd_produto_giv']] = item['cd_produto_web']
            mapa_produtos[normalizar_codigo_cidade(item['cd_produto_giv'])] = item['cd_produto_web']

    itens_filhos = []
    filhos_sem_pai = []
    variacoes_por_pai_web = carregar_variacoes_por_pai_web(
        cursor_web, tabelas_web_produto.get('produto_filho'), tenant_id
    )
    variacoes_duplicadas = 0
    for reg in filhos:
        cd_produto_giv = reg.get('cd_produto')
        cd_pai_giv = reg.get('cd_produto_pai')
        if mapa_produtos.get(cd_pai_giv) is None:
            filhos_sem_pai.append(reg)
            continue
        cd_produto_web = next(codigos_web)
        produto, produto_filho, erros_validacao = montar_produto_filho_web(
            reg,
            cd_produto_web,
            tenant_id,
            mapas,
            mapa_produtos,
            barcodes_por_produto.get(cd_produto_giv)
        )
        if erros_validacao:
            erros += 1
            detalhe = f"cd_produto_giv={cd_produto_giv}: {'; '.join(erros_validacao)}"
            erros_detalhe.append(detalhe)
            if erros <= 10:
                print(f"  [PULO] produto filho faltante {detalhe}")
            elif erros == 11:
                print("  ... suprimindo demais erros de produto filho faltante")
            continue

        # Mesma regra da conversao completa: ux_produto_filho_familia_variacao
        # impede duas variacoes com o mesmo pai + cor + tamanho.
        variacao = (
            produto_filho.get('cd_produto_pai'),
            produto_filho.get('cd_cor'),
            produto_filho.get('cd_tamanho'),
        )
        if variacao in variacoes_por_pai_web:
            variacoes_duplicadas += 1
            continue
        variacoes_por_pai_web.add(variacao)

        itens_filhos.append({
            'cd_produto_giv': cd_produto_giv,
            'cd_produto_web': cd_produto_web,
            'produto': produto,
            'produto_filho': produto_filho,
            'precos': montar_precos_web(
                precos_por_produto.get(cd_produto_giv, []),
                cd_produto_web,
                tenant_id
            ),
            'estoque': converter_produto_estoque(
                estoques_por_produto.get(cd_produto_giv),
                cd_produto_web,
                tenant_id,
                cd_empresa
            ),
        })

    if filhos_sem_pai:
        for reg in filhos_sem_pai:
            registrar_erro_validacao(
                erros_detalhe,
                'produto_faltante_documento',
                f"cd_produto_giv={reg.get('cd_produto')}",
                f"pai GIV {reg.get('cd_produto_pai')} sem de/para web"
            )
        erros = len(erros_detalhe)

    if itens_filhos:
        inseridos_filhos, erros_filhos, erros_filhos_detalhe, itens_filhos_sucesso = (
            inserir_produtos_filhos_lote_transacional(
                cursor_web,
                tabelas_web_produto,
                itens_filhos,
                limites,
                TAMANHO_LOTE_PRODUTO
            )
        )
        inseridos += inseridos_filhos
        erros += erros_filhos
        erros_detalhe.extend(erros_filhos_detalhe)
        for item in itens_filhos_sucesso:
            mapa_produtos[item['cd_produto_giv']] = item['cd_produto_web']
            mapa_produtos[normalizar_codigo_cidade(item['cd_produto_giv'])] = item['cd_produto_web']

    if variacoes_duplicadas:
        print(
            f"[OK] Produto faltante: {variacoes_duplicadas} variacoes duplicadas no GIV "
            "(mesmo pai + cor + tamanho) foram ignoradas."
        )

    print(
        f"[OK] Produtos faltantes para documentos: {inseridos} cadastrados, "
        f"{erros} erros."
    )
    return {
        'tabela': 'produto_faltante_documento',
        'lidos': len(codigos_usados),
        'inseridos': inseridos,
        'existentes': len(codigos_usados) - len(codigos_faltantes),
        'erros': erros,
        'erros_detalhe': erros_detalhe,
        'motivo_existentes': 'produto usado em documento ja possui de/para Web',
    }


def carregar_mapa_forma_pagamento(cursor_giv, cursor_web, tabela_web_forma_pagamento, tenant_id, cd_empresa_giv=None):
    formas_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'forma_pagamento',
        'cd_forma_pagamento',
        cd_empresa_giv=cd_empresa_giv
    )
    mapa_nome = buscar_mapa_nome_web(
        cursor_web,
        tabela_web_forma_pagamento,
        'cd_forma_pagto',
        'ds_forma_pagto',
        tenant_id=tenant_id
    )
    mapa = {}
    for forma in formas_giv:
        cd_web = mapa_nome.get(chave_nome_produto(forma.get('ds_forma_pagamento')))
        if cd_web is not None:
            mapa[forma.get('cd_forma_pagamento')] = cd_web
            mapa[normalizar_codigo_cidade(forma.get('cd_forma_pagamento'))] = cd_web
    padrao = buscar_codigo_minimo_web(cursor_web, tabela_web_forma_pagamento, 'cd_forma_pagto', tenant_id=tenant_id)
    print(f"[OK] Mapa forma_pagamento: {len(mapa)} formas; forma padrao={padrao}.")
    return mapa, padrao


def carregar_mapa_cfop(cursor_web, tabela_web_cfop):
    cursor_web.execute(f"SELECT {quote_identificador('codigo')} FROM {tabela_web_cfop}")
    return {row[0] for row in cursor_web.fetchall()}


def carregar_dados_empresa(cursor_web, tabela_web_empresa, cd_empresa, tenant_id):
    cursor_web.execute(
        f"""
        SELECT cnpj, inscricao_estadual, inscricao_municipal
          FROM {tabela_web_empresa}
         WHERE {quote_identificador('cd_empresa')} = %s
           AND {quote_identificador('tenant_id')} = %s
         LIMIT 1
        """,
        (cd_empresa, tenant_id)
    )
    row = cursor_web.fetchone()
    if not row:
        return {}
    return {
        'cnpj': row[0],
        'inscricao_estadual': row[1],
        'inscricao_municipal': row[2],
    }


def processar_bancos(cursor_giv, cursor_web, tabela_web_banco, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Buscando bancos do GIV...")
    bancos_giv = buscar_registros_giv_tabela(cursor_giv, 'banco', 'cd_banco', cd_empresa_giv=cd_empresa_giv)
    print(f"[OK] {len(bancos_giv)} bancos encontrados no GIV.")

    sequence_banco = buscar_sequence_coluna_web(cursor_web, tabela_web_banco, 'cd_banco')
    max_banco = sincronizar_sequence_com_max(cursor_web, tabela_web_banco, 'cd_banco', sequence_banco)
    print(f"[OK] Sequence de banco: {sequence_banco} (sincronizada com max={max_banco}).")

    cursor_web.execute(
        f"""
        SELECT cd_banco, nm_banco, nr_digito
          FROM {tabela_web_banco}
         WHERE tenant_id = %s
           AND cd_empresa = %s
        """,
        (tenant_id, cd_empresa)
    )
    existentes = {}
    for cd_banco, nm_banco, nr_digito in cursor_web.fetchall():
        existentes[(chave_texto(nm_banco), nr_digito)] = cd_banco

    mapa = {}
    registros = []
    reaproveitados = 0
    pendentes = []
    for banco in bancos_giv:
        chave = (chave_texto(banco.get('nm_banco')), banco.get('nr_digito'))
        cd_web = existentes.get(chave)
        if cd_web is not None:
            mapa[banco.get('cd_banco')] = cd_web
            mapa[normalizar_codigo_cidade(banco.get('cd_banco'))] = cd_web
            reaproveitados += 1
            continue
        pendentes.append(banco)

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_banco, len(pendentes)))
    for banco in pendentes:
        cd_web = next(codigos)
        mapa[banco.get('cd_banco')] = cd_web
        mapa[normalizar_codigo_cidade(banco.get('cd_banco'))] = cd_web
        registros.append(limpar_registro({
            'cd_banco': cd_web,
            'nm_banco': limpar_valor(banco.get('nm_banco')) or f"BANCO {banco.get('cd_banco')}",
            'nr_digito': banco.get('nr_digito'),
            'id_status': 'A',
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
        }))

    if reaproveitados:
        print(f"[OK] {reaproveitados} bancos reaproveitados por nome/digito no mesmo tenant/empresa.")
    print(f"[OK] {len(registros)} bancos novos para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_banco, registros, 'banco')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_banco,
        registros,
        'cd_banco',
        'sp_banco'
    )
    mapa = remover_mapa_por_codigos_erro(mapa, erros_detalhe, 'cd_banco')
    return mapa, {
        'tabela': 'banco',
        'lidos': len(bancos_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def carregar_mapa_banco_rotinas(cursor_giv, cursor_web, tabela_web_banco, tenant_id, cd_empresa, cd_empresa_giv=None):
    """Relaciona banco GIV -> banco Web por nome/digito para rotinas que dependem de conta."""
    bancos_giv = buscar_registros_giv_tabela(cursor_giv, 'banco', 'cd_banco', cd_empresa_giv=cd_empresa_giv)
    cursor_web.execute(
        f"""
        SELECT cd_banco, nm_banco, nr_digito
          FROM {tabela_web_banco}
         WHERE tenant_id = %s
           AND cd_empresa = %s
         ORDER BY cd_banco
        """,
        (tenant_id, cd_empresa)
    )
    por_chave = {}
    por_codigo = {}
    for cd_banco, nm_banco, nr_digito in cursor_web.fetchall():
        por_chave.setdefault((chave_texto(nm_banco), nr_digito), cd_banco)
        por_codigo.setdefault(cd_banco, cd_banco)

    mapa = {}
    for banco in bancos_giv:
        cd_origem = banco.get('cd_banco')
        cd_web = por_chave.get((chave_texto(banco.get('nm_banco')), banco.get('nr_digito')))
        if cd_web is None:
            cd_web = por_codigo.get(cd_origem)
        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web

    print(f"[OK] Mapa banco rotinas: {len(mapa)} bancos relacionados por nome/digito.")
    return mapa


def processar_banco_contas(
    cursor_giv,
    cursor_web,
    tabela_web_banco_conta,
    mapa_bancos,
    tenant_id,
    cd_empresa,
    cd_empresa_giv=None
):
    print()
    print("[...] Buscando contas bancarias do GIV...")
    contas_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'banco_conta',
        'cd_empresa, cd_conta',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(contas_giv)} contas bancarias encontradas no GIV.")

    sequence_conta = buscar_sequence_coluna_web(cursor_web, tabela_web_banco_conta, 'cd_conta')
    max_conta = sincronizar_sequence_com_max(cursor_web, tabela_web_banco_conta, 'cd_conta', sequence_conta)
    print(f"[OK] Sequence de banco_conta: {sequence_conta} (sincronizada com max={max_conta}).")

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_conta, len(contas_giv)))
    registros = []
    mapa = {}
    erros_detalhe = []
    for conta in contas_giv:
        cd_conta_web = next(codigos)
        cd_banco_giv = conta.get('cd_banco')
        cd_banco_web = mapa_bancos.get(cd_banco_giv) or mapa_bancos.get(normalizar_codigo_cidade(cd_banco_giv))
        if cd_banco_web is None:
            registrar_erro_validacao(
                erros_detalhe,
                'banco_conta',
                f"cd_conta={conta.get('cd_conta')}",
                f"banco GIV {cd_banco_giv} sem de/para Web"
            )
            continue

        cd_conta_giv = conta.get('cd_conta')
        mapa[(conta.get('cd_empresa'), cd_conta_giv)] = cd_conta_web
        mapa[cd_conta_giv] = cd_conta_web
        mapa[normalizar_codigo_cidade(cd_conta_giv)] = cd_conta_web
        registros.append(limpar_registro({
            'cd_conta': cd_conta_web,
            'cd_banco': cd_banco_web,
            'nr_conta': limpar_valor(conta.get('nr_conta')) or str(cd_conta_giv or cd_conta_web),
            'ds_conta': limpar_valor(conta.get('ds_conta')) or f"CONTA {cd_conta_giv or cd_conta_web}",
            'nr_agencia': limpar_valor(conta.get('nr_agencia')),
            'vl_saldo': valor_decimal_ou_zero(conta.get('vl_saldo')),
            'vl_limite': valor_decimal_ou_zero(conta.get('vl_limite')),
            'id_ativa': 'S' if valor_flag(conta.get('id_ativa'), 'S') in ('S', 'A') else 'N',
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
        }))

    aplicar_limites_texto_web(cursor_web, tabela_web_banco_conta, registros, 'banco_conta')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_banco_conta,
        registros,
        'cd_conta',
        'sp_banco_conta'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa = remover_mapa_por_codigos_erro(mapa, erros_insert_detalhe, 'cd_conta')
    return mapa, {
        'tabela': 'banco_conta',
        'lidos': len(contas_giv),
        'inseridos': inseridos,
        'existentes': 0,
        'erros': len(erros_detalhe),
        'erros_detalhe': erros_detalhe,
    }


def carregar_mapa_banco_conta_rotinas(
    cursor_giv,
    cursor_web,
    tabela_web_banco_conta,
    mapa_bancos,
    tenant_id,
    cd_empresa,
    cd_empresa_giv=None
):
    """Relaciona conta bancaria GIV -> banco_conta Web por banco/conta/agencia/descricao."""
    contas_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'banco_conta',
        'cd_empresa, cd_conta',
        cd_empresa_giv=cd_empresa_giv
    )
    cursor_web.execute(
        f"""
        SELECT cd_conta, cd_banco, nr_conta, nr_agencia, ds_conta
          FROM {tabela_web_banco_conta}
         WHERE tenant_id = %s
           AND cd_empresa = %s
         ORDER BY cd_conta
        """,
        (tenant_id, cd_empresa)
    )
    por_chave_completa = {}
    por_chave_conta = {}
    por_codigo = {}
    for cd_conta, cd_banco, nr_conta, nr_agencia, ds_conta in cursor_web.fetchall():
        chave_completa = (
            cd_banco,
            chave_texto(nr_conta),
            chave_texto(nr_agencia),
            chave_texto(ds_conta),
        )
        chave_conta = (cd_banco, chave_texto(nr_conta), chave_texto(nr_agencia))
        por_chave_completa.setdefault(chave_completa, cd_conta)
        por_chave_conta.setdefault(chave_conta, cd_conta)
        por_codigo.setdefault(cd_conta, cd_conta)

    mapa = {}
    for conta in contas_giv:
        cd_conta_giv = conta.get('cd_conta')
        cd_banco_web = (
            mapa_bancos.get(conta.get('cd_banco'))
            or mapa_bancos.get(normalizar_codigo_cidade(conta.get('cd_banco')))
        )
        cd_web = None
        if cd_banco_web is not None:
            chave_completa = (
                cd_banco_web,
                chave_texto(conta.get('nr_conta')),
                chave_texto(conta.get('nr_agencia')),
                chave_texto(conta.get('ds_conta')),
            )
            chave_conta = (
                cd_banco_web,
                chave_texto(conta.get('nr_conta')),
                chave_texto(conta.get('nr_agencia')),
            )
            cd_web = por_chave_completa.get(chave_completa) or por_chave_conta.get(chave_conta)
        if cd_web is None:
            cd_web = por_codigo.get(cd_conta_giv)
        if cd_web is not None:
            mapa[(conta.get('cd_empresa'), cd_conta_giv)] = cd_web
            mapa[cd_conta_giv] = cd_web
            mapa[normalizar_codigo_cidade(cd_conta_giv)] = cd_web

    print(f"[OK] Mapa banco_conta rotinas: {len(mapa)} chaves relacionadas.")
    return mapa


def status_cartao_administradora(registro_giv):
    return 'A' if valor_flag(registro_giv.get('id_ativo'), 'S') in ('S', 'A') else 'I'


def processar_cartao_administradoras(cursor_giv, cursor_web, tabela_web_cartao, tenant_id, cd_empresa):
    print()
    print("[...] Buscando administradoras de cartao do GIV...")
    administradoras_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'cartao_administradora',
        'cd_administradora'
    )
    print(f"[OK] {len(administradoras_giv)} administradoras encontradas no GIV.")

    sequence_cartao = buscar_sequence_coluna_web(cursor_web, tabela_web_cartao, 'cd_administradora')
    max_cartao = sincronizar_sequence_com_max(cursor_web, tabela_web_cartao, 'cd_administradora', sequence_cartao)
    print(f"[OK] Sequence de cartao_administradora: {sequence_cartao} (sincronizada com max={max_cartao}).")

    cursor_web.execute(
        f"""
        SELECT cd_administradora, ds_administradora, id_tipo_cartao
          FROM {tabela_web_cartao}
         WHERE tenant_id = %s
           AND cd_empresa = %s
         ORDER BY cd_administradora
        """,
        (tenant_id, cd_empresa)
    )
    existentes = {}
    for cd_administradora, ds_administradora, id_tipo_cartao in cursor_web.fetchall():
        chave = (chave_texto(ds_administradora), valor_flag(id_tipo_cartao, 'D'))
        existentes.setdefault(chave, cd_administradora)

    mapa = {}
    registros = []
    reaproveitados = 0
    pendentes = []
    for admin in administradoras_giv:
        tipo = valor_flag(admin.get('id_tipo_cartao'), 'D')
        chave = (chave_texto(admin.get('ds_administradora')), tipo)
        cd_web = existentes.get(chave)
        cd_origem = admin.get('cd_administradora')
        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web
            reaproveitados += 1
            continue
        pendentes.append(admin)

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_cartao, len(pendentes)))
    for admin in pendentes:
        cd_web = next(codigos)
        cd_origem = admin.get('cd_administradora')
        mapa[cd_origem] = cd_web
        mapa[normalizar_codigo_cidade(cd_origem)] = cd_web
        registros.append(limpar_registro({
            'cd_administradora': cd_web,
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
            'ds_administradora': limpar_valor(admin.get('ds_administradora')) or f"CARTAO {cd_origem}",
            'id_tipo_cartao': valor_flag(admin.get('id_tipo_cartao'), 'D'),
            'id_status': status_cartao_administradora(admin),
        }))

    if reaproveitados:
        print(f"[OK] {reaproveitados} administradoras reaproveitadas por descricao/tipo no mesmo tenant/empresa.")
    print(f"[OK] {len(registros)} administradoras novas para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_cartao, registros, 'cartao_administradora')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_cartao,
        registros,
        'cd_administradora',
        'sp_cartao_administradora'
    )
    mapa = remover_mapa_por_codigos_erro(mapa, erros_detalhe, 'cd_administradora')
    return mapa, {
        'tabela': 'cartao_administradora',
        'lidos': len(administradoras_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }


def carregar_mapa_cartao_administradora_rotinas(cursor_giv, cursor_web, tabela_web_cartao, tenant_id, cd_empresa):
    """Relaciona administradora de cartao GIV -> Web por descricao/tipo."""
    administradoras_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'cartao_administradora',
        'cd_administradora'
    )
    cursor_web.execute(
        f"""
        SELECT cd_administradora, ds_administradora, id_tipo_cartao
          FROM {tabela_web_cartao}
         WHERE tenant_id = %s
           AND cd_empresa = %s
         ORDER BY cd_administradora
        """,
        (tenant_id, cd_empresa)
    )
    por_chave = {}
    por_codigo = {}
    for cd_administradora, ds_administradora, id_tipo_cartao in cursor_web.fetchall():
        por_chave.setdefault((chave_texto(ds_administradora), valor_flag(id_tipo_cartao, 'D')), cd_administradora)
        por_codigo.setdefault(cd_administradora, cd_administradora)

    mapa = {}
    for admin in administradoras_giv:
        cd_origem = admin.get('cd_administradora')
        cd_web = por_chave.get((
            chave_texto(admin.get('ds_administradora')),
            valor_flag(admin.get('id_tipo_cartao'), 'D')
        ))
        if cd_web is None:
            cd_web = por_codigo.get(cd_origem)
        if cd_web is not None:
            mapa[cd_origem] = cd_web
            mapa[normalizar_codigo_cidade(cd_origem)] = cd_web

    print(f"[OK] Mapa cartao_administradora rotinas: {len(mapa)} administradoras relacionadas.")
    return mapa


def map_tp_condicao_pagamento(registro_giv):
    tipo = valor_flag(registro_giv.get('id_modulo_usado'), 'V')
    return tipo if tipo in ('C', 'V') else 'V'


def carregar_formas_condicao_giv(cursor_giv):
    return buscar_registros_giv(
        cursor_giv,
        """
        SELECT
            cd_condicao_pagto,
            id_forma_pagamento,
            id_tipo
          FROM condicao_pagto_forma
         ORDER BY cd_condicao_pagto, id_forma_pagamento
        """
    )


FORMAS_CONDICAO_GIV = {
    'DIN': {
        'descricao': 'DINHEIRO',
        'aliases': ('DINHEIRO',),
        'id_movto_caixa': 'S',
    },
    'CRE': {
        'descricao': 'CREDIARIO',
        'aliases': ('CREDIARIO', 'CREDIÁRIO'),
        'id_movto_caixa': 'S',
    },
    'CAC': {
        'descricao': 'CARTAO CREDITO',
        'aliases': ('CARTAO CREDITO', 'CARTAO DE CREDITO', 'CARTÃO CRÉDITO', 'CARTÃO DE CRÉDITO', 'CARTAO'),
        'id_movto_caixa': 'N',
    },
    'CAH': {
        'descricao': 'CARTAO CREDITO',
        'aliases': ('CARTAO CREDITO', 'CARTAO DE CREDITO', 'CARTÃO CRÉDITO', 'CARTÃO DE CRÉDITO', 'CARTAO'),
        'id_movto_caixa': 'N',
    },
    'CAD': {
        'descricao': 'CARTAO DEBITO',
        'aliases': ('CARTAO DEBITO', 'CARTÃO DÉBITO', 'CARTAO DE DEBITO', 'CARTÃO DE DÉBITO', 'DEBITO'),
        'id_movto_caixa': 'S',
    },
    'CAP': {
        'descricao': 'CARTAO PARCELADO',
        'aliases': ('CARTAO PARCELADO', 'CARTÃO PARCELADO', 'PARCELADO'),
        'id_movto_caixa': 'N',
    },
    'CHA': {
        'descricao': 'CHEQUE A VISTA',
        'aliases': ('CHEQUE A VISTA', 'CHEQUE À VISTA', 'CHEQUE AVISTA'),
        'id_movto_caixa': 'S',
    },
    'CHP': {
        'descricao': 'CHEQUE A PRAZO',
        'aliases': ('CHEQUE A PRAZO', 'CHEQUE PRAZO'),
        'id_movto_caixa': 'S',
    },
    'PIX': {
        'descricao': 'PIX',
        'aliases': ('PIX',),
        'id_movto_caixa': 'S',
    },
}


def codigo_forma_condicao_giv(registro_giv):
    return (limpar_valor(registro_giv.get('id_forma_pagamento')) or '').upper()


def info_forma_condicao_giv(codigo, id_tipo=None):
    codigo = (limpar_valor(codigo) or '').upper()
    id_tipo = (limpar_valor(id_tipo) or '').upper()
    info = dict(FORMAS_CONDICAO_GIV.get(codigo) or {})
    if not info:
        if codigo.startswith('CH'):
            info = dict(FORMAS_CONDICAO_GIV['CHP' if id_tipo == 'P' else 'CHA'])
        elif codigo.startswith('CA'):
            info = dict(FORMAS_CONDICAO_GIV['CAP' if id_tipo == 'P' else 'CAC'])
        elif codigo.startswith('CR'):
            info = dict(FORMAS_CONDICAO_GIV['CRE'])
        elif codigo.startswith('DI'):
            info = dict(FORMAS_CONDICAO_GIV['DIN'])
        else:
            info = {
                'descricao': f"FORMA {codigo or 'NAO INFORMADA'}",
                'aliases': (),
                'id_movto_caixa': 'N',
            }
    aliases = list(info.get('aliases') or ())
    descricao = limpar_valor(info.get('descricao')) or f"FORMA {codigo or 'NAO INFORMADA'}"
    aliases.extend([descricao, codigo])
    info['codigo'] = codigo
    info['descricao'] = descricao
    info['aliases'] = tuple(dict.fromkeys(alias for alias in aliases if limpar_valor(alias)))
    info['id_movto_caixa'] = valor_flag(info.get('id_movto_caixa'), 'N')
    return info


def carregar_formas_pagamento_web(cursor_web, tabela_web_forma_pagamento, tenant_id):
    cursor_web.execute(
        f"""
        SELECT cd_forma_pagto, ds_forma_pagto, id_movto_caixa, id_status
          FROM {tabela_web_forma_pagamento}
         WHERE tenant_id = %s
         ORDER BY
            CASE WHEN id_status = 'A' THEN 0 ELSE 1 END,
            cd_forma_pagto
        """,
        (tenant_id,)
    )
    return [
        {
            'cd_forma_pagto': row[0],
            'ds_forma_pagto': row[1],
            'id_movto_caixa': row[2],
            'id_status': row[3],
        }
        for row in cursor_web.fetchall()
    ]


def encontrar_forma_pagamento_web(formas_web, aliases, somente_ativa=True):
    chaves = {chave_nome_produto(alias) for alias in aliases if chave_nome_produto(alias)}
    if not chaves:
        return None
    for forma in formas_web:
        if somente_ativa and valor_flag(forma.get('id_status'), 'I') != 'A':
            continue
        chave = chave_nome_produto(forma.get('ds_forma_pagto'))
        if chave in chaves:
            return forma
    return None


def garantir_formas_condicao_web(cursor_web, tabela_web_forma_pagamento, formas_giv, tenant_id):
    formas_unicas = {}
    for forma in formas_giv:
        codigo = codigo_forma_condicao_giv(forma)
        if not codigo:
            continue
        formas_unicas.setdefault(codigo, forma.get('id_tipo'))

    formas_web = carregar_formas_pagamento_web(cursor_web, tabela_web_forma_pagamento, tenant_id)
    mapa = {}
    registros_novos = []
    erros_detalhe = []
    reaproveitadas = 0
    reativadas = 0

    sequence_forma = buscar_sequence_coluna_web(cursor_web, tabela_web_forma_pagamento, 'cd_forma_pagto')
    max_forma = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_forma_pagamento,
        'cd_forma_pagto',
        sequence_forma
    )
    print(f"[OK] Sequence de forma_pagamento: {sequence_forma} (sincronizada com max={max_forma}).")

    codigos_para_criar = []
    for codigo, id_tipo in sorted(formas_unicas.items()):
        info = info_forma_condicao_giv(codigo, id_tipo)
        forma_ativa = encontrar_forma_pagamento_web(formas_web, info['aliases'], somente_ativa=True)
        if forma_ativa:
            mapa[codigo] = forma_ativa['cd_forma_pagto']
            reaproveitadas += 1
            print(
                f"[OK] Forma pagamento {codigo}: reaproveitada no Web "
                f"cd_forma_pagto={forma_ativa['cd_forma_pagto']} ({forma_ativa['ds_forma_pagto']})."
            )
            continue

        forma_inativa = encontrar_forma_pagamento_web(formas_web, info['aliases'], somente_ativa=False)
        if forma_inativa:
            cursor_web.execute(
                f"""
                UPDATE {tabela_web_forma_pagamento}
                   SET id_status = 'A'
                 WHERE cd_forma_pagto = %s
                   AND tenant_id = %s
                """,
                (forma_inativa['cd_forma_pagto'], tenant_id)
            )
            forma_inativa['id_status'] = 'A'
            mapa[codigo] = forma_inativa['cd_forma_pagto']
            reativadas += 1
            print(
                f"[OK] Forma pagamento {codigo}: encontrada inativa e reativada no Web "
                f"cd_forma_pagto={forma_inativa['cd_forma_pagto']} ({forma_inativa['ds_forma_pagto']})."
            )
            continue

        codigos_para_criar.append((codigo, info))

    codigos_reservados = iter(reservar_valores_sequence(cursor_web, sequence_forma, len(codigos_para_criar)))
    for codigo, info in codigos_para_criar:
        cd_forma = next(codigos_reservados)
        mapa[codigo] = cd_forma
        registros_novos.append(limpar_registro({
            'cd_forma_pagto': cd_forma,
            'ds_forma_pagto': info['descricao'],
            'id_movto_caixa': info['id_movto_caixa'],
            'id_status': 'A',
            'tenant_id': tenant_id,
        }))
        print(
            f"[OK] Forma pagamento {codigo}: nao existia ativa no Web; "
            f"sera cadastrada como cd_forma_pagto={cd_forma} ({info['descricao']})."
        )

    aplicar_limites_texto_web(cursor_web, tabela_web_forma_pagamento, registros_novos, 'forma_pagamento')
    inseridos, erros, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_forma_pagamento,
        registros_novos,
        'cd_forma_pagto',
        'sp_forma_pagamento_condicao'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    codigos_erro = codigos_insert_com_erro(erros_insert_detalhe, 'cd_forma_pagto')
    if codigos_erro:
        mapa = {
            codigo: cd_forma
            for codigo, cd_forma in mapa.items()
            if cd_forma not in codigos_erro
        }

    print(
        f"[OK] Formas de pagamento para condicoes: {reaproveitadas} reaproveitadas, "
        f"{reativadas} reativadas, {inseridos} cadastradas, {erros} erros."
    )
    return mapa, {
        'tabela': 'forma_pagamento',
        'lidos': len(formas_unicas),
        'inseridos': inseridos,
        'existentes': reaproveitadas + reativadas,
        'erros': len(erros_detalhe),
        'erros_detalhe': erros_detalhe,
        'motivo_existentes': 'forma de pagamento ja existia ativa ou foi reativada no Web',
    }


def carregar_mapa_forma_condicao_por_codigo(cursor_web, tabela_web_forma_pagamento, tenant_id):
    cursor_web.execute(
        f"""
        SELECT cd_forma_pagto, ds_forma_pagto, id_status
          FROM {tabela_web_forma_pagamento}
         WHERE tenant_id = %s
         ORDER BY
            CASE WHEN id_status = 'A' THEN 0 ELSE 1 END,
            cd_forma_pagto
        """,
        (tenant_id,)
    )
    por_nome = {}
    for cd_forma, ds_forma, _status in cursor_web.fetchall():
        chave = chave_nome_produto(ds_forma)
        if chave and chave not in por_nome:
            por_nome[chave] = cd_forma

    def buscar_forma(*apelidos):
        chaves_apelido = [chave_nome_produto(apelido) for apelido in apelidos]
        for chave_apelido in chaves_apelido:
            for nome, cd_forma in por_nome.items():
                if chave_apelido and nome == chave_apelido:
                    return cd_forma
        for chave_apelido in chaves_apelido:
            for nome, cd_forma in por_nome.items():
                if chave_apelido and chave_apelido in nome:
                    return cd_forma
        return None

    return {
        'DIN': buscar_forma('DINHEIRO'),
        'CRE': buscar_forma('CREDIARIO'),
        'CAC': buscar_forma('CARTAO CREDITO', 'CARTAO DE CREDITO', 'CARTAO'),
        'CAD': buscar_forma('CARTAO DEBITO', 'CARTAO'),
        'CAP': buscar_forma('CARTAO PARCELADO', 'PARCELADO'),
        'CHA': buscar_forma('CHEQUE A VISTA', 'CHEQUE'),
        'CHP': buscar_forma('CHEQUE A PRAZO', 'CHEQUE'),
    }


def carregar_vinculos_condicao_forma_web(cursor_web, tabela_web_condicao_forma, tenant_id):
    cursor_web.execute(
        f"""
        SELECT cd_condicao_pagto, cd_forma_pagto
          FROM {tabela_web_condicao_forma}
         WHERE tenant_id = %s
        """,
        (tenant_id,)
    )
    return {(row[0], row[1]) for row in cursor_web.fetchall()}


def processar_condicoes_pagamento(
    cursor_giv,
    cursor_web,
    tabela_web_condicao,
    tabela_web_condicao_forma,
    tabela_web_forma_pagamento,
    tenant_id,
    cd_empresa_giv=None
):
    print()
    print("[...] Buscando condicoes de pagamento do GIV...")
    condicoes_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'condicao_pagto',
        'cd_condicao_pagto',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(condicoes_giv)} condicoes encontradas no GIV.")

    codigos_condicoes_giv = {condicao.get('cd_condicao_pagto') for condicao in condicoes_giv}
    formas_giv = [
        forma for forma in carregar_formas_condicao_giv(cursor_giv)
        if forma.get('cd_condicao_pagto') in codigos_condicoes_giv
    ]
    print(f"[OK] {len(formas_giv)} vinculos forma x condicao encontrados no GIV.")
    mapa_formas_codigo, resumo_formas_pagamento = garantir_formas_condicao_web(
        cursor_web,
        tabela_web_forma_pagamento,
        formas_giv,
        tenant_id
    )

    sequence_condicao = buscar_sequence_coluna_web(cursor_web, tabela_web_condicao, 'cd_condicao_pagto')
    max_condicao = sincronizar_sequence_com_max(
        cursor_web,
        tabela_web_condicao,
        'cd_condicao_pagto',
        sequence_condicao
    )
    print(f"[OK] Sequence de condicao_pagamento: {sequence_condicao} (sincronizada com max={max_condicao}).")

    registros = []
    mapa, reaproveitados, pendentes_por_chave, condicoes_para_inserir = planejar_cadastro_por_descricao(
        cursor_web,
        tabela_web_condicao,
        'cd_condicao_pagto',
        'ds_condicao_pagto',
        condicoes_giv,
        'cd_condicao_pagto',
        lambda reg: limpar_valor(reg.get('ds_condicao_pagto')) or f"CONDICAO {reg.get('cd_condicao_pagto')}",
        tenant_id=tenant_id
    )

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_condicao, len(condicoes_para_inserir)))
    for chave_nome in condicoes_para_inserir:
        pendente = pendentes_por_chave[chave_nome]
        reg_base = pendente['regs'][0]
        cd_web = next(codigos)
        for reg in pendente['regs']:
            mapa[reg.get('cd_condicao_pagto')] = cd_web
            mapa[normalizar_codigo_cidade(reg.get('cd_condicao_pagto'))] = cd_web
        registros.append(limpar_registro({
            'cd_condicao_pagto': cd_web,
            'ds_condicao_pagto': pendente['descricao'],
            'nr_parcelas': reg_base.get('nr_parcelas') or 1,
            'id_entrada': valor_flag(reg_base.get('id_entrada'), 'N'),
            'id_status': valor_flag(reg_base.get('id_situacao'), 'A'),
            'id_bloquear_crediario': valor_flag(reg_base.get('id_bloqueio_crediario'), 'N'),
            'id_bloquear_entrega': valor_flag(reg_base.get('id_bloqueio_entrega'), 'N'),
            'id_imprime_carne': valor_flag(reg_base.get('id_imprime_carne'), 'N'),
            'pr_comissao': valor_decimal_ou_zero(reg_base.get('pr_comissao')),
            'pr_desconto': valor_decimal_ou_zero(reg_base.get('pr_desconto')),
            'tp_condicao': map_tp_condicao_pagamento(reg_base),
            'tx_juros': valor_decimal_ou_zero(reg_base.get('pr_acrescimo')),
            'tenant_id': tenant_id,
        }))

    if reaproveitados:
        print(f"[OK] {reaproveitados} condicoes reaproveitadas por descricao no mesmo tenant_id.")
    print(f"[OK] {len(registros)} condicoes novas para inserir.")
    aplicar_limites_texto_web(cursor_web, tabela_web_condicao, registros, 'condicao_pagamento')
    inseridos, erros, erros_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_condicao,
        registros,
        'cd_condicao_pagto',
        'sp_condicao_pagamento'
    )
    mapa = remover_mapa_por_codigos_erro(mapa, erros_detalhe, 'cd_condicao_pagto')

    sincronizadas = 0
    for condicao in condicoes_giv:
        cd_condicao_web = mapa.get(condicao.get('cd_condicao_pagto'))
        if cd_condicao_web is None:
            continue
        cursor_web.execute(
            f"""
            UPDATE {tabela_web_condicao}
               SET ds_condicao_pagto = %s,
                   nr_parcelas = %s,
                   id_entrada = %s,
                   id_status = %s,
                   id_bloquear_crediario = %s,
                   id_bloquear_entrega = %s,
                   id_imprime_carne = %s,
                   pr_comissao = %s,
                   pr_desconto = %s,
                   tp_condicao = %s,
                   tx_juros = %s
             WHERE cd_condicao_pagto = %s
               AND tenant_id = %s
            """,
            (
                limpar_valor(condicao.get('ds_condicao_pagto')) or f"CONDICAO {condicao.get('cd_condicao_pagto')}",
                condicao.get('nr_parcelas') or 1,
                valor_flag(condicao.get('id_entrada'), 'N'),
                valor_flag(condicao.get('id_situacao'), 'A'),
                valor_flag(condicao.get('id_bloqueio_crediario'), 'N'),
                valor_flag(condicao.get('id_bloqueio_entrega'), 'N'),
                valor_flag(condicao.get('id_imprime_carne'), 'N'),
                valor_decimal_ou_zero(condicao.get('pr_comissao')),
                valor_decimal_ou_zero(condicao.get('pr_desconto')),
                map_tp_condicao_pagamento(condicao),
                valor_decimal_ou_zero(condicao.get('pr_acrescimo')),
                cd_condicao_web,
                tenant_id,
            )
        )
        sincronizadas += 1
    if sincronizadas:
        print(f"[OK] {sincronizadas} condicoes sincronizadas com os dados do GIV.")

    condicoes_web = sorted({
        cd_condicao_web
        for cd_condicao_web in mapa.values()
        if isinstance(cd_condicao_web, int)
    })
    if condicoes_web:
        placeholders = ', '.join(['%s'] * len(condicoes_web))
        cursor_web.execute(
            f"""
            DELETE FROM {tabela_web_condicao_forma}
             WHERE tenant_id = %s
               AND cd_condicao_pagto IN ({placeholders})
            """,
            [tenant_id] + condicoes_web
        )
        apagados = cursor_web.rowcount if cursor_web.rowcount is not None else 0
        print(f"[OK] {apagados} vinculos antigos de formas removidos para sincronizar pelo GIV.")
    vinculos_existentes = carregar_vinculos_condicao_forma_web(
        cursor_web,
        tabela_web_condicao_forma,
        tenant_id
    )
    vinculos = []
    erros_formas = []
    existentes_formas = 0
    logs_vinculos = 0
    for forma in formas_giv:
        cd_condicao_web = mapa.get(forma.get('cd_condicao_pagto'))
        codigo_forma = codigo_forma_condicao_giv(forma)
        cd_forma_web = mapa_formas_codigo.get(codigo_forma)
        if cd_condicao_web is None:
            registrar_erro_validacao(
                erros_formas,
                'condicao_pagamento_forma',
                f"condicao_giv={forma.get('cd_condicao_pagto')} forma={codigo_forma}",
                'condicao sem de/para web'
            )
            continue
        if cd_forma_web is None:
            registrar_erro_validacao(
                erros_formas,
                'condicao_pagamento_forma',
                f"condicao_giv={forma.get('cd_condicao_pagto')} forma={codigo_forma}",
                'forma de pagamento nao foi encontrada nem cadastrada no Web'
            )
            continue
        chave_vinculo = (cd_condicao_web, cd_forma_web)
        if chave_vinculo in vinculos_existentes:
            existentes_formas += 1
            if existentes_formas <= 10:
                print(
                    f"[INFO] Vinculo forma x condicao ja existia/duplicado: "
                    f"condicao_giv={forma.get('cd_condicao_pagto')} forma={codigo_forma} "
                    f"-> condicao_web={cd_condicao_web} forma_web={cd_forma_web}."
                )
            elif existentes_formas == 11:
                print("  ... suprimindo demais vinculos forma x condicao ja existentes/duplicados")
            continue
        vinculos_existentes.add(chave_vinculo)
        vinculos.append({
            'cd_condicao_pagto': cd_condicao_web,
            'cd_forma_pagto': cd_forma_web,
            'tenant_id': tenant_id,
        })
        logs_vinculos += 1
        if logs_vinculos <= 20:
            print(
                f"[OK] Vinculo criado: condicao_giv={forma.get('cd_condicao_pagto')} "
                f"forma={codigo_forma} -> condicao_web={cd_condicao_web} forma_web={cd_forma_web}."
            )
        elif logs_vinculos == 21:
            print("  ... suprimindo demais logs de vinculos forma x condicao criados")

    inseridos_formas, erros_insert_formas, erros_insert_formas_detalhe = inserir_registros_web(
        cursor_web,
        tabela_web_condicao_forma,
        vinculos,
        'cd_condicao_pagto',
        'sp_condicao_forma'
    )
    erros_formas.extend(erros_insert_formas_detalhe)
    return mapa, [resumo_formas_pagamento, {
        'tabela': 'condicao_pagamento',
        'lidos': len(condicoes_giv),
        'inseridos': inseridos,
        'existentes': reaproveitados,
        'erros': erros,
        'erros_detalhe': erros_detalhe,
    }, {
        'tabela': 'condicao_pagamento_forma',
        'lidos': len(formas_giv),
        'inseridos': inseridos_formas,
        'existentes': existentes_formas,
        'erros': len(erros_formas),
        'erros_detalhe': erros_formas,
        'motivo_existentes': 'vinculo forma x condicao ja existia no Web',
    }]


def processar_pedido_compra_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando pedido_compra e pedido_compra_item...")
    pedidos_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'pedido_compra',
        'cd_empresa, nr_pedido',
        cd_empresa_giv=cd_empresa_giv
    )
    where_itens = ""
    params_itens = []
    if cd_empresa_giv is not None:
        where_itens = "WHERE i.cd_empresa = ?"
        params_itens.append(cd_empresa_giv)
    itens_giv = buscar_registros_giv(
        cursor_giv,
        f"""
        SELECT i.*, p.cd_cor AS produto_cd_cor, p.cd_tamanho AS produto_cd_tamanho
          FROM pedido_compra_item i
          LEFT JOIN produto p ON p.cd_produto = i.cd_produto
         {where_itens}
         ORDER BY i.cd_empresa, i.nr_pedido, i.cd_produto, i.cd_unidade, i.vl_unitario
        """,
        params_itens if params_itens else None
    )
    print(f"[OK] {len(pedidos_giv)} pedidos e {len(itens_giv)} itens encontrados no GIV.")

    usados = buscar_set_coluna_web(cursor_web, tabelas_web['pedido_compra'], 'nr_pedido', tenant_id=tenant_id)
    max_pedido = max([0] + [int(v or 0) for v in usados])
    registros = []
    mapa_pedido = {}
    erros_detalhe = []
    renumerados = 0
    for pedido in pedidos_giv:
        chave_origem = (pedido.get('cd_empresa'), pedido.get('nr_pedido'))
        motivos = []
        cd_fornecedor = mapas['fornecedor'].get(pedido.get('cd_fornecedor'))
        cd_condicao = mapas['condicao_pagamento'].get(pedido.get('cd_condicao_pagto'))
        cd_usuario = mapas['usuario'].get(pedido.get('cd_usuario')) or mapas.get('usuario_padrao')
        if cd_fornecedor is None:
            motivos.append(f"fornecedor GIV {pedido.get('cd_fornecedor')} sem de/para")
        if cd_condicao is None:
            motivos.append(f"condicao GIV {pedido.get('cd_condicao_pagto')} sem de/para")
        if cd_usuario is None:
            motivos.append(f"usuario GIV {pedido.get('cd_usuario')} sem de/para/padrao")
        if motivos:
            registrar_erro_validacao(erros_detalhe, 'pedido_compra', f"nr_pedido={pedido.get('nr_pedido')}", '; '.join(motivos))
            continue

        nr_origem = int(pedido.get('nr_pedido') or 0)
        if nr_origem > max_pedido and nr_origem not in usados:
            nr_web = nr_origem
            usados.add(nr_web)
        else:
            nr_web = proximo_numero_disponivel(usados, max_pedido)
            max_pedido = max(max_pedido, nr_web)
            if nr_web != nr_origem:
                renumerados += 1
        mapa_pedido[chave_origem] = nr_web

        cd_transportador = mapas['fornecedor'].get(pedido.get('cd_transportador')) if pedido.get('cd_transportador') else None
        registros.append(limpar_registro({
            'nr_pedido': nr_web,
            'tenant_id': tenant_id,
            'id_status': map_status_pedido_compra(pedido.get('id_situacao')),
            'cd_empresa': cd_empresa,
            'cd_fornecedor': cd_fornecedor,
            'cd_transportador': cd_transportador,
            'cd_condicao_pagto': cd_condicao,
            'cd_usuario': cd_usuario,
            'nm_representante': None,
            'dt_digitacao': valor_data_ou_agora(pedido.get('dt_digitacao')),
            'dt_emissao': valor_data_ou_agora(pedido.get('dt_emissao')),
            'dt_previsao_entrega': valor_data_ou_agora(pedido.get('dt_previsao_entrega')),
            'pr_desconto1': valor_decimal_ou_zero(pedido.get('pr_desconto1')),
            'pr_desconto2': valor_decimal_ou_zero(pedido.get('pr_desconto2')),
            'pr_desconto3': valor_decimal_ou_zero(pedido.get('pr_desconto3')),
            'pr_desconto4': valor_decimal_ou_zero(pedido.get('pr_desconto4')),
            'pr_desconto5': valor_decimal_ou_zero(pedido.get('pr_desconto5')),
            'pr_desconto6': valor_decimal_ou_zero(pedido.get('pr_desconto6')),
            'tp_frete': str(pedido.get('tp_frete') if pedido.get('tp_frete') is not None else 'N')[:1],
            'vl_frete': valor_decimal_ou_zero(pedido.get('vl_frete')),
            'vl_total_produto': valor_decimal_ou_zero(pedido.get('vl_produto')),
            'vl_total_pedido': valor_decimal_ou_zero(pedido.get('vl_pedido')),
            'vl_base_icms': valor_decimal_ou_zero(pedido.get('vl_base_icms')),
            'vl_icms': valor_decimal_ou_zero(pedido.get('vl_icms')),
            'vl_ipi': valor_decimal_ou_zero(pedido.get('vl_ipi')),
            'observacao': pedido.get('observacao'),
            'observacao2': pedido.get('observacao_2'),
            'cd_usuario_cancelamento': mapas['usuario'].get(pedido.get('cd_usuario_cancela')),
            'dt_cancelamento': pedido.get('dt_cancela'),
            'motivo_cancelamento': pedido.get('motivo_cancelamento'),
            'id_autorizado': valor_flag(pedido.get('id_autorizado'), 'N'),
            'cd_usuario_autorizado': mapas['usuario'].get(pedido.get('cd_autorizacao')),
            'dt_autorizado': pedido.get('dt_autorizacao'),
        }))

    if renumerados:
        print(f"[INFO] Pedido_compra: {renumerados} pedidos receberam novo nr_pedido para evitar colisao/reversao.")
    aplicar_limites_texto_web(cursor_web, tabelas_web['pedido_compra'], registros, 'pedido_compra')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['pedido_compra'],
        registros,
        'nr_pedido',
        'sp_pedido_compra'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_pedido = remover_mapa_por_codigos_erro(mapa_pedido, erros_insert_detalhe, 'nr_pedido')

    itens = []
    ordinal_por_pedido = {}
    erros_itens = []
    status_por_pedido = {reg['nr_pedido']: reg['id_status'] for reg in registros}
    for item in itens_giv:
        chave = (item.get('cd_empresa'), item.get('nr_pedido'))
        nr_pedido_web = mapa_pedido.get(chave)
        if nr_pedido_web is None:
            continue
        motivos = []
        cd_produto = mapas['produto'].get(item.get('cd_produto'))
        cd_unidade = mapas['unidade'].get(item.get('cd_unidade'))
        if cd_produto is None:
            motivos.append(f"produto GIV {item.get('cd_produto')} sem de/para")
        if cd_unidade is None:
            motivos.append(f"unidade GIV {item.get('cd_unidade')} sem de/para")
        if motivos:
            registrar_erro_validacao(erros_itens, 'pedido_compra_item', f"nr_pedido={item.get('nr_pedido')} produto={item.get('cd_produto')}", '; '.join(motivos))
            continue
        ordinal_por_pedido[nr_pedido_web] = ordinal_por_pedido.get(nr_pedido_web, 0) + 1
        itens.append(limpar_registro({
            'nr_item': ordinal_por_pedido[nr_pedido_web],
            'nr_pedido': nr_pedido_web,
            'tenant_id': tenant_id,
            'id_status': 'C' if status_por_pedido.get(nr_pedido_web) == 'C' else 'A',
            'id_atualizar_custo': valor_flag(item.get('id_atualiza_custo'), 'S'),
            'cd_produto': cd_produto,
            'cd_unidade': cd_unidade,
            'qt_produto': valor_decimal_ou_zero(item.get('qt_pedida')),
            'qt_entregue': valor_decimal_ou_zero(item.get('qt_atendida')),
            'qt_cancelamento': valor_decimal_ou_zero(item.get('qt_suspenso')),
            'vl_unitario': valor_decimal_ou_zero(item.get('vl_unitario')),
            'vl_frete': valor_decimal_ou_zero(item.get('vl_frete')),
            'pr_desconto1': valor_decimal_ou_zero(item.get('pr_desconto1')),
            'pr_desconto2': valor_decimal_ou_zero(item.get('pr_desconto2')),
            'pr_desconto3': valor_decimal_ou_zero(item.get('pr_desconto3')),
            'pr_desconto4': valor_decimal_ou_zero(item.get('pr_desconto4')),
            'pr_desconto5': valor_decimal_ou_zero(item.get('pr_desconto5')),
            'pr_desconto6': valor_decimal_ou_zero(item.get('pr_desconto6')),
            'pr_icms': valor_decimal_ou_zero(item.get('pr_icms')),
            'pr_ipi': valor_decimal_ou_zero(item.get('pr_ipi')),
            'cd_cor': mapas['cor'].get(item.get('produto_cd_cor')),
            'cd_tamanho': mapas['tamanho'].get(item.get('produto_cd_tamanho')),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['pedido_compra_item'], itens, 'pedido_compra_item')
    inseridos_itens, erros_insert_itens, erros_insert_itens_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['pedido_compra_item'],
        itens,
        'nr_item',
        'sp_pedido_compra_item'
    )
    erros_itens.extend(erros_insert_itens_detalhe)

    return mapa_pedido, [
        {
            'tabela': 'pedido_compra',
            'lidos': len(pedidos_giv),
            'inseridos': inseridos,
            'existentes': 0,
            'erros': len(erros_detalhe),
            'erros_detalhe': erros_detalhe,
            'motivo_existentes': 'numero reaproveitado/renumerado por de-para em memoria',
        },
        {
            'tabela': 'pedido_compra_item',
            'lidos': len(itens_giv),
            'inseridos': inseridos_itens,
            'existentes': 0,
            'erros': len(erros_itens),
            'erros_detalhe': erros_itens,
        },
    ]


def processar_nota_fiscal_entrada_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando nota_fiscal_entrada e itens...")
    notas_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'nota_fiscal_entrada',
        'cd_empresa, nr_nota, serie, cd_fornecedor',
        cd_empresa_giv=cd_empresa_giv
    )
    itens_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'nota_fiscal_entrada_item',
        'cd_empresa, nr_nota, serie, cd_fornecedor, nr_item',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(notas_giv)} notas de entrada e {len(itens_giv)} itens encontrados no GIV.")

    sequence_nf = buscar_sequence_coluna_web(cursor_web, tabelas_web['nota_fiscal_entrada'], 'nf_id')
    max_nf = sincronizar_sequence_com_max(cursor_web, tabelas_web['nota_fiscal_entrada'], 'nf_id', sequence_nf)
    print(f"[OK] Sequence nota_fiscal_entrada: {sequence_nf} (sincronizada com max={max_nf}).")

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_nf, len(notas_giv)))
    registros = []
    mapa_nf = {}
    erros_detalhe = []
    for nota in notas_giv:
        nf_id = next(codigos)
        chave = (nota.get('cd_empresa'), nota.get('nr_nota'), serie_doc(nota.get('serie')), nota.get('cd_fornecedor'))
        motivos = []
        cd_fornecedor = mapas['fornecedor'].get(nota.get('cd_fornecedor'))
        cd_condicao = mapas['condicao_pagamento'].get(nota.get('cd_condicao_pagto'))
        cd_usuario = mapas['usuario'].get(nota.get('cd_usuario')) or mapas.get('usuario_padrao')
        if cd_fornecedor is None:
            motivos.append(f"fornecedor GIV {nota.get('cd_fornecedor')} sem de/para")
        if cd_condicao is None:
            motivos.append(f"condicao GIV {nota.get('cd_condicao_pagto')} sem de/para")
        if cd_usuario is None:
            motivos.append(f"usuario GIV {nota.get('cd_usuario')} sem de/para/padrao")
        if motivos:
            registrar_erro_validacao(erros_detalhe, 'nota_fiscal_entrada', f"nr_nota={nota.get('nr_nota')} serie={nota.get('serie')}", '; '.join(motivos))
            continue
        mapa_nf[chave] = nf_id
        registros.append(limpar_registro({
            'nf_id': nf_id,
            'tenant_id': tenant_id,
            'cd_empresa': cd_empresa,
            'nr_nota': nota.get('nr_nota'),
            'serie': serie_doc(nota.get('serie')),
            'id_pessoa': valor_flag(nota.get('id_pessoa'), 'J'),
            'cd_fornecedor': cd_fornecedor,
            'cd_transportador': mapas['fornecedor'].get(nota.get('cd_transportador')) if nota.get('cd_transportador') else None,
            'cd_natureza': nota.get('cd_natureza'),
            'cd_condicao_pagto': cd_condicao,
            'cd_usuario': cd_usuario,
            'dt_digitacao': valor_data_ou_agora(nota.get('dt_digitacao')),
            'dt_entrada': valor_data_ou_agora(nota.get('dt_entrada')),
            'obs': nota.get('obs'),
            'cd_tipo_documento': nota.get('cd_tipo_documento') or 1,
            'nr_chave_acesso': nota.get('nr_chave_acesso'),
            'id_soma_frete_total_nota': valor_flag(nota.get('id_soma_frete_total_nota'), 'N'),
            'tp_frete': 'N',
            'vl_frete': valor_decimal_ou_zero(nota.get('vl_frete')),
            'vl_desconto': valor_decimal_ou_zero(nota.get('vl_desconto')),
            'vl_outras_despesas': valor_decimal_campo_giv(
                nota,
                'vl_outras_despesas',
                'vl_outra_despesa'
            ),
            'vl_total_produto': valor_decimal_ou_zero(nota.get('vl_produto')),
            'vl_total_nota': valor_decimal_ou_zero(nota.get('vl_total_nota')),
            'vl_base_icms': valor_decimal_ou_zero(nota.get('vl_base_icms')),
            'vl_icms': valor_decimal_ou_zero(nota.get('vl_icms')),
            'vl_ipi': valor_decimal_ou_zero(nota.get('vl_ipi')),
            'id_conferido': valor_flag(nota.get('id_conferido'), 'N'),
            'cd_usuario_conferido': mapas['usuario'].get(nota.get('cd_usuario_conferencia')),
            'dt_conferido': nota.get('dt_conferencia'),
            'dt_emissao': valor_data_ou_agora(nota.get('dt_emissao')),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['nota_fiscal_entrada'], registros, 'nota_fiscal_entrada')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['nota_fiscal_entrada'],
        registros,
        'nf_id',
        'sp_nf_entrada'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_nf = remover_mapa_por_codigos_erro(mapa_nf, erros_insert_detalhe, 'nf_id')

    itens = []
    erros_itens = []
    # A PK do Web e (tenant_id, nf_id, nr_item), mas o GIV pode repetir
    # nr_item dentro da mesma nota. O Web usa a posicao valida do item e
    # preserva o numero original em nr_item_xml_origem.
    ordinal = {}
    for item in itens_giv:
        chave = (item.get('cd_empresa'), item.get('nr_nota'), serie_doc(item.get('serie')), item.get('cd_fornecedor'))
        nf_id = mapa_nf.get(chave)
        if nf_id is None:
            continue
        motivos = []
        cd_produto = mapas['produto'].get(item.get('cd_produto'))
        cd_unidade = mapas['unidade'].get(item.get('cd_unidade'))
        if cd_produto is None:
            motivos.append(f"produto GIV {item.get('cd_produto')} sem de/para")
        if cd_unidade is None:
            motivos.append(f"unidade GIV {item.get('cd_unidade')} sem de/para")
        if motivos:
            registrar_erro_validacao(erros_itens, 'nota_fiscal_entrada_item', f"nf={item.get('nr_nota')} item={item.get('nr_item')}", '; '.join(motivos))
            continue
        ordinal[nf_id] = ordinal.get(nf_id, 0) + 1
        itens.append(limpar_registro({
            'nr_item': ordinal[nf_id],
            'nr_item_xml_origem': item.get('nr_item'),
            'nf_id': nf_id,
            'tenant_id': tenant_id,
            'cd_produto': cd_produto,
            'id_movimentar_produto_estoque': valor_flag(item.get('id_movimenta_estoque'), 'S'),
            'id_atualizar_custo': valor_flag(item.get('id_atualiza_custo'), 'S'),
            'cd_produto_barra_ean': somente_digitos(item.get('cd_produto_barra_ean')),
            'cd_produto_barra_ean_trib': somente_digitos(item.get('cd_produto_barra_ean_trib')),
            'cd_natureza': item.get('cd_natureza'),
            'cd_situacao_tributaria': limpar_valor(item.get('cd_situacao_tributaria')) or CD_SITUACAO_TRIBUTARIA_PRODUTO_PADRAO,
            'cd_situacao_pis': limpar_valor(item.get('cd_situacao_pis')) or '99',
            'cd_situacao_cofins': limpar_valor(item.get('cd_situacao_cofins')) or '99',
            'cd_situacao_ipi': limpar_valor(item.get('cd_situacao_ipi')) or '99',
            'cd_unidade': cd_unidade,
            'fator_estoque': item.get('fator_unidade') or Decimal('1'),
            'qt_produto': valor_decimal_ou_zero(item.get('qt_produto')),
            'vl_unitario': valor_decimal_ou_zero(item.get('vl_unitario')),
            'vl_custo_unitario': valor_decimal_ou_zero(item.get('vl_custo_unitario') or item.get('vl_custo') or item.get('vl_unitario')),
            'vl_desconto': valor_decimal_ou_zero(item.get('vl_desconto')),
            'vl_frete': valor_decimal_ou_zero(item.get('vl_frete')),
            'vl_outras_despesas': valor_decimal_campo_giv(
                item,
                'vl_outras_despesas',
                'vl_outro'
            ),
            'vl_base_icms': valor_decimal_ou_zero(item.get('vl_base_icms')),
            'pr_icms': valor_decimal_ou_zero(item.get('pr_icms')),
            'pr_reducao_icms': valor_decimal_ou_zero(item.get('pr_reducao_icms')),
            'vl_icms': valor_decimal_ou_zero(item.get('vl_icms')),
            'vl_base_icms_simples_nacional': valor_decimal_ou_zero(item.get('vl_base_icms_simples_nacional')),
            'pr_icms_simples_nacional': valor_decimal_ou_zero(item.get('pr_icms_simples_nacional')),
            'vl_icms_simples_nacional': valor_decimal_ou_zero(item.get('vl_icms_simples_nacional')),
            'pr_ipi': valor_decimal_ou_zero(item.get('pr_ipi')),
            'vl_ipi': valor_decimal_ou_zero(item.get('vl_ipi')),
            'vl_base_pis_cofins': valor_decimal_ou_zero(item.get('vl_base_pis_cofins')),
            'pr_pis': valor_decimal_ou_zero(item.get('pr_pis')),
            'pr_cofins': valor_decimal_ou_zero(item.get('pr_cofins')),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['nota_fiscal_entrada_item'], itens, 'nota_fiscal_entrada_item')
    inseridos_itens, erros_insert_itens, erros_insert_itens_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['nota_fiscal_entrada_item'],
        itens,
        'nr_item',
        'sp_nf_entrada_item'
    )
    erros_itens.extend(erros_insert_itens_detalhe)

    return mapa_nf, [
        {
            'tabela': 'nota_fiscal_entrada',
            'lidos': len(notas_giv),
            'inseridos': inseridos,
            'existentes': 0,
            'erros': len(erros_detalhe),
            'erros_detalhe': erros_detalhe,
        },
        {
            'tabela': 'nota_fiscal_entrada_item',
            'lidos': len(itens_giv),
            'inseridos': inseridos_itens,
            'existentes': 0,
            'erros': len(erros_itens),
            'erros_detalhe': erros_itens,
        },
    ]


def calcular_margem(vl_unitario, vl_custo):
    vl_unitario = valor_decimal_ou_zero(vl_unitario)
    vl_custo = valor_decimal_ou_zero(vl_custo)
    if vl_custo:
        return ((vl_unitario - vl_custo) / vl_custo) * Decimal('100')
    return Decimal('0')


def processar_prevenda_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando prevenda e prevenda_item...")
    prevendas_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'prevenda',
        'cd_empresa, nr_prevenda',
        cd_empresa_giv=cd_empresa_giv
    )
    itens_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'prevenda_item',
        'cd_empresa, nr_prevenda, cd_produto, vl_unitario',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(prevendas_giv)} prevendas e {len(itens_giv)} itens encontrados no GIV.")

    usados = buscar_set_coluna_web(cursor_web, tabelas_web['prevenda'], 'nr_prevenda', tenant_id=tenant_id, cd_empresa=cd_empresa)
    max_prevenda = max([0] + [int(v or 0) for v in usados])
    registros = []
    mapa_prevenda = {}
    erros_detalhe = []
    renumerados = 0
    for prevenda in prevendas_giv:
        motivos = []
        cd_cliente = mapas['cliente'].get((prevenda.get('cd_empresa_cliente'), prevenda.get('cd_cliente'))) or mapas['cliente'].get(prevenda.get('cd_cliente'))
        cd_condicao = mapas['condicao_pagamento'].get(prevenda.get('cd_condicao_pagto'))
        cd_forma = mapas['forma_pagamento'].get(prevenda.get('cd_forma_pagamento')) or mapas.get('forma_pagamento_padrao')
        cd_usuario = (
            mapas['usuario'].get(prevenda.get('cd_funcionario'))
            or mapas['usuario'].get(normalizar_codigo_cidade(prevenda.get('cd_funcionario')))
            or mapas['usuario'].get(prevenda.get('cd_usuario'))
            or mapas['usuario'].get(normalizar_codigo_cidade(prevenda.get('cd_usuario')))
            or mapas.get('usuario_padrao')
        )
        if cd_cliente is None:
            motivos.append(f"cliente GIV {prevenda.get('cd_cliente')} sem de/para")
        if cd_condicao is None:
            motivos.append(f"condicao GIV {prevenda.get('cd_condicao_pagto')} sem de/para")
        if cd_forma is None:
            motivos.append(f"forma GIV {prevenda.get('cd_forma_pagamento')} sem de/para/padrao")
        if cd_usuario is None:
            motivos.append(f"vendedor/usuario GIV {prevenda.get('cd_funcionario')}/{prevenda.get('cd_usuario')} sem de/para/padrao")
        if motivos:
            registrar_erro_validacao(erros_detalhe, 'prevenda', f"nr_prevenda={prevenda.get('nr_prevenda')}", '; '.join(motivos))
            continue

        nr_origem = int(prevenda.get('nr_prevenda') or 0)
        if nr_origem > max_prevenda and nr_origem not in usados:
            nr_web = nr_origem
            usados.add(nr_web)
        else:
            nr_web = proximo_numero_disponivel(usados, max_prevenda)
            max_prevenda = max(max_prevenda, nr_web)
            if nr_web != nr_origem:
                renumerados += 1
        mapa_prevenda[(prevenda.get('cd_empresa'), prevenda.get('nr_prevenda'))] = nr_web

        vl_acrescimo_total = valor_decimal_ou_zero(prevenda.get('vl_acrescimo_total'))
        vl_acrescimo_item = valor_decimal_ou_zero(prevenda.get('vl_acrescimo_total_item'))
        registros.append(limpar_registro({
            'nr_prevenda': nr_web,
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
            'id_status': map_status_prevenda(prevenda.get('id_situacao')),
            'dt_emissao': valor_data_ou_agora(prevenda.get('dt_emissao')),
            'nr_identificacao': prevenda.get('nr_cupom'),
            'cd_cliente': cd_cliente,
            'cd_condicao_pagto': cd_condicao,
            'cd_forma_pagto': cd_forma,
            'cd_usuario_digitacao': cd_usuario,
            'cd_usuario_liberou_limite_credito_maior': mapas['usuario'].get(prevenda.get('cd_usuario_limite_credito')),
            'cd_usuario_liberou_crediario': mapas['usuario'].get(prevenda.get('cd_usuario_crediario')),
            'cd_usuario_liberou_taxa_menor': mapas['usuario'].get(prevenda.get('cd_usuario_reducao_taxa')),
            'cd_usuario_liberou_dias_ultima_consulta_spc': mapas['usuario'].get(prevenda.get('cd_usuario_consulta_spc')),
            'cd_usuario_liberou_desconto_maior': mapas['usuario'].get(prevenda.get('cd_usuario_desconto')),
            'vl_desconto': valor_decimal_ou_zero(prevenda.get('vl_desconto_total')),
            'vl_acrescimo_produto': vl_acrescimo_item,
            # No GIV, vl_acrescimo_total e o acrescimo financeiro e
            # vl_acrescimo_total_item e o acrescimo dos itens. Nao subtrair
            # um do outro: o Web guarda os dois componentes separadamente.
            'vl_acrescimo_financeiro': vl_acrescimo_total,
            'vl_total': valor_decimal_ou_zero(prevenda.get('vl_total')),
            'observacao': prevenda.get('obs'),
            'cd_usuario_caixa': cd_usuario,
            'nr_caixa': prevenda.get('nr_caixa_efetivacao'),
            'cd_usuario_cancelamento': mapas['usuario'].get(prevenda.get('cd_usuario_cancela')),
            'dt_cancelamento': prevenda.get('dt_cancela'),
            'motivo_cancelamento': prevenda.get('motivo_cancela'),
        }))

    if renumerados:
        print(f"[INFO] Prevenda: {renumerados} prevendas receberam novo numero para evitar colisao/reversao.")
    aplicar_limites_texto_web(cursor_web, tabelas_web['prevenda'], registros, 'prevenda')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['prevenda'],
        registros,
        'nr_prevenda',
        'sp_prevenda'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_prevenda = remover_mapa_por_codigos_erro(mapa_prevenda, erros_insert_detalhe, 'nr_prevenda')

    itens = []
    erros_itens = []
    ordinal = {}
    status_por_prevenda = {reg['nr_prevenda']: reg['id_status'] for reg in registros}
    for item in itens_giv:
        nr_prevenda_web = mapa_prevenda.get((item.get('cd_empresa'), item.get('nr_prevenda')))
        if nr_prevenda_web is None:
            continue
        cd_produto_giv = normalizar_codigo_cidade(item.get('cd_produto'))
        cd_produto = mapas['produto'].get(cd_produto_giv) or mapas['produto'].get(item.get('cd_produto'))
        cd_usuario = mapas['usuario'].get(item.get('cd_funcionario')) or mapas.get('usuario_padrao')
        motivos = []
        if cd_produto is None:
            motivos.append(f"produto GIV {item.get('cd_produto')} sem de/para")
        if cd_usuario is None:
            motivos.append(f"usuario comissao GIV {item.get('cd_funcionario')} sem de/para/padrao")
        if motivos:
            registrar_erro_validacao(erros_itens, 'prevenda_item', f"prevenda={item.get('nr_prevenda')} produto={item.get('cd_produto')}", '; '.join(motivos))
            continue
        ordinal[nr_prevenda_web] = ordinal.get(nr_prevenda_web, 0) + 1
        itens.append(limpar_registro({
            'nr_item': ordinal[nr_prevenda_web],
            'nr_prevenda': nr_prevenda_web,
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
            'id_status': 'C' if status_por_prevenda.get(nr_prevenda_web) == 'C' else 'A',
            'cd_produto': cd_produto,
            'qt_produto': valor_decimal_ou_zero(item.get('qt_produto')),
            'qt_devolvido': valor_decimal_ou_zero(item.get('qt_devolvido')),
            'vl_unitario': valor_decimal_ou_zero(item.get('vl_unitario')),
            'pr_margem_lucro': calcular_margem(item.get('vl_unitario'), item.get('vl_custo')),
            'vl_custo_unitario': valor_decimal_ou_zero(item.get('vl_custo')),
            'vl_desconto': valor_decimal_ou_zero(item.get('vl_desconto')),
            'vl_acrescimo_produto': valor_decimal_ou_zero(item.get('vl_acrescimo')),
            'vl_acrescimo_financeiro': Decimal('0'),
            'cd_usuario_comissao': cd_usuario,
            'pr_comissao': valor_decimal_ou_zero(item.get('pr_comissao')),
            'observacao': item.get('complemento'),
            'id_produto_promocao': valor_flag(item.get('id_produto_promocao'), 'N'),
            'vl_promocao': valor_decimal_ou_zero(item.get('vl_promocao')),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['prevenda_item'], itens, 'prevenda_item')
    inseridos_itens, erros_insert_itens, erros_insert_itens_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['prevenda_item'],
        itens,
        'nr_item',
        'sp_prevenda_item'
    )
    erros_itens.extend(erros_insert_itens_detalhe)

    return mapa_prevenda, [
        {'tabela': 'prevenda', 'lidos': len(prevendas_giv), 'inseridos': inseridos, 'existentes': 0, 'erros': len(erros_detalhe), 'erros_detalhe': erros_detalhe},
        {'tabela': 'prevenda_item', 'lidos': len(itens_giv), 'inseridos': inseridos_itens, 'existentes': 0, 'erros': len(erros_itens), 'erros_detalhe': erros_itens},
    ]


def combinar_data_hora(data, hora=None):
    """Combina date + time quando o GIV envia os campos separados."""
    if isinstance(data, datetime.datetime):
        return data
    if isinstance(data, datetime.date) and isinstance(hora, datetime.time):
        return datetime.datetime.combine(data, hora)
    if isinstance(data, datetime.date):
        return datetime.datetime.combine(data, datetime.time.min)
    return valor_data_ou_agora(data)


def observacao_condicional(registro):
    partes = [
        registro.get('obs1'),
        registro.get('obs2'),
        registro.get('obs3'),
        registro.get('obs4'),
    ]
    if map_status_condicional(registro.get('id_situacao')) == 'C':
        partes.append(registro.get('motivo_cancela'))
    texto = ' | '.join(limpar_valor(parte) for parte in partes if limpar_valor(parte))
    return texto or None


def processar_condicionais_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando condicionais e itens...")
    condicionais_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'orcamento',
        'cd_empresa, nr_orcamento',
        cd_empresa_giv=cd_empresa_giv
    )
    itens_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'orcamento_item',
        'cd_empresa, nr_orcamento, nr_item',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(condicionais_giv)} condicionais e {len(itens_giv)} itens encontrados no GIV.")
    status_giv = {}
    for condicional in condicionais_giv:
        status = limpar_valor(condicional.get('id_situacao')) or 'SEM_STATUS'
        status_giv[status] = status_giv.get(status, 0) + 1
    if status_giv:
        status_txt = ', '.join(f"{status}={total}" for status, total in sorted(status_giv.items()))
        print(f"[INFO] Condicionais GIV por status: {status_txt}.")

    sequence_condicional = buscar_sequence_coluna_web(
        cursor_web,
        tabelas_web['condicional'],
        'nr_condicional'
    )
    max_condicional = sincronizar_sequence_com_max(
        cursor_web,
        tabelas_web['condicional'],
        'nr_condicional',
        sequence_condicional
    )
    print(f"[OK] Sequence condicional: {sequence_condicional} (sincronizada com max={max_condicional}).")

    totais_itens = {}
    for item in itens_giv:
        chave = (item.get('cd_empresa'), item.get('nr_orcamento'))
        total = totais_itens.setdefault(chave, {'qt': Decimal('0'), 'vl': Decimal('0')})
        total['qt'] += valor_decimal_ou_zero(item.get('qt_cotada'))
        total['vl'] += valor_decimal_ou_zero(item.get('vl_total_item'))

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_condicional, len(condicionais_giv)))
    registros = []
    mapa_condicional = {}
    erros_detalhe = []
    amostras_depara = []
    for condicional in condicionais_giv:
        nr_condicional_web = next(codigos)
        chave_origem = (condicional.get('cd_empresa'), condicional.get('nr_orcamento'))
        cd_cliente = (
            mapas['cliente'].get((condicional.get('cd_empresa_cliente'), condicional.get('cd_cliente')))
            or mapas['cliente'].get((condicional.get('cd_empresa'), condicional.get('cd_cliente')))
            or mapas['cliente'].get(condicional.get('cd_cliente'))
            or mapas['cliente'].get(normalizar_codigo_cidade(condicional.get('cd_cliente')))
        )
        cd_usuario = (
            mapas['usuario'].get(condicional.get('cd_usuario_responsavel'))
            or mapas['usuario'].get(normalizar_codigo_cidade(condicional.get('cd_usuario_responsavel')))
            or mapas['usuario'].get(condicional.get('cd_usuario'))
            or mapas['usuario'].get(normalizar_codigo_cidade(condicional.get('cd_usuario')))
            or mapas.get('usuario_padrao')
        )
        motivos = []
        if cd_cliente is None:
            motivos.append(f"cliente GIV {condicional.get('cd_cliente')} sem de/para")
        if cd_usuario is None:
            motivos.append(
                f"usuario GIV {condicional.get('cd_usuario_responsavel')}/{condicional.get('cd_usuario')} sem de/para/padrao"
            )
        if motivos:
            registrar_erro_validacao(
                erros_detalhe,
                'condicional',
                f"nr_orcamento={condicional.get('nr_orcamento')}",
                '; '.join(motivos)
            )
            continue

        total_item = totais_itens.get(chave_origem, {'qt': Decimal('0'), 'vl': Decimal('0')})
        vl_total = valor_decimal_ou_zero(condicional.get('vl_total_orcamento')) or total_item['vl']
        mapa_condicional[chave_origem] = nr_condicional_web
        if len(amostras_depara) < 5:
            amostras_depara.append(
                f"GIV {condicional.get('nr_orcamento')} -> Web {nr_condicional_web} "
                f"cliente={cd_cliente} valor={vl_total}"
            )
        registros.append(limpar_registro({
            'nr_condicional': nr_condicional_web,
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
            'id_status': map_status_condicional(condicional.get('id_situacao')),
            'dt_digitacao': combinar_data_hora(condicional.get('dt_emissao'), condicional.get('hr_digitacao')),
            'cd_cliente': cd_cliente,
            'cd_usuario_digitacao': cd_usuario,
            'vl_total': vl_total,
            'qt_produto_total': total_item['qt'],
            'vl_acrescimo': (
                valor_decimal_ou_zero(condicional.get('vl_acrescimo_total'))
                + valor_decimal_ou_zero(condicional.get('vl_acrescimo_total_item'))
            ),
            'vl_desconto': valor_decimal_ou_zero(condicional.get('vl_desconto_total')),
            'observacao': observacao_condicional(condicional),
        }))

    print(f"[OK] {len(registros)} condicionais para inserir.")
    if amostras_depara:
        print("[INFO] De/para condicional (amostra): " + " | ".join(amostras_depara))
    aplicar_limites_texto_web(cursor_web, tabelas_web['condicional'], registros, 'condicional')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['condicional'],
        registros,
        'nr_condicional',
        'sp_condicional'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_condicional = remover_mapa_por_codigos_erro(mapa_condicional, erros_insert_detalhe, 'nr_condicional')
    print(f"[OK] Condicionais inseridos no Web: {inseridos}; nao inseridos: {len(erros_detalhe)}.")

    itens = []
    erros_itens = []
    ordinal = {}
    status_por_condicional = {reg['nr_condicional']: reg['id_status'] for reg in registros}
    for item in itens_giv:
        nr_condicional_web = mapa_condicional.get((item.get('cd_empresa'), item.get('nr_orcamento')))
        if nr_condicional_web is None:
            continue

        cd_produto_giv = normalizar_codigo_cidade(item.get('cd_produto'))
        cd_produto = mapas['produto'].get(cd_produto_giv) or mapas['produto'].get(item.get('cd_produto'))
        if cd_produto is None:
            registrar_erro_validacao(
                erros_itens,
                'condicional_item',
                f"condicional={item.get('nr_orcamento')} produto={item.get('cd_produto')}",
                f"produto GIV {item.get('cd_produto')} sem de/para"
            )
            continue

        ordinal[nr_condicional_web] = ordinal.get(nr_condicional_web, 0) + 1
        status_item = 'C' if status_por_condicional.get(nr_condicional_web) == 'C' else 'A'
        qt_produto = valor_decimal_ou_zero(item.get('qt_cotada'))
        qt_faturado = valor_decimal_ou_zero(item.get('qt_atendida'))
        qt_devolvido = valor_decimal_ou_zero(item.get('qt_suspenso'))
        vl_unitario = valor_decimal_ou_zero(item.get('vl_unitario'))
        vl_custo = valor_decimal_ou_zero(item.get('vl_custo'))
        itens.append(limpar_registro({
            'nr_item': ordinal[nr_condicional_web],
            'nr_condicional': nr_condicional_web,
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
            'id_status': status_item,
            'cd_produto': cd_produto,
            'qt_produto': qt_produto,
            'qt_devolvido': qt_devolvido,
            'qt_faturado': qt_faturado,
            'cd_usuario_status': mapas['usuario'].get(item.get('cd_usuario_suspenso')),
            'dt_status': item.get('dt_suspenso'),
            'motivo_cancelamento': item.get('ds_motivo_suspenso') if status_item == 'C' or qt_devolvido else None,
            'vl_unitario': vl_unitario,
            'vl_custo_unitario': vl_custo,
            'pr_margem_lucro': calcular_margem(vl_unitario, vl_custo),
            'vl_desconto': valor_decimal_ou_zero(item.get('vl_desconto')),
            'vl_acrescimo_produto': valor_decimal_ou_zero(item.get('vl_acrescimo')),
            'vl_acrescimo_financeiro': Decimal('0'),
            'id_produto_promocao': valor_flag(item.get('id_produto_promocao'), 'N'),
            'vl_promocao': valor_decimal_ou_zero(item.get('vl_promocao')),
            'cd_usuario_comissao': mapas['usuario'].get(item.get('cd_funcionario')),
            'pr_comissao': valor_decimal_ou_zero(item.get('pr_comissao')),
        }))

    print(f"[OK] {len(itens)} itens de condicionais para inserir.")
    aplicar_limites_texto_web(cursor_web, tabelas_web['condicional_item'], itens, 'condicional_item')
    inseridos_itens, erros_insert_itens, erros_insert_itens_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['condicional_item'],
        itens,
        'nr_item',
        'sp_condicional_item'
    )
    erros_itens.extend(erros_insert_itens_detalhe)
    print(f"[OK] Itens de condicionais inseridos no Web: {inseridos_itens}; nao inseridos: {len(erros_itens)}.")

    return mapa_condicional, [
        {
            'tabela': 'condicional',
            'lidos': len(condicionais_giv),
            'inseridos': inseridos,
            'existentes': 0,
            'erros': len(erros_detalhe),
            'erros_detalhe': erros_detalhe,
        },
        {
            'tabela': 'condicional_item',
            'lidos': len(itens_giv),
            'inseridos': inseridos_itens,
            'existentes': 0,
            'erros': len(erros_itens),
            'erros_detalhe': erros_itens,
        },
    ]


def observacao_nf_saida(nota):
    partes = [nota.get('obs'), nota.get('obs1'), nota.get('obs2'), nota.get('obs3'), nota.get('obs4'), nota.get('obs5')]
    texto = ' | '.join(limpar_valor(parte) for parte in partes if limpar_valor(parte))
    return texto or None


def processar_nota_fiscal_saida_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando nota_fiscal_saida, info e itens...")
    notas_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'nota_fiscal_saida',
        'cd_empresa, nr_nota, serie',
        cd_empresa_giv=cd_empresa_giv
    )
    itens_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'nota_fiscal_saida_item',
        'cd_empresa, nr_nota, serie, cd_produto, vl_unitario',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(notas_giv)} notas de saida e {len(itens_giv)} itens encontrados no GIV.")

    sequence_nf = buscar_sequence_coluna_web(cursor_web, tabelas_web['nota_fiscal_saida'], 'nf_id')
    max_nf = sincronizar_sequence_com_max(cursor_web, tabelas_web['nota_fiscal_saida'], 'nf_id', sequence_nf)
    print(f"[OK] Sequence nota_fiscal_saida: {sequence_nf} (sincronizada com max={max_nf}).")

    # A rotina pode ser executada novamente depois de uma conversao parcial.
    # Reaproveitamos a NF Web pela chave de origem (empresa, numero e serie)
    # e reservamos sequence somente para as notas que ainda nao existem.
    mapa_nf_existente = dict(mapas.get('nf_saida') or {})
    mapa_nf = dict(mapa_nf_existente)
    notas_novas = []
    chaves_novas = set()
    for nota in notas_giv:
        chave = (nota.get('cd_empresa'), nota.get('nr_nota'), serie_doc(nota.get('serie')))
        if chave in mapa_nf_existente or chave in chaves_novas:
            continue
        chaves_novas.add(chave)
        notas_novas.append((chave, nota))

    if mapa_nf_existente:
        print(
            f"[OK] {len(notas_giv) - len(notas_novas)} notas de saida ja existem no Web; "
            f"{len(notas_novas)} serao novas nesta execucao."
        )

    codigos = iter(
        reservar_valores_sequence(cursor_web, sequence_nf, len(notas_novas))
        if notas_novas
        else []
    )
    registros = []
    infos = []
    erros_detalhe = []
    nf_ids_novos = set()
    clientes_padrao = 0
    empresa = mapas.get('empresa') or {}
    cliente_padrao = mapas.get('cliente_padrao')
    for chave, nota in notas_novas:
        nf_id = next(codigos)
        motivos = []
        cd_cliente = mapas['cliente'].get((nota.get('cd_empresa_cliente'), nota.get('cd_cliente'))) or mapas['cliente'].get(nota.get('cd_cliente'))
        if cd_cliente is None and cliente_padrao is not None:
            # Ha NFs antigas com cd_empresa_cliente=1 e outras com 0, mas os
            # codigos apontados nao existem mais na tabela cliente do GIV.
            # Preservamos a NF e seus itens usando o consumidor padrao.
            cd_cliente = cliente_padrao
            clientes_padrao += 1
        cd_cfop = nota.get('cd_natureza')
        if cd_cliente is None:
            motivos.append(f"cliente GIV {nota.get('cd_cliente')} sem de/para")
        if cd_cfop not in mapas['cfop']:
            motivos.append(f"CFOP {cd_cfop} nao encontrado no Web")
        if motivos:
            registrar_erro_validacao(erros_detalhe, 'nota_fiscal_saida', f"nr_nota={nota.get('nr_nota')} serie={nota.get('serie')}", '; '.join(motivos))
            continue
        mapa_nf[chave] = nf_id
        nf_ids_novos.add(nf_id)
        vl_acrescimo = valor_decimal_ou_zero(nota.get('vl_acrescimo'))
        vl_acrescimo_item = valor_decimal_ou_zero(nota.get('vl_acrescimo_total_item'))
        registros.append(limpar_registro({
            'nf_id': nf_id,
            'tenant_id': tenant_id,
            'id_status': map_status_nf_saida(nota.get('id_situacao')),
            'cd_empresa': cd_empresa,
            'nr_nota': nota.get('nr_nota'),
            'serie': serie_doc(nota.get('serie')),
            'dt_emissao': valor_data_ou_agora(nota.get('dt_emissao')),
            'id_pessoa': valor_flag(nota.get('id_pessoa'), 'F'),
            'cd_cliente': cd_cliente,
            'cd_cfop': cd_cfop,
            'id_finalidade_nfe': nota.get('id_finalidade_nfe') or 1,
            'tp_frete': valor_flag(nota.get('tp_frete'), 'N'),
            'vl_frete': valor_decimal_ou_zero(nota.get('vl_frete')),
            'vl_desconto': valor_decimal_ou_zero(nota.get('vl_desconto_total')),
            'vl_acrescimo_produto': vl_acrescimo_item,
            # O campo vl_acrescimo do GIV representa o componente financeiro;
            # vl_acrescimo_total_item ja e o componente dos produtos.
            'vl_acrescimo_financeiro': vl_acrescimo,
            'vl_outras_despesas': Decimal('0'),
            'vl_total': valor_decimal_ou_zero(nota.get('vl_total_nota')),
            'vl_base_substituicao': valor_decimal_ou_zero(nota.get('vl_base_icms_subst')),
            'vl_substituicao': valor_decimal_ou_zero(nota.get('vl_icms_subst')),
            'vl_base_icms': valor_decimal_ou_zero(nota.get('vl_base_icms')),
            'vl_icms': valor_decimal_ou_zero(nota.get('vl_icms')),
            'vl_ipi': valor_decimal_ou_zero(nota.get('vl_ipi')),
            'observacao': observacao_nf_saida(nota),
        }))

        cd_usuario = mapas['usuario'].get(nota.get('cd_usuario')) or mapas.get('usuario_padrao')
        cd_forma = mapas.get('forma_pagamento_padrao')
        cd_condicao = mapas['condicao_pagamento'].get(nota.get('cd_condicao_pagto')) or mapas.get('condicao_padrao')
        if cd_usuario and cd_forma and cd_condicao:
            infos.append(limpar_registro({
                'nf_id': nf_id,
                'tenant_id': tenant_id,
                'dt_saida': nota.get('dt_saida'),
                'nr_identificacao': nota.get('nr_cupom_fiscal'),
                'cd_condicao_pagto': cd_condicao,
                'cd_forma_pagamento': cd_forma,
                'cd_transportador': mapas['fornecedor'].get(nota.get('cd_transportador')) if nota.get('cd_transportador') else None,
                'qt_volume': nota.get('qt_volume'),
                'ds_volume': nota.get('ds_volume'),
                'peso_bruto': valor_decimal_ou_zero(nota.get('peso_bruto')),
                'peso_liquido': valor_decimal_ou_zero(nota.get('peso_liquido')),
                'cd_usuario_digitacao': cd_usuario,
                'cd_usuario_liberou_dias_ultima_consulta_spc': mapas['usuario'].get(nota.get('cd_usuario_consulta_spc')),
                'cd_usuario_liberou_desconto_maior': mapas['usuario'].get(nota.get('cd_usuario_liberou')),
                'cd_usuario_cancelamento': mapas['usuario'].get(nota.get('cd_usuario_cancela')),
                'dt_cancelamento': nota.get('dt_cancela'),
                'motivo_cancelamento': nota.get('motivo_cancela'),
                'cd_usuario_caixa': mapas['usuario'].get(nota.get('cd_usuario_impressao')) or cd_usuario,
                'nr_caixa': nota.get('nr_caixa'),
                'dt_impressao': nota.get('dt_impressao'),
                'id_nfe_cancelamento_apos_prazo': valor_flag(nota.get('id_nfe_cancelamento_apos_prazo'), 'N'),
                'cliente_nome_nfce': nota.get('ds_cliente_nfce') or nota.get('nm_cliente'),
                'cliente_tp_pessoa_nfce': nota.get('tp_pessoa_nfce'),
                'cliente_id_cadastro_nacional_nfce': normalizar_cadastro_nacional(nota.get('cgc_nfce') or nota.get('cpf')),
                'ds_url_consulta_nfce': nota.get('ds_url_consulta_nfce'),
                'empresa_id_cadastro_nacional': empresa.get('cnpj'),
                'empresa_id_cadastro_estadual': empresa.get('inscricao_estadual'),
                'empresa_id_cadastro_municipal': empresa.get('inscricao_municipal'),
                'clienteCd_cliente': cd_cliente,
                'clienteCd_empresa': cd_empresa,
            }))
        else:
            registrar_erro_validacao(erros_detalhe, 'nota_fiscal_saida_info', f"nf_id={nf_id}", 'sem usuario/forma/condicao padrao para info')

    if clientes_padrao:
        print(
            f"[AVISO] {clientes_padrao} notas de saida usarao o cliente padrao "
            f"cd_cliente={cliente_padrao}: o GIV informou consumidor sem cadastro "
            "(cd_empresa_cliente=0)."
        )

    aplicar_limites_texto_web(cursor_web, tabelas_web['nota_fiscal_saida'], registros, 'nota_fiscal_saida')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['nota_fiscal_saida'],
        registros,
        'nf_id',
        'sp_nf_saida'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_nf = remover_mapa_por_codigos_erro(mapa_nf, erros_insert_detalhe, 'nf_id')

    aplicar_limites_texto_web(cursor_web, tabelas_web['nota_fiscal_saida_info'], infos, 'nota_fiscal_saida_info')
    inseridos_info, erros_info, erros_info_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['nota_fiscal_saida_info'],
        infos,
        'nf_id',
        'sp_nf_saida_info'
    )

    itens = []
    erros_itens = []
    ordinal = {}
    status_nf = {reg['nf_id']: reg['id_status'] for reg in registros}
    itens_existentes = 0
    for item in itens_giv:
        chave = (item.get('cd_empresa'), item.get('nr_nota'), serie_doc(item.get('serie')))
        nf_id = mapa_nf.get(chave)
        if nf_id is None:
            continue
        if nf_id not in nf_ids_novos:
            itens_existentes += 1
            continue
        cd_cfop = item.get('cd_natureza')
        cd_produto = mapas['produto'].get(item.get('cd_produto'))
        motivos = []
        if cd_produto is None:
            motivos.append(f"produto GIV {item.get('cd_produto')} sem de/para")
        if cd_cfop not in mapas['cfop']:
            motivos.append(f"CFOP {cd_cfop} nao encontrado no Web")
        if motivos:
            registrar_erro_validacao(erros_itens, 'nota_fiscal_saida_item', f"nf={item.get('nr_nota')} produto={item.get('cd_produto')}", '; '.join(motivos))
            continue
        ordinal[nf_id] = ordinal.get(nf_id, 0) + 1
        itens.append(limpar_registro({
            'nr_item': ordinal[nf_id],
            'nf_id': nf_id,
            'tenant_id': tenant_id,
            'id_status': 'C' if status_nf.get(nf_id) == 'C' else map_status_nf_saida(item.get('id_situacao')),
            'cd_produto': cd_produto,
            'qt_produto': valor_decimal_ou_zero(item.get('qt_produto')),
            'qt_devolvido': Decimal('0'),
            'vl_unitario': valor_decimal_ou_zero(item.get('vl_unitario')),
            'pr_margem_lucro': valor_decimal_ou_zero(item.get('pr_margem_lucro')),
            'vl_custo_unitario': valor_decimal_ou_zero(item.get('vl_custo') or item.get('vl_custo_reposicao')),
            'vl_frete': valor_decimal_ou_zero(item.get('vl_frete')),
            'vl_desconto': valor_decimal_ou_zero(item.get('vl_desconto')),
            'vl_acrescimo_produto': valor_decimal_ou_zero(item.get('vl_acrescimo')),
            'vl_acrescimo_financeiro': valor_decimal_ou_zero(item.get('vl_acrescimo_financeiro')),
            'vl_outras_despesas': valor_decimal_ou_zero(item.get('vl_outras_despesas')),
            'cd_cfop': cd_cfop,
            'cd_cst_nacional': limpar_valor(item.get('cd_situacao_tributaria')) or CD_SITUACAO_TRIBUTARIA_PRODUTO_PADRAO,
            'cd_cst_estadual': limpar_valor(item.get('cd_situacao_tributaria')) or CD_SITUACAO_TRIBUTARIA_PRODUTO_PADRAO,
            'id_movimentou_estoque': valor_flag(item.get('id_movimenta_estoque'), 'N'),
            'id_produto_promocao': valor_flag(item.get('id_produto_promocao'), 'N'),
            'vl_promocao': Decimal('0'),
            'vl_base_substituicao': valor_decimal_ou_zero(item.get('vl_base_substituicao')),
            'pr_reducao_base_substituicao': valor_decimal_ou_zero(item.get('pr_reducao_base_substituicao')),
            'pr_base_substituicao': valor_decimal_ou_zero(item.get('pr_base_substituicao')),
            'pr_icms_substituicao': valor_decimal_ou_zero(item.get('pr_icms_substituicao')),
            'vl_substituicao': valor_decimal_ou_zero(item.get('vl_substituicao')),
            'vl_base_icms': valor_decimal_ou_zero(item.get('vl_base_icms')),
            'pr_reducao_icms': valor_decimal_ou_zero(item.get('pr_reducao_icms')),
            'pr_icms': valor_decimal_ou_zero(item.get('pr_icms')),
            'vl_icms': valor_decimal_ou_zero(item.get('vl_icms')),
            'vl_base_diferencial_icms': Decimal('0'),
            'pr_diferencial_icms': valor_decimal_ou_zero(item.get('pr_diferencial_icms')),
            'vl_diferencial_icms': valor_decimal_ou_zero(item.get('vl_diferencial_icms')),
            'vl_base_fundo_combate_pobreza': valor_decimal_ou_zero(item.get('vl_base_fundo_combate_pobreza')),
            'pr_fundo_combate_pobreza': valor_decimal_ou_zero(item.get('pr_fundo_combate_pobreza')),
            'vl_fundo_combate_pobreza': valor_decimal_ou_zero(item.get('vl_fundo_combate_pobreza')),
            'vl_base_ipi': valor_decimal_ou_zero(item.get('vl_base_ipi')),
            'pr_ipi': valor_decimal_ou_zero(item.get('pr_ipi')),
            'vl_ipi': valor_decimal_ou_zero(item.get('vl_ipi')),
            'vl_base_pis_cofins': Decimal('0'),
            'pr_pis': valor_decimal_ou_zero(item.get('pr_pis')),
            'pr_cofins': valor_decimal_ou_zero(item.get('pr_cofins')),
            'vl_pis': valor_decimal_ou_zero(item.get('vl_pis')),
            'vl_cofins': valor_decimal_ou_zero(item.get('vl_cofins')),
            'observacao': limpar_valor(item.get('complemento')) or '',
            'cd_produto_barra_ean': somente_digitos(item.get('cd_produto_barra_ean')),
            'cd_produto_barra_ean_trib': somente_digitos(item.get('cd_produto_barra_ean_trib')),
            'cd_beneficio_fiscal': limpar_valor(item.get('cd_beneficio_fiscal')),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['nota_fiscal_saida_item'], itens, 'nota_fiscal_saida_item')
    inseridos_itens, erros_insert_itens, erros_insert_itens_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['nota_fiscal_saida_item'],
        itens,
        'nr_item',
        'sp_nf_saida_item'
    )
    erros_itens.extend(erros_insert_itens_detalhe)

    return mapa_nf, [
        {
            'tabela': 'nota_fiscal_saida',
            'lidos': len(notas_giv),
            'inseridos': inseridos,
            'existentes': len(notas_giv) - len(notas_novas),
            'erros': len(erros_detalhe),
            'erros_detalhe': erros_detalhe,
        },
        {
            'tabela': 'nota_fiscal_saida_info',
            'lidos': len(notas_giv),
            'inseridos': inseridos_info,
            'existentes': len(notas_giv) - len(notas_novas),
            'erros': erros_info,
            'erros_detalhe': erros_info_detalhe,
        },
        {
            'tabela': 'nota_fiscal_saida_item',
            'lidos': len(itens_giv),
            'inseridos': inseridos_itens,
            'existentes': itens_existentes,
            'erros': len(erros_itens),
            'erros_detalhe': erros_itens,
        },
    ]


def processar_titulo_receber_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando contas a receber e historicos...")
    titulos_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'titulo_receber',
        'cd_empresa, nr_titulo, serie, nr_parcela, cd_pessoa',
        cd_empresa_giv=cd_empresa_giv
    )
    historicos_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'titulo_receber_historico',
        'cd_empresa, nr_titulo, serie, nr_parcela, nr_ocorrencia',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(titulos_giv)} titulos receber e {len(historicos_giv)} historicos encontrados no GIV.")

    sequence_tr = buscar_sequence_coluna_web(cursor_web, tabelas_web['titulo_receber'], 'tr_id')
    max_tr = sincronizar_sequence_com_max(cursor_web, tabelas_web['titulo_receber'], 'tr_id', sequence_tr)
    print(f"[OK] Sequence titulo_receber: {sequence_tr} (sincronizada com max={max_tr}).")

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_tr, len(titulos_giv)))
    registros = []
    mapa_tr = {}
    erros_detalhe = []
    for titulo in titulos_giv:
        tr_id = next(codigos)
        chave = (
            titulo.get('cd_empresa'),
            titulo.get('nr_titulo'),
            serie_doc(titulo.get('serie')),
            titulo.get('nr_parcela'),
            titulo.get('cd_empresa_cliente'),
            titulo.get('cd_pessoa'),
        )
        motivos = []
        cd_cliente = mapas['cliente'].get((titulo.get('cd_empresa_cliente'), titulo.get('cd_pessoa'))) or mapas['cliente'].get(titulo.get('cd_pessoa'))
        cd_condicao = mapas['condicao_pagamento'].get(titulo.get('cd_condicao_pagto')) or mapas.get('condicao_padrao')
        cd_forma = mapas['forma_pagamento'].get(titulo.get('cd_forma_pagamento')) or mapas.get('forma_pagamento_padrao')
        if cd_cliente is None:
            motivos.append(f"cliente GIV {titulo.get('cd_pessoa')} sem de/para")
        if cd_condicao is None:
            motivos.append(f"condicao GIV {titulo.get('cd_condicao_pagto')} sem de/para/padrao")
        if cd_forma is None:
            motivos.append(f"forma GIV {titulo.get('cd_forma_pagamento')} sem de/para/padrao")
        if motivos:
            registrar_erro_validacao(erros_detalhe, 'titulo_receber', f"titulo={titulo.get('nr_titulo')}/{titulo.get('nr_parcela')}", '; '.join(motivos))
            continue
        mapa_tr[chave] = tr_id
        valores_titulo = calcular_valores_titulo_receber(titulo)
        status = map_status_titulo_receber(
            titulo.get('id_situacao'),
            valores_titulo['vl_liquido'],
            valores_titulo['vl_pago']
        )
        obs = limpar_valor(titulo.get('obs'))
        if (limpar_valor(titulo.get('id_situacao')) or '').upper() == 'NG':
            obs = ((obs + ' | ') if obs else '') + 'NEGOCIADO NO GIV'
        registros.append(limpar_registro({
            'tr_id': tr_id,
            'tenant_id': tenant_id,
            'id_status': status,
            'nf_id': mapas.get('nf_saida', {}).get((titulo.get('cd_empresa'), titulo.get('nr_titulo'), serie_doc(titulo.get('serie')))),
            'cd_empresa': cd_empresa,
            'nr_titulo': titulo.get('nr_titulo'),
            'nr_parcela': titulo.get('nr_parcela'),
            'nr_parcela_original': titulo.get('nr_parcela_original'),
            'serie': serie_doc(titulo.get('serie')),
            'cd_cliente': cd_cliente,
            'cd_condicao_pagto': cd_condicao,
            'cd_forma_pagamento': cd_forma,
            'observacao': obs,
            'dt_digitacao': titulo.get('dt_manutencao') or titulo.get('dt_emissao') or datetime.datetime.now(),
            'dt_emissao': valor_data_ou_agora(titulo.get('dt_emissao')),
            'dt_vencto': valor_data_ou_agora(titulo.get('dt_vencto')),
            'dt_pagto': titulo.get('dt_pagto'),
            'vl_titulo': valores_titulo['vl_titulo'],
            'vl_acrescimo': valores_titulo['vl_acrescimo'],
            'vl_desconto': valores_titulo['vl_desconto'],
            'vl_pago': valores_titulo['vl_pago'],
            'cd_usuario_cancelamento': None,
            'dt_cancelamento': titulo.get('dt_cancelamento'),
            'motivo_cancelamento': (obs or 'Cancelado no GIV') if status == 'C' else None,
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['titulo_receber'], registros, 'titulo_receber')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['titulo_receber'],
        registros,
        'tr_id',
        'sp_titulo_receber'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_tr = remover_mapa_por_codigos_erro(mapa_tr, erros_insert_detalhe, 'tr_id')

    historicos = []
    erros_hist = []
    sequence_trh = buscar_sequence_coluna_web(cursor_web, tabelas_web['titulo_receber_historico'], 'trh_id')
    sincronizar_sequence_com_max(cursor_web, tabelas_web['titulo_receber_historico'], 'trh_id', sequence_trh)
    codigos_hist = iter(reservar_valores_sequence(cursor_web, sequence_trh, len(historicos_giv)))
    for hist in historicos_giv:
        trh_id = next(codigos_hist)
        chave = (
            hist.get('cd_empresa'),
            hist.get('nr_titulo'),
            serie_doc(hist.get('serie')),
            hist.get('nr_parcela'),
            hist.get('cd_empresa_cliente'),
            hist.get('cd_cliente'),
        )
        tr_id = mapa_tr.get(chave)
        if tr_id is None:
            registrar_erro_validacao(erros_hist, 'titulo_receber_historico', f"titulo={hist.get('nr_titulo')}/{hist.get('nr_parcela')}", 'titulo pai sem de/para')
            continue
        nm_coluna = 'historico'
        anterior = '0'
        novo = '0'
        if hist.get('dt_vencto_anterior') != hist.get('dt_vencto_atual'):
            nm_coluna = 'dt_vencto'
            anterior = str(hist.get('dt_vencto_anterior') or '0')
            novo = str(hist.get('dt_vencto_atual') or '0')
        elif valor_decimal_ou_zero(hist.get('vl_movto')):
            nm_coluna = 'vl_pago'
            anterior = '0'
            novo = str(hist.get('vl_movto'))
        elif 'LIQUID' in (chave_texto(hist.get('historico')) or ''):
            nm_coluna = 'id_status'
            anterior = 'A'
            novo = 'L'
        historicos.append(limpar_registro({
            'trh_id': trh_id,
            'tenant_id': tenant_id,
            'tr_id': tr_id,
            'cd_usuario': mapas['usuario'].get(hist.get('cd_usuario')),
            'dt_historico': valor_data_ou_agora(hist.get('dt_historico')),
            'ds_historico': limpar_valor(hist.get('historico')) or 'Historico GIV',
            'nm_coluna': nm_coluna,
            'vl_coluna_anterior': anterior,
            'vl_coluna_novo': novo,
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['titulo_receber_historico'], historicos, 'titulo_receber_historico')
    inseridos_hist, erros_insert_hist, erros_insert_hist_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['titulo_receber_historico'],
        historicos,
        'trh_id',
        'sp_titulo_receber_hist'
    )
    erros_hist.extend(erros_insert_hist_detalhe)
    return mapa_tr, [
        {'tabela': 'titulo_receber', 'lidos': len(titulos_giv), 'inseridos': inseridos, 'existentes': 0, 'erros': len(erros_detalhe), 'erros_detalhe': erros_detalhe},
        {'tabela': 'titulo_receber_historico', 'lidos': len(historicos_giv), 'inseridos': inseridos_hist, 'existentes': 0, 'erros': len(erros_hist), 'erros_detalhe': erros_hist},
    ]


def carregar_mapa_banco_conta(cursor_web, tabela_web_banco_conta, tenant_id, cd_empresa):
    cursor_web.execute(
        f"""
        SELECT cd_conta, cd_banco
          FROM {tabela_web_banco_conta}
         WHERE tenant_id = %s
           AND cd_empresa = %s
        """,
        (tenant_id, cd_empresa)
    )
    mapa = {}
    for cd_conta, cd_banco in cursor_web.fetchall():
        mapa.setdefault(cd_conta, cd_conta)
        mapa.setdefault(cd_banco, cd_conta)
    return mapa


def carregar_mapa_nf_saida_existente(
    cursor_web,
    tabela_web_nf_saida,
    tenant_id,
    cd_empresa,
    cd_empresa_giv=None
):
    cursor_web.execute(
        f"""
        SELECT nf_id, nr_nota, serie
          FROM {tabela_web_nf_saida}
         WHERE tenant_id = %s
           AND cd_empresa = %s
        """,
        (tenant_id, cd_empresa)
    )
    empresa_origem = cd_empresa_giv if cd_empresa_giv is not None else cd_empresa
    return {
        (empresa_origem, nr_nota, serie_doc(serie)): nf_id
        for nf_id, nr_nota, serie in cursor_web.fetchall()
    }


def carregar_mapa_nf_entrada_existente(cursor_web, tabela_web_nf_entrada, mapa_fornecedor, tenant_id, cd_empresa):
    reverso_fornecedor = {}
    for cd_giv, cd_web in mapa_fornecedor.items():
        reverso_fornecedor.setdefault(cd_web, set()).add(cd_giv)

    cursor_web.execute(
        f"""
        SELECT nf_id, nr_nota, serie, cd_fornecedor
          FROM {tabela_web_nf_entrada}
         WHERE tenant_id = %s
           AND cd_empresa = %s
        """,
        (tenant_id, cd_empresa)
    )
    mapa = {}
    for nf_id, nr_nota, serie, cd_fornecedor_web in cursor_web.fetchall():
        for cd_fornecedor_giv in reverso_fornecedor.get(cd_fornecedor_web, ()):
            mapa[(cd_empresa, nr_nota, serie_doc(serie), cd_fornecedor_giv)] = nf_id
    return mapa


def processar_titulo_pagar_rotina(cursor_giv, cursor_web, tabelas_web, mapas, tenant_id, cd_empresa, cd_empresa_giv=None):
    print()
    print("[...] Processando contas a pagar e historicos...")
    titulos_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'titulo_pagar',
        'cd_empresa, nr_titulo, serie, nr_parcela, cd_fornecedor',
        cd_empresa_giv=cd_empresa_giv
    )
    historicos_giv = buscar_registros_giv_tabela(
        cursor_giv,
        'titulo_pagar_historico',
        'cd_empresa, nr_titulo, serie, nr_parcela, cd_fornecedor, nr_ocorrencia',
        cd_empresa_giv=cd_empresa_giv
    )
    print(f"[OK] {len(titulos_giv)} titulos pagar e {len(historicos_giv)} historicos encontrados no GIV.")

    sequence_tp = buscar_sequence_coluna_web(cursor_web, tabelas_web['titulo_pagar'], 'tp_id')
    max_tp = sincronizar_sequence_com_max(cursor_web, tabelas_web['titulo_pagar'], 'tp_id', sequence_tp)
    print(f"[OK] Sequence titulo_pagar: {sequence_tp} (sincronizada com max={max_tp}).")

    codigos = iter(reservar_valores_sequence(cursor_web, sequence_tp, len(titulos_giv)))
    registros = []
    mapa_tp = {}
    erros_detalhe = []
    for titulo in titulos_giv:
        tp_id = next(codigos)
        chave = (
            titulo.get('cd_empresa'),
            titulo.get('nr_titulo'),
            titulo.get('nr_parcela'),
            titulo.get('cd_fornecedor'),
            serie_doc(titulo.get('serie')),
        )
        motivos = []
        cd_fornecedor = mapas['fornecedor'].get(titulo.get('cd_fornecedor'))
        if cd_fornecedor is None:
            motivos.append(f"fornecedor GIV {titulo.get('cd_fornecedor')} sem de/para")
        if motivos:
            registrar_erro_validacao(erros_detalhe, 'titulo_pagar', f"titulo={titulo.get('nr_titulo')}/{titulo.get('nr_parcela')}", '; '.join(motivos))
            continue
        mapa_tp[chave] = tp_id
        status = map_status_titulo_pagar(titulo.get('id_situacao'), titulo.get('vl_titulo'), titulo.get('vl_pago'))
        obs = limpar_valor(titulo.get('obs'))
        registros.append(limpar_registro({
            'tp_id': tp_id,
            'tenant_id': tenant_id,
            'id_status': status,
            'cd_empresa': cd_empresa,
            'nr_titulo': titulo.get('nr_titulo'),
            'nr_parcela': titulo.get('nr_parcela'),
            'nr_parcela_original': None,
            'cd_condicao_pagto': mapas['condicao_pagamento'].get(titulo.get('cd_condicao_pagto')),
            'cd_conta': mapas.get('banco_conta', {}).get(titulo.get('cd_conta')),
            'cd_forma_pagamento': mapas['forma_pagamento'].get(titulo.get('cd_forma_pagamento')) or mapas.get('forma_pagamento_padrao'),
            'cd_fornecedor': cd_fornecedor,
            'cd_tipo_despesa': titulo.get('cd_tipo_despesa'),
            'nr_cheque': str(titulo.get('nr_cheque')) if titulo.get('nr_cheque') is not None else None,
            'observacao': obs,
            'dt_digitacao': valor_data_ou_agora(titulo.get('dt_digitacao')),
            'dt_emissao': titulo.get('dt_emissao'),
            'dt_vencto': titulo.get('dt_vencto'),
            'dt_pagto': titulo.get('dt_pagto'),
            'dt_agendamento_pagto': titulo.get('dt_agendamento'),
            'vl_titulo': valor_decimal_ou_zero(titulo.get('vl_titulo')),
            'vl_acrescimo': valor_decimal_ou_zero(titulo.get('vl_acrescimo')),
            'vl_desconto': valor_decimal_ou_zero(titulo.get('vl_desconto')),
            'vl_pago': valor_decimal_ou_zero(titulo.get('vl_pago')),
            'cd_usuario_cancelamento': None,
            'dt_cancelamento': titulo.get('dt_cancelamento'),
            'motivo_cancelamento': (obs or 'Cancelado no GIV') if status == 'C' else None,
            'id_conferido': valor_flag(titulo.get('id_conferido'), 'N'),
            'cd_usuario_conferido': None,
            'dt_conferido': None,
            'id_previsao': valor_flag(titulo.get('id_previsao'), 'N'),
            'serie': serie_doc(titulo.get('serie')),
            'nf_id_entrada': mapas.get('nf_entrada', {}).get((titulo.get('cd_empresa'), titulo.get('nr_titulo'), serie_doc(titulo.get('serie')), titulo.get('cd_fornecedor'))),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['titulo_pagar'], registros, 'titulo_pagar')
    inseridos, erros_insert, erros_insert_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['titulo_pagar'],
        registros,
        'tp_id',
        'sp_titulo_pagar'
    )
    erros_detalhe.extend(erros_insert_detalhe)
    mapa_tp = remover_mapa_por_codigos_erro(mapa_tp, erros_insert_detalhe, 'tp_id')

    historicos = []
    erros_hist = []
    sequence_tph = buscar_sequence_coluna_web(cursor_web, tabelas_web['titulo_pagar_historico'], 'tph_id')
    sincronizar_sequence_com_max(cursor_web, tabelas_web['titulo_pagar_historico'], 'tph_id', sequence_tph)
    codigos_hist = iter(reservar_valores_sequence(cursor_web, sequence_tph, len(historicos_giv)))
    for hist in historicos_giv:
        tph_id = next(codigos_hist)
        chave = (
            hist.get('cd_empresa'),
            hist.get('nr_titulo'),
            hist.get('nr_parcela'),
            hist.get('cd_fornecedor'),
            serie_doc(hist.get('serie')),
        )
        tp_id = mapa_tp.get(chave)
        if tp_id is None:
            registrar_erro_validacao(erros_hist, 'titulo_pagar_historico', f"titulo={hist.get('nr_titulo')}/{hist.get('nr_parcela')}", 'titulo pai sem de/para')
            continue
        nm_coluna = 'historico'
        anterior = '0'
        novo = '0'
        if hist.get('dt_vencto_anterior') != hist.get('dt_vencto_atual'):
            nm_coluna = 'dt_vencto'
            anterior = str(hist.get('dt_vencto_anterior') or '0')
            novo = str(hist.get('dt_vencto_atual') or '0')
        elif 'LIQUID' in (chave_texto(hist.get('historico')) or ''):
            nm_coluna = 'id_status'
            anterior = 'A'
            novo = 'L'
        historicos.append(limpar_registro({
            'tph_id': tph_id,
            'tenant_id': tenant_id,
            'tp_id': tp_id,
            'cd_usuario': mapas['usuario'].get(hist.get('cd_usuario')),
            'dt_historico': valor_data_ou_agora(hist.get('dt_historico')),
            'ds_historico': limpar_valor(hist.get('historico')) or 'Historico GIV',
            'nm_coluna': nm_coluna,
            'vl_coluna_anterior': anterior,
            'vl_coluna_novo': novo,
            'tp_acao': map_tp_acao_historico(hist.get('tp_historico'), hist.get('historico'), hist.get('dt_vencto_anterior'), hist.get('dt_vencto_atual')),
        }))

    aplicar_limites_texto_web(cursor_web, tabelas_web['titulo_pagar_historico'], historicos, 'titulo_pagar_historico')
    inseridos_hist, erros_insert_hist, erros_insert_hist_detalhe = inserir_registros_web(
        cursor_web,
        tabelas_web['titulo_pagar_historico'],
        historicos,
        'tph_id',
        'sp_titulo_pagar_hist'
    )
    erros_hist.extend(erros_insert_hist_detalhe)
    return mapa_tp, [
        {'tabela': 'titulo_pagar', 'lidos': len(titulos_giv), 'inseridos': inseridos, 'existentes': 0, 'erros': len(erros_detalhe), 'erros_detalhe': erros_detalhe},
        {'tabela': 'titulo_pagar_historico', 'lidos': len(historicos_giv), 'inseridos': inseridos_hist, 'existentes': 0, 'erros': len(erros_hist), 'erros_detalhe': erros_hist},
    ]


def normalizar_cnpj_web(valor):
    """Normaliza CNPJ para 14 digitos; valores invalidos viram None."""
    digitos = somente_digitos(valor)
    if digitos and len(digitos) == 13:
        digitos = digitos.zfill(14)
    if digitos and len(digitos) == 14:
        return digitos
    return None


def perguntar_texto(rotulo, padrao=None, obrigatorio=False, secreto=False):
    """Pergunta um texto no console respeitando padrao e obrigatoriedade."""
    while True:
        sufixo = f" [{padrao}]" if padrao not in (None, '') else ""
        prompt = f"{rotulo}{sufixo}: "
        valor = getpass.getpass(prompt) if secreto and sys.stdin.isatty() else input(prompt)
        valor = limpar_valor(valor)
        if valor is None and padrao not in (None, ''):
            valor = padrao
        if valor is None and obrigatorio:
            print("[ERRO] Campo obrigatorio.")
            continue
        return valor


def perguntar_inteiro(rotulo, padrao=None, obrigatorio=False):
    """Pergunta um inteiro no console."""
    while True:
        valor = perguntar_texto(rotulo, str(padrao) if padrao is not None else None, obrigatorio)
        if valor is None:
            return None
        try:
            return int(valor)
        except ValueError:
            print("[ERRO] Informe um numero inteiro.")


def solicitar_dias_licenca_empresa():
    dias = perguntar_inteiro("Dias de licenca da empresa", DIAS_LICENCA_EMPRESA_PADRAO, True)
    return dias or DIAS_LICENCA_EMPRESA_PADRAO


def solicitar_dados_admin_empresa():
    print()
    print("[EMPRESA] Usuario administrador inicial")
    return {
        'admin_nome': perguntar_texto("Nome do administrador", ADMIN_NOME_PADRAO, True),
        'admin_login': perguntar_texto("Login do administrador", ADMIN_LOGIN_PADRAO, True),
        'admin_senha': perguntar_texto("Senha do administrador", ADMIN_SENHA_PADRAO, True, secreto=True),
    }


def solicitar_dados_nova_empresa_manual():
    print()
    print("[NOVA EMPRESA] Informe os dados principais.")
    nm_empresa = perguntar_texto("Razao social / nome da empresa", obrigatorio=True)
    nm_fantasia = perguntar_texto("Nome fantasia", nm_empresa, True)
    nm_reduzido = perguntar_texto("Nome reduzido", (nm_fantasia or nm_empresa)[:10], False)
    dados = {
        'tenant_id': perguntar_inteiro("TENANT_ID Web que sera vinculado a empresa", obrigatorio=True),
        'nm_empresa': nm_empresa,
        'nm_fantasia': nm_fantasia,
        'nm_reduzido': nm_reduzido,
        'cidade_entrada': perguntar_texto("Cidade Web (codigo IBGE ou nome)", obrigatorio=True),
        'cep': normalizar_cep(perguntar_texto("CEP (8 digitos, opcional)", None, False)),
        'endereco': perguntar_texto("Endereco", "S/N", True),
        'ds_numero': perguntar_texto("Numero", "S/N", True),
        'bairro': perguntar_texto("Bairro", "CENTRO", False),
        'cnpj': normalizar_cnpj_web(perguntar_texto("CNPJ (somente numeros, opcional)", None, False)),
        'inscricao_estadual': perguntar_texto("Inscricao estadual", None, False),
        'inscricao_municipal': normalizar_codigo_fiscal(perguntar_texto("Inscricao municipal", None, False), 9),
        'cnae': normalizar_codigo_fiscal(perguntar_texto("CNAE", None, False), 7),
        'fone': normalizar_celular_web(perguntar_texto("Fone", None, False)),
        'celular': normalizar_celular_web(perguntar_texto("Celular", None, False)),
        'email': perguntar_texto("Email", None, False),
        'dias_licenca': solicitar_dias_licenca_empresa(),
    }
    dados.update(solicitar_dados_admin_empresa())
    return dados


def solicitar_cd_empresa_giv_origem():
    return perguntar_inteiro("CD_EMPRESA do GIV para importar", 1, True)


def buscar_empresa_giv_por_codigo(cursor_giv, cd_empresa_giv):
    cursor_giv.execute("SELECT * FROM empresa WHERE cd_empresa = ?", (cd_empresa_giv,))
    row = cursor_giv.fetchone()
    if not row:
        raise RuntimeError(f"Empresa GIV cd_empresa={cd_empresa_giv} nao encontrada.")
    colunas = [col[0].lower() for col in cursor_giv.description]
    return dict(zip(colunas, row))


def montar_dados_empresa_giv(registro_giv, cd_cidade_web, cep_web):
    nm_empresa = limpar_valor(registro_giv.get('nm_empresa')) or f"EMPRESA GIV {registro_giv.get('cd_empresa')}"
    nm_fantasia = limpar_valor(registro_giv.get('nm_fantasia')) or nm_empresa
    print()
    print(f"[EMPRESA GIV] Encontrada: {registro_giv.get('cd_empresa')} - {nm_empresa}")
    dados = {
        'nm_empresa': nm_empresa,
        'nm_fantasia': nm_fantasia,
        'nm_reduzido': limpar_valor(registro_giv.get('nm_reduzido')) or (nm_fantasia or nm_empresa)[:10],
        'cd_cidade': cd_cidade_web,
        'cep': cep_web,
        'endereco': limpar_valor(registro_giv.get('endereco')) or 'S/N',
        'ds_numero': limpar_valor(registro_giv.get('ds_numero')) or 'S/N',
        'bairro': limpar_valor(registro_giv.get('bairro')) or 'CENTRO',
        'cnpj': normalizar_cnpj_web(registro_giv.get('cgc') or registro_giv.get('cnpj')),
        'inscricao_estadual': limpar_valor(registro_giv.get('insc_estadual') or registro_giv.get('inscricao_estadual')),
        'inscricao_municipal': normalizar_codigo_fiscal(
            registro_giv.get('insc_municipal') or registro_giv.get('inscricao_municipal'),
            9
        ),
        'cnae': normalizar_codigo_fiscal(registro_giv.get('cnae'), 7),
        'fone': normalizar_celular_web(registro_giv.get('fone')),
        'celular': normalizar_celular_web(registro_giv.get('celular')),
        'email': limpar_valor(registro_giv.get('email')),
        'dias_licenca': solicitar_dias_licenca_empresa(),
    }
    dados.update(solicitar_dados_admin_empresa())
    return dados


def cidade_web_por_codigo(cursor_web, cd_cidade):
    cursor_web.execute("SELECT cd_cidade, nm_cidade FROM cidade WHERE cd_cidade = %s", (cd_cidade,))
    return cursor_web.fetchone()


def resolver_cd_cidade_web_por_entrada(cursor_web, entrada):
    entrada = limpar_valor(entrada)
    if entrada is None:
        return None
    if somente_digitos(entrada) == str(entrada).strip():
        cd_cidade = int(entrada)
        row = cidade_web_por_codigo(cursor_web, cd_cidade)
        if row:
            return row[0]
        return None

    chave = normalizar_nome_cidade(entrada)
    cursor_web.execute("SELECT cd_cidade, nm_cidade FROM cidade")
    encontrados = []
    for cd_cidade, nm_cidade in cursor_web.fetchall():
        if normalizar_nome_cidade(nm_cidade) == chave:
            encontrados.append((cd_cidade, nm_cidade))
    if len(encontrados) == 1:
        return encontrados[0][0]
    if len(encontrados) > 1:
        print("[AVISO] Mais de uma cidade encontrada com esse nome. Informe o codigo IBGE.")
    return None


def solicitar_cd_cidade_web_manual(cursor_web, entrada_inicial=None):
    entrada = entrada_inicial
    while True:
        if entrada is None:
            entrada = perguntar_texto("Cidade Web (codigo IBGE ou nome)", obrigatorio=True)
        cd_cidade = resolver_cd_cidade_web_por_entrada(cursor_web, entrada)
        if cd_cidade is not None:
            return cd_cidade
        print("[ERRO] Cidade nao encontrada no Web. Use o codigo IBGE existente na tabela cidade.")
        entrada = None


def validar_cep_existente_web(cursor_web, cep, contexto):
    cep = normalizar_cep(cep)
    if not cep:
        return None
    cursor_web.execute("SELECT 1 FROM cep WHERE cep = %s", (cep,))
    if cursor_web.fetchone():
        return cep
    print(f"[AVISO] CEP {cep} nao existe no Web; {contexto} sera gravada sem CEP.")
    return None


def resolver_tabelas_empresa_base(cursor_web):
    nomes = {
        'tenant': 'tenant',
        'empresa': 'empresa',
        'funcao': 'funcao',
        'usuario': 'usuario',
        'unidade': 'unidade',
        'cor': 'cor',
        'tamanho': 'tamanho',
        'marca': 'marca',
        'colecao': 'colecao',
        'departamento': 'departamento',
        'grupo': 'grupo',
        'grade': 'grade',
        'tabela_preco': 'tabela_preco',
        'situacao_tributaria': 'situacao_tributaria',
        'condicao_pagamento': 'condicao_pagamento',
        'condicao_pagamento_forma': 'condicao_pagamento_forma',
        'forma_pagamento': 'forma_pagamento',
        'operacao_estoque': 'operacao_estoque',
        'local_estoque': 'local_estoque',
        'caixa': 'caixa',
        'banco': 'banco',
        'banco_conta': 'banco_conta',
        'categoria_cliente': 'categoria_cliente',
        'cliente': 'cliente',
        'sequencial': 'sequencial',
        'sequencial_nota_fiscal': 'sequencial_nota_fiscal',
        'tenant_sequencial': 'tenant_sequencial',
        'tenant_sequencial_empresa': 'tenant_sequencial_empresa',
        'sys_config_values': 'sys_config_values',
    }
    return {chave: resolver_tabela_web(cursor_web, nome) for chave, nome in nomes.items()}


EMPRESA_BASE_SEQUENCES = (
    ('tenant', 'id'),
    ('empresa', 'cd_empresa'),
    ('usuario', 'cd_usuario'),
    ('unidade', 'cd_unidade'),
    ('cor', 'cd_cor'),
    ('tamanho', 'cd_tamanho'),
    ('marca', 'cd_marca'),
    ('colecao', 'cd_colecao'),
    ('departamento', 'cd_departamento'),
    ('grupo', 'cd_grupo'),
    ('grade', 'cd_grade'),
    ('tabela_preco', 'cd_tabela_preco'),
    ('condicao_pagamento', 'cd_condicao_pagto'),
    ('forma_pagamento', 'cd_forma_pagto'),
    ('operacao_estoque', 'cd_operacao_estoque'),
    ('caixa', 'cd_caixa'),
    ('banco', 'cd_banco'),
    ('banco_conta', 'cd_conta'),
    ('categoria_cliente', 'cd_categoria'),
    ('cliente', 'cd_cliente'),
)


def sincronizar_sequences_empresa_base(cursor_web, tabelas_web):
    for chave, coluna in EMPRESA_BASE_SEQUENCES:
        tabela = tabelas_web.get(chave)
        if not tabela:
            continue
        sequence = buscar_sequence_coluna_web(cursor_web, tabela, coluna)
        maximo = sincronizar_sequence_com_max(cursor_web, tabela, coluna, sequence)
        print(f"[OK] Sequence {chave}: {sequence} (sincronizada com max={maximo}).")


def capturar_estado_reversao_empresa_base(cursor_web, tabelas_web):
    estado = {
        'tipo': 'empresa_base',
        'tenant_id': None,
        'cd_empresa': None,
        'capturado_em': datetime.datetime.now().isoformat(timespec='seconds'),
        'itens': {},
    }
    for chave, coluna in EMPRESA_BASE_SEQUENCES:
        tabela = tabelas_web.get(chave)
        if tabela:
            estado['itens'][chave] = item_reversao(cursor_web, tabela, coluna, coluna)
    return estado


def inserir_empresa_base_retornando(cursor_web, tabelas_web, chave_tabela, registro, coluna_retorno):
    tabela = tabelas_web[chave_tabela]
    registro = limpar_registro(dict(registro))
    aplicar_limites_texto_registro(registro, buscar_limites_texto_web(cursor_web, tabela))
    return inserir_registro_web_retornando(cursor_web, tabela, registro, coluna_retorno)


def inserir_empresa_base_sem_retorno(cursor_web, tabelas_web, chave_tabela, registros, contexto):
    tabela = tabelas_web[chave_tabela]
    registros = [limpar_registro(dict(registro)) for registro in registros]
    aplicar_limites_texto_web(cursor_web, tabela, registros, contexto)
    inserir_registros_multi_sem_savepoint(cursor_web, tabela, registros)
    return len(registros)


def buscar_existente_empresa_base(cursor_web, tabela, colunas_chave, registro, coluna_retorno=None):
    condicoes = []
    params = []
    for coluna in colunas_chave:
        valor = registro.get(coluna)
        if valor is None:
            condicoes.append(f"{quote_identificador(coluna)} IS NULL")
        else:
            condicoes.append(f"{quote_identificador(coluna)} = %s")
            params.append(valor)
    if not condicoes:
        return None
    coluna_select = quote_identificador(coluna_retorno) if coluna_retorno else "1"
    cursor_web.execute(
        f"""
        SELECT {coluna_select}
          FROM {tabela}
         WHERE {' AND '.join(condicoes)}
         LIMIT 1
        """,
        params
    )
    return cursor_web.fetchone()


def inserir_empresa_base_unico_retornando(
    cursor_web,
    tabelas_web,
    chave_tabela,
    registro,
    coluna_retorno,
    colunas_chave
):
    tabela = tabelas_web[chave_tabela]
    registro = limpar_registro(dict(registro))
    aplicar_limites_texto_registro(registro, buscar_limites_texto_web(cursor_web, tabela))
    existente = buscar_existente_empresa_base(cursor_web, tabela, colunas_chave, registro, coluna_retorno)
    if existente:
        return existente[0], False
    return inserir_registro_web_retornando(cursor_web, tabela, registro, coluna_retorno), True


def inserir_empresa_base_sem_retorno_unico(cursor_web, tabelas_web, chave_tabela, registros, contexto, colunas_chave):
    tabela = tabelas_web[chave_tabela]
    limites = buscar_limites_texto_web(cursor_web, tabela)
    inseridos = 0
    existentes = 0
    for registro in registros:
        registro = limpar_registro(dict(registro))
        aplicar_limites_texto_registro(registro, limites)
        if buscar_existente_empresa_base(cursor_web, tabela, colunas_chave, registro):
            existentes += 1
            continue
        inserir_registro_web_sem_savepoint(cursor_web, tabela, registro)
        inseridos += 1
    if existentes:
        print(f"[OK] {contexto}: {existentes} registro(s) reaproveitados no tenant/empresa informado.")
    return inseridos, existentes


def validar_tenant_empresa_base(cursor_web, tabelas_web, tenant_id):
    """Garante que a empresa sera vinculada a um tenant existente no Web."""
    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        raise RuntimeError("tenant_id da empresa deve ser numerico.")
    cursor_web.execute(
        f"SELECT {quote_identificador('name')} FROM {tabelas_web['tenant']} WHERE {quote_identificador('id')} = %s",
        (tenant_id,)
    )
    row = cursor_web.fetchone()
    if not row:
        raise RuntimeError(
            f"Tenant Web id={tenant_id} nao encontrado. Crie o tenant pelo botao 'Criar tenant' "
            "ou informe um tenant_id existente."
        )
    print(f"[OK] Tenant Web validado: tenant_id={tenant_id} ({row[0]}).")
    return tenant_id


def buscar_ou_criar_funcao_admin(cursor_web, tabela_funcao):
    cursor_web.execute(
        f"SELECT id FROM {tabela_funcao} WHERE UPPER(nome) = 'ADMINISTRADOR' ORDER BY id LIMIT 1"
    )
    row = cursor_web.fetchone()
    if row:
        return row[0], False
    funcao_id = inserir_registro_web_retornando(
        cursor_web,
        tabela_funcao,
        {
            'nome': 'ADMINISTRADOR',
            'descricao': 'Administrador da Empresa',
        },
        'id'
    )
    return funcao_id, True


def adicionar_resumo_empresa(resumos, tabela, lidos, inseridos, existentes=0, erros=0, detalhes=None):
    resumos.append({
        'tabela': tabela,
        'lidos': lidos,
        'inseridos': inseridos,
        'existentes': existentes,
        'erros': erros,
        'erros_detalhe': detalhes or [],
    })


def inserir_situacoes_tributarias_base(cursor_web, tabelas_web, tenant_id, cd_empresa):
    tabela = tabelas_web['situacao_tributaria']
    registros = [
        {
            'cd_situacao_tributaria': 102,
            'ds_situacao_tributaria': 'TRIBUTACAO SN SEM PERMISSAO DE CREDITO',
            'id_simples_nacional': 'S',
            'tenant_id': tenant_id,
            'cd_empresa': cd_empresa,
        },
        {
            'cd_situacao_tributaria': 500,
            'ds_situacao_tributaria': 'ICMS COBRADO ANTERIORMENTE POR SUBSTITUICAO TRIBUTARIA',
            'id_simples_nacional': 'S',
            'tenant_id': tenant_id,
            'cd_empresa': cd_empresa,
        },
        {
            'cd_situacao_tributaria': 900,
            'ds_situacao_tributaria': 'OUTROS',
            'id_simples_nacional': 'S',
            'tenant_id': tenant_id,
            'cd_empresa': cd_empresa,
        },
    ]
    inseridos = 0
    existentes = 0
    detalhes = []
    limites = buscar_limites_texto_web(cursor_web, tabela)
    for registro in registros:
        codigo = registro['cd_situacao_tributaria']
        cursor_web.execute(
            f"SELECT tenant_id, cd_empresa FROM {tabela} WHERE cd_situacao_tributaria = %s",
            (codigo,)
        )
        if cursor_web.fetchone():
            existentes += 1
            detalhes.append(
                f"cd_situacao_tributaria={codigo}: codigo ja existe globalmente no Web; schema nao permite duplicar por tenant"
            )
            continue
        registro = limpar_registro(registro)
        aplicar_limites_texto_registro(registro, limites)
        inserir_registro_web_sem_savepoint(cursor_web, tabela, registro)
        inseridos += 1
    return {
        'tabela': 'situacao_tributaria',
        'lidos': len(registros),
        'inseridos': inseridos,
        'existentes': existentes,
        'erros': 0,
        'erros_detalhe': detalhes,
        'motivo_reaproveitamento': 'codigo fiscal ja existia globalmente no Web',
    }


def criar_empresa_base_web(cursor_web, tabelas_web, dados):
    print()
    print("[...] Sincronizando sequences da criacao de empresa...")
    sincronizar_sequences_empresa_base(cursor_web, tabelas_web)

    resumos = []
    data_licenca = datetime.date.today() + datetime.timedelta(days=int(dados.get('dias_licenca') or DIAS_LICENCA_EMPRESA_PADRAO))

    print()
    print("[...] Criando empresa no Web vinculada ao tenant informado...")
    tenant_id = validar_tenant_empresa_base(cursor_web, tabelas_web, dados.get('tenant_id'))

    cd_empresa = inserir_empresa_base_retornando(
        cursor_web,
        tabelas_web,
        'empresa',
        {
            'id_status': 'A',
            'nm_empresa': dados['nm_empresa'],
            'nm_fantasia': dados['nm_fantasia'],
            'nm_reduzido': dados.get('nm_reduzido'),
            'cd_cidade': dados['cd_cidade'],
            'cep': dados.get('cep'),
            'endereco': dados.get('endereco') or 'S/N',
            'ds_numero': dados.get('ds_numero') or 'S/N',
            'bairro': dados.get('bairro'),
            'cnpj': dados.get('cnpj'),
            'inscricao_estadual': dados.get('inscricao_estadual'),
            'inscricao_municipal': dados.get('inscricao_municipal'),
            'cnae': dados.get('cnae'),
            'fone': dados.get('fone'),
            'celular': dados.get('celular'),
            'email': dados.get('email'),
            'tenant_id': tenant_id,
            'data_vencimento_licenca': data_licenca,
        },
        'cd_empresa'
    )
    adicionar_resumo_empresa(resumos, 'empresa', 1, 1)

    funcao_id, criou_funcao = buscar_ou_criar_funcao_admin(cursor_web, tabelas_web['funcao'])
    adicionar_resumo_empresa(resumos, 'funcao', 1, 1 if criou_funcao else 0, 0 if criou_funcao else 1)

    cd_usuario = inserir_empresa_base_retornando(
        cursor_web,
        tabelas_web,
        'usuario',
        {
            'id_status': 'A',
            'cd_empresa': cd_empresa,
            'nm_usuario': dados.get('admin_nome') or ADMIN_NOME_PADRAO,
            'nm_login': dados.get('admin_login') or ADMIN_LOGIN_PADRAO,
            'senha': dados.get('admin_senha') or ADMIN_SENHA_PADRAO,
            'cd_cidade': dados['cd_cidade'],
            'cep': dados.get('cep'),
            'endereco': dados.get('endereco'),
            'bairro': dados.get('bairro'),
            'ds_numero': dados.get('ds_numero') or 'S/N',
            'email': dados.get('email'),
            'tenant_id': tenant_id,
            'funcao_id': funcao_id,
        },
        'cd_usuario'
    )
    adicionar_resumo_empresa(resumos, 'usuario_admin', 1, 1)

    print("[...] Inserindo dados minimos do tenant/empresa...")
    lotes_base = [
        ('unidade', 'unidade', [
            {'ds_unidade': 'UN', 'ds_unidade_longa': 'UNIDADE', 'tenant_id': tenant_id},
            {'ds_unidade': 'PC', 'ds_unidade_longa': 'PECA', 'tenant_id': tenant_id},
            {'ds_unidade': 'KG', 'ds_unidade_longa': 'QUILOGRAMA', 'tenant_id': tenant_id},
            {'ds_unidade': 'MT', 'ds_unidade_longa': 'METRO', 'tenant_id': tenant_id},
            {'ds_unidade': 'CX', 'ds_unidade_longa': 'CAIXA', 'tenant_id': tenant_id},
        ]),
        ('cor', 'cor', [
            {'ds_cor': 'UNICA', 'tenant_id': tenant_id},
            {'ds_cor': 'BRANCO', 'tenant_id': tenant_id},
            {'ds_cor': 'PRETO', 'tenant_id': tenant_id},
        ]),
        ('tamanho', 'tamanho', [
            {'ds_tamanho': 'UNICO', 'tenant_id': tenant_id},
            {'ds_tamanho': 'P', 'tenant_id': tenant_id},
            {'ds_tamanho': 'M', 'tenant_id': tenant_id},
            {'ds_tamanho': 'G', 'tenant_id': tenant_id},
            {'ds_tamanho': 'GG', 'tenant_id': tenant_id},
        ]),
        ('marca', 'marca', [
            {'ds_marca': 'SEM MARCA', 'pr_comissao': Decimal('0'), 'tenant_id': tenant_id},
        ]),
        ('colecao', 'colecao', [
            {'ds_colecao': 'GERAL', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa},
        ]),
        ('departamento', 'departamento', [
            {'ds_departamento': 'GERAL', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa},
        ]),
        ('grupo', 'grupo', [
            {'ds_grupo': 'GERAL', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa},
        ]),
        ('grade', 'grade', [
            {'ds_grade': 'GERAL', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa},
        ]),
        ('tabela_preco', 'tabela_preco', [
            {'ds_tabela_preco': 'TABELA PADRAO', 'id_status': 'A', 'pr_margem_lucro': Decimal('0'), 'tenant_id': tenant_id},
        ]),
        ('operacao_estoque', 'operacao_estoque', [
            {'ds_operacao_estoque': 'ENTRADA', 'id_fisico': '+', 'id_disponivel': '+', 'tenant_id': tenant_id},
            {'ds_operacao_estoque': 'SAIDA', 'id_fisico': '-', 'id_disponivel': '-', 'tenant_id': tenant_id},
            {'ds_operacao_estoque': 'AJUSTE POSITIVO', 'id_fisico': '+', 'id_disponivel': '+', 'tenant_id': tenant_id},
            {'ds_operacao_estoque': 'AJUSTE NEGATIVO', 'id_fisico': '-', 'id_disponivel': '-', 'tenant_id': tenant_id},
        ]),
        ('local_estoque', 'local_estoque', [
            {'cd_empresa': cd_empresa, 'cd_local_estoque': 1, 'ds_local_estoque': 'ESTOQUE PRINCIPAL', 'tenant_id': tenant_id},
        ]),
        ('caixa', 'caixa', [
            {'ds_caixa': 'CAIXA 01', 'cd_empresa': cd_empresa, 'tenant_id': tenant_id},
        ]),
        ('categoria_cliente', 'categoria_cliente', [
            {'ds_categoria': 'GERAL', 'tenant_id': tenant_id},
        ]),
        ('sequencial', 'sequencial', [
            {'tenant_id': tenant_id, 'cd_empresa': cd_empresa, 'tabela': 'email_envio', 'nr_sequencial': 0},
        ]),
        ('sequencial_nota_fiscal', 'sequencial_nota_fiscal', [
            {'tenant_id': tenant_id, 'cd_empresa': cd_empresa, 'serie': '1', 'ultimo_nr': 0},
        ]),
        ('tenant_sequencial', 'tenant_sequencial', [
            {'tenant_id': tenant_id, 'nm_tabela': 'pedido_compra', 'nr_sequencial': 0},
            {'tenant_id': tenant_id, 'nm_tabela': 'titulo_pagar', 'nr_sequencial': 0},
            {'tenant_id': tenant_id, 'nm_tabela': 'titulo_receber', 'nr_sequencial': 0},
        ]),
        ('tenant_sequencial_empresa', 'tenant_sequencial_empresa', [
            {'tenant_id': tenant_id, 'cd_empresa': cd_empresa, 'nm_tabela': 'nota_fiscal_entrada', 'nr_sequencial': 0},
        ]),
        ('sys_config_values', 'sys_config_values', [
            {'key': 'regime_empresa', 'scope_type': 'tenant', 'tenant_id': tenant_id, 'cd_empresa': None, 'value': 'SIMPLES_NACIONAL'},
            {'key': 'imprime_nfce_questiona', 'scope_type': 'empresa', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa, 'value': 'true'},
            {'key': 'utiliza_preco_atacado', 'scope_type': 'empresa', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa, 'value': 'N'},
            {'key': 'utiliza_sub_grupo', 'scope_type': 'empresa', 'tenant_id': tenant_id, 'cd_empresa': cd_empresa, 'value': 'N'},
        ]),
    ]
    chaves_unicas_base = {
        'unidade': ('tenant_id', 'ds_unidade'),
        'cor': ('tenant_id', 'ds_cor'),
        'tamanho': ('tenant_id', 'ds_tamanho'),
        'marca': ('tenant_id', 'ds_marca'),
        'tabela_preco': ('tenant_id', 'ds_tabela_preco'),
        'operacao_estoque': ('tenant_id', 'ds_operacao_estoque'),
        'categoria_cliente': ('tenant_id', 'ds_categoria'),
        'sequencial': ('tenant_id', 'cd_empresa', 'tabela'),
        'sequencial_nota_fiscal': ('tenant_id', 'cd_empresa', 'serie'),
        'tenant_sequencial': ('tenant_id', 'nm_tabela'),
        'tenant_sequencial_empresa': ('tenant_id', 'cd_empresa', 'nm_tabela'),
        'sys_config_values': ('key', 'scope_type', 'tenant_id', 'cd_empresa'),
    }
    for chave_tabela, nome_resumo, registros in lotes_base:
        colunas_chave = chaves_unicas_base.get(chave_tabela)
        if colunas_chave:
            inseridos, existentes = inserir_empresa_base_sem_retorno_unico(
                cursor_web,
                tabelas_web,
                chave_tabela,
                registros,
                nome_resumo,
                colunas_chave
            )
            adicionar_resumo_empresa(resumos, nome_resumo, len(registros), inseridos, existentes)
        else:
            inseridos = inserir_empresa_base_sem_retorno(cursor_web, tabelas_web, chave_tabela, registros, nome_resumo)
            adicionar_resumo_empresa(resumos, nome_resumo, len(registros), inseridos)

    # A tabela de preco criada acima so passa a valer como padrao depois de
    # registrada em sys_config_values no escopo da empresa (regra oficial do Web:
    # migration 20260715210000_tabela_preco_padrao_por_empresa).
    definir_tabela_preco_padrao_produto(cursor_web, tenant_id, cd_empresa)

    resumo_situacoes = inserir_situacoes_tributarias_base(cursor_web, tabelas_web, tenant_id, cd_empresa)
    resumos.append(resumo_situacoes)

    cd_condicao, inseriu_condicao = inserir_empresa_base_unico_retornando(
        cursor_web,
        tabelas_web,
        'condicao_pagamento',
        {
            'ds_condicao_pagto': 'A VISTA',
            'nr_parcelas': 1,
            'id_entrada': 'S',
            'id_status': 'A',
            'id_bloquear_crediario': 'N',
            'id_bloquear_entrega': 'N',
            'id_imprime_carne': 'N',
            'pr_comissao': Decimal('0'),
            'pr_desconto': Decimal('0'),
            'tp_condicao': 'V',
            'tx_juros': Decimal('0'),
            'tenant_id': tenant_id,
        },
        'cd_condicao_pagto',
        ('tenant_id', 'ds_condicao_pagto')
    )
    adicionar_resumo_empresa(resumos, 'condicao_pagamento', 1, 1 if inseriu_condicao else 0, 0 if inseriu_condicao else 1)

    formas = [
        {'ds_forma_pagto': 'DINHEIRO', 'id_movto_caixa': 'S', 'id_status': 'A', 'tenant_id': tenant_id},
        {'ds_forma_pagto': 'CARTAO CREDITO', 'id_movto_caixa': 'S', 'id_status': 'A', 'tenant_id': tenant_id},
        {'ds_forma_pagto': 'CARTAO DEBITO', 'id_movto_caixa': 'S', 'id_status': 'A', 'tenant_id': tenant_id},
        {'ds_forma_pagto': 'PIX', 'id_movto_caixa': 'S', 'id_status': 'A', 'tenant_id': tenant_id},
    ]
    cd_formas = []
    formas_inseridas = 0
    formas_existentes = 0
    for forma in formas:
        cd_forma, inseriu_forma = inserir_empresa_base_unico_retornando(
            cursor_web,
            tabelas_web,
            'forma_pagamento',
            forma,
            'cd_forma_pagto',
            ('tenant_id', 'ds_forma_pagto')
        )
        cd_formas.append(cd_forma)
        if inseriu_forma:
            formas_inseridas += 1
        else:
            formas_existentes += 1
    adicionar_resumo_empresa(resumos, 'forma_pagamento', len(formas), formas_inseridas, formas_existentes)

    vinculos = [
        {'cd_condicao_pagto': cd_condicao, 'cd_forma_pagto': cd_forma, 'tenant_id': tenant_id}
        for cd_forma in cd_formas
    ]
    if inseriu_condicao:
        vinculos_inseridos, vinculos_existentes = inserir_empresa_base_sem_retorno_unico(
            cursor_web,
            tabelas_web,
            'condicao_pagamento_forma',
            vinculos,
            'condicao_pagamento_forma',
            ('tenant_id', 'cd_condicao_pagto', 'cd_forma_pagto')
        )
    else:
        vinculos_inseridos = 0
        vinculos_existentes = len(vinculos)
        print("[OK] condicao_pagamento_forma: condicao padrao ja existia; vinculos existentes foram mantidos.")
    adicionar_resumo_empresa(resumos, 'condicao_pagamento_forma', len(vinculos), vinculos_inseridos, vinculos_existentes)

    cd_banco = inserir_empresa_base_retornando(
        cursor_web,
        tabelas_web,
        'banco',
        {
            'nm_banco': 'BANCO PRINCIPAL',
            'id_status': 'A',
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
        },
        'cd_banco'
    )
    adicionar_resumo_empresa(resumos, 'banco', 1, 1)

    inserir_empresa_base_retornando(
        cursor_web,
        tabelas_web,
        'banco_conta',
        {
            'cd_banco': cd_banco,
            'nr_conta': '00001',
            'ds_conta': 'CONTA PRINCIPAL',
            'vl_saldo': Decimal('0'),
            'vl_limite': Decimal('0'),
            'id_ativa': 'S',
            'cd_empresa': cd_empresa,
            'tenant_id': tenant_id,
        },
        'cd_conta'
    )
    adicionar_resumo_empresa(resumos, 'banco_conta', 1, 1)

    inserir_empresa_base_retornando(
        cursor_web,
        tabelas_web,
        'cliente',
        {
            'ativo': 'S',
            'cd_empresa': cd_empresa,
            'cd_cidade': dados['cd_cidade'],
            'cep': dados.get('cep'),
            'cd_usuario': cd_usuario,
            'nm_cliente': 'CONSUMIDOR FINAL',
            'nr_residencia': 'S/N',
            'tp_pessoa': 'F',
            'endereco': dados.get('endereco'),
            'bairro': dados.get('bairro'),
            'fone': dados.get('fone'),
            'celular': dados.get('celular'),
            'cd_categoria': None,
            'tenant_id': tenant_id,
            'email': dados.get('email'),
        },
        'cd_cliente'
    )
    adicionar_resumo_empresa(resumos, 'cliente_padrao', 1, 1)

    print(f"[OK] Empresa criada na transacao: tenant_id={tenant_id}, cd_empresa={cd_empresa}, admin={cd_usuario}.")
    return {
        'tenant_id': tenant_id,
        'cd_empresa': cd_empresa,
        'cd_usuario_admin': cd_usuario,
        'resumos': resumos,
    }


def preparar_dados_empresa_manual_com_web(cursor_web, dados):
    dados = dict(dados)
    dados['cd_cidade'] = solicitar_cd_cidade_web_manual(cursor_web, dados.pop('cidade_entrada', None))
    dados['cep'] = validar_cep_existente_web(cursor_web, dados.get('cep'), 'empresa')
    return dados


def preparar_dados_empresa_giv_com_web(cursor_giv, cursor_web, cd_empresa_giv):
    registro_giv = buscar_empresa_giv_por_codigo(cursor_giv, cd_empresa_giv)
    print("[...] Carregando cidades para mapear cidade da empresa GIV...")
    cidades_giv = buscar_cidades_giv(cursor_giv)
    cidades_web = buscar_cidades_web(cursor_web)
    cd_cidade_web = resolver_cidade_trab(
        registro_giv.get('cd_cidade'),
        cidades_giv,
        cidades_web,
        f"empresa GIV cd_empresa={cd_empresa_giv}"
    )
    if cd_cidade_web is None:
        print("[AVISO] Nao foi possivel mapear a cidade da empresa GIV automaticamente.")
        cd_cidade_fallback = limpar_valor(os.environ.get("CONVERTER_EMPRESA_GIV_CIDADE_FALLBACK"))
        cd_cidade_web = solicitar_cd_cidade_web_manual(cursor_web, cd_cidade_fallback)
    cep_web = validar_cep_existente_web(cursor_web, registro_giv.get('cep'), 'empresa GIV')
    return montar_dados_empresa_giv(registro_giv, cd_cidade_web, cep_web)


def main_criar_empresa(modo):
    print("=" * 60)
    print("  CRIACAO / IMPORTACAO DE EMPRESA - Web")
    print("=" * 60)

    conn_giv = None
    cursor_giv = None
    conn_web = None
    cursor_web = None

    cd_empresa_giv = None
    dados_manual = None
    tenant_id_empresa = None
    if modo == OPCAO_NOVA_EMPRESA:
        dados_manual = solicitar_dados_nova_empresa_manual()
    elif modo == OPCAO_EMPRESA_GIV:
        cd_empresa_giv = solicitar_cd_empresa_giv_origem()
        tenant_id_empresa = perguntar_inteiro("TENANT_ID Web que sera vinculado a empresa", obrigatorio=True)
    else:
        raise RuntimeError(f"Modo de empresa desconhecido: {modo}")

    try:
        if modo == OPCAO_EMPRESA_GIV:
            conn_giv = conectar_giv()
            cursor_giv = conn_giv.cursor()

        conn_web = conectar_web()
        cursor_web = conn_web.cursor()

        print()
        print("[...] Resolvendo tabelas Web da criacao de empresa...")
        tabelas_web = resolver_tabelas_empresa_base(cursor_web)
        print("[OK] Tabelas Web da criacao de empresa resolvidas.")

        estado_reversao = capturar_estado_reversao_empresa_base(cursor_web, tabelas_web)
        gui_progress_tabela('empresa')

        if modo == OPCAO_NOVA_EMPRESA:
            dados = preparar_dados_empresa_manual_com_web(cursor_web, dados_manual)
        else:
            dados = preparar_dados_empresa_giv_com_web(cursor_giv, cursor_web, cd_empresa_giv)
            dados['tenant_id'] = tenant_id_empresa

        resultado = criar_empresa_base_web(cursor_web, tabelas_web, dados)
        estado_reversao['tenant_id'] = resultado['tenant_id']
        estado_reversao['cd_empresa'] = resultado['cd_empresa']

        imprimir_resumo(resultado['resumos'])
        gravar_reverter_txt(estado_reversao, tabelas_web)
        print()
        print(f"[INFO] Use estes codigos nas proximas conversoes: tenant_id={resultado['tenant_id']} cd_empresa={resultado['cd_empresa']}")
        confirmar_transacao(conn_web)

    except Exception as e:
        print()
        print(f"[ERRO FATAL] {e}")
        if conn_web:
            print("[...] Realizando ROLLBACK automatico...")
            try:
                conn_web.rollback()
                print("[OK] ROLLBACK realizado com sucesso.")
            except Exception:
                print("[ERRO] Falha ao realizar rollback.")
        sys.exit(1)

    finally:
        if cursor_giv:
            cursor_giv.close()
        if cursor_web:
            cursor_web.close()
        if conn_giv:
            conn_giv.close()
        if conn_web:
            conn_web.close()
        print()
        print("[OK] Conexoes encerradas.")
        print("Fim da criacao de empresa.")



def main_cliente_antigo():
    print("=" * 60)
    print("  CONVERSOR DE DADOS - GIV -> Web")
    print("  Tabela: CLIENTE")
    print("=" * 60)
    print()

    # Solicitar tenant_id ao usuário
    tenant_id = input("Informe o TENANT_ID para os registros: ").strip()
    if not tenant_id:
        print("[ERRO] tenant_id eh obrigatorio.")
        sys.exit(1)

    # Tentar converter para inteiro se possível
    try:
        tenant_id = int(tenant_id)
    except ValueError:
        pass  # Mantém como string se não for numérico

    print()

    # ---- Conexões ----
    conn_giv = conectar_giv()
    conn_web = conectar_web()

    cursor_giv = conn_giv.cursor()
    cursor_web = conn_web.cursor()

    try:
        # ---- Carregar tabelas auxiliares de cidades ----
        print()
        print("[...] Carregando tabelas de cidades para mapeamento...")
        cidades_giv = buscar_cidades_giv(cursor_giv)
        cidades_web = buscar_cidades_web(cursor_web)

        print()
        print("[INFO] Cliente: registros existentes no Web nao serao usados para pular a importacao.")

        sequence_cliente = buscar_sequence_coluna_web(cursor_web, 'cliente', 'cd_cliente')
        max_cliente = sincronizar_sequence_com_max(
            cursor_web,
            'cliente',
            'cd_cliente',
            sequence_cliente
        )
        print(f"[OK] Sequence de cliente: {sequence_cliente} (sincronizada com max={max_cliente}).")

        # ---- Buscar clientes do GIV ----
        print()
        print("[...] Buscando clientes do banco GIV...")
        clientes_giv = buscar_clientes_giv(cursor_giv)
        print(f"[OK] {len(clientes_giv)} clientes encontrados no GIV.")

        if len(clientes_giv) == 0:
            print("[AVISO] Nenhum cliente encontrado. Nada a converter.")
            return

        # ---- Converter registros ----
        print()
        print("[...] Convertendo registros...")
        clientes_web = []
        pulados = 0
        codigos_cliente = iter(reservar_valores_sequence(cursor_web, sequence_cliente, len(clientes_giv)))
        for reg in clientes_giv:
            cd_cliente_web = next(codigos_cliente)
            convertido = converter_cliente(reg, tenant_id, cidades_giv, cidades_web, cd_cliente_web, CD_EMPRESA_PADRAO)
            clientes_web.append(convertido)
        print(f"[OK] {len(clientes_web)} registros para inserir (nenhum cliente foi pulado por ja existir no Web).")

        if len(clientes_web) == 0:
            print("[AVISO] Todos os clientes ja existem no banco Web. Nada a inserir.")
            return

        # ---- Inserir no PostgreSQL (dentro da transacao) ----
        print()
        print("[...] Desabilitando checagem de FK temporariamente...")
        try:
            cursor_web.execute("SET session_replication_role = 'replica'")
            fk_desabilitada = True
            print("[OK] FK checks desabilitados.")
        except Exception:
            fk_desabilitada = False
            print("[AVISO] Nao foi possivel desabilitar FK. Registros com FK invalida serao pulados.")

        print("[...] Inserindo registros no banco Web (PostgreSQL)...")
        inseridos, erros, erros_detalhe = inserir_clientes_web(cursor_web, clientes_web)

        if fk_desabilitada:
            cursor_web.execute("SET session_replication_role = 'origin'")
            print("[OK] FK checks reabilitados.")

        print()
        print("=" * 60)
        print(f"  RESUMO DA CONVERSAO")
        print(f"  Total lidos do GIV:     {len(clientes_giv)}")
        print(f"  Total inseridos no Web: {inseridos}")
        print(f"  Pulados com erro:       {erros}")
        print("=" * 60)

        if erros > 0:
            print()
            print(f"  Detalhes dos {erros} erros:")
            for detalhe in erros_detalhe:
                print(f"    - {detalhe}")

        # ---- Decisao do usuario: COMMIT ou ROLLBACK ----
        print()
        print(">>> A transacao esta ABERTA no PostgreSQL. <<<")
        print(">>> Nenhum dado foi efetivado ainda.        <<<")
        print()

        while True:
            decisao = input("Deseja CONFIRMAR (commit) ou CANCELAR (rollback)? [C/R]: ").strip().upper()
            if decisao == 'C':
                conn_web.commit()
                print()
                print("[OK] COMMIT realizado! Dados efetivados no banco Web.")
                break
            elif decisao == 'R':
                conn_web.rollback()
                print()
                print("[OK] ROLLBACK realizado! Nenhum dado foi alterado no banco Web.")
                break
            else:
                print("Opção inválida. Digite 'C' para commit ou 'R' para rollback.")

    except Exception as e:
        print()
        print(f"[ERRO FATAL] {e}")
        print("[...] Realizando ROLLBACK automático...")
        try:
            conn_web.rollback()
            print("[OK] ROLLBACK realizado com sucesso.")
        except:
            print("[ERRO] Falha ao realizar rollback.")
        sys.exit(1)

    finally:
        # ---- Fechar conexões ----
        cursor_giv.close()
        cursor_web.close()
        conn_giv.close()
        conn_web.close()
        print()
        print("[OK] Conexões encerradas.")
        print("Fim da conversão.")


def motivo_reaproveitamento_resumo(resumo):
    """Descricao curta para registros lidos do GIV que nao viraram novo INSERT."""
    motivo = resumo.get('motivo_existentes')
    if motivo:
        return motivo

    tabela = str(resumo.get('tabela', '')).lower()
    if tabela in {'grupo', 'marca', 'cor', 'departamento', 'sub_grupo', 'tamanho', 'unidade', 'colecao'}:
        return "reaproveitado por descricao ja existente no mesmo tenant/empresa"
    if tabela in {'fornecedor', 'cliente'}:
        return "ja existia no Web ou foi tratado pela regra da tabela"
    return "ja existia/reaproveitado no Web conforme regra da tabela"


def imprimir_resumo(resumos):
    print()
    print("=" * 60)
    print("  RESUMO DA CONVERSAO")
    for resumo in resumos:
        lidos = int(resumo.get('lidos') or 0)
        inseridos = int(resumo.get('inseridos') or 0)
        existentes = int(resumo.get('existentes') or 0)
        erros = int(resumo.get('erros') or 0)
        nao_inseridos = max(lidos - inseridos, 0)
        sem_detalhe = max(nao_inseridos - existentes - erros, 0)

        print("-" * 60)
        print(f"  Tabela:                         {resumo.get('tabela')}")
        print(f"  Total lidos do GIV:             {lidos}")
        print(f"  Inseridos no Web:               {inseridos}")
        print(f"  Nao inseridos total:            {nao_inseridos}")
        print(f"  Reaproveitados/ja existentes:   {existentes}")
        print(f"  Nao inseridos com erro:         {erros}")
        if sem_detalhe:
            print(f"  Nao inseridos sem detalhe:      {sem_detalhe}")

    print("=" * 60)

    encontrou_detalhe = False
    for resumo in resumos:
        lidos = int(resumo.get('lidos') or 0)
        inseridos = int(resumo.get('inseridos') or 0)
        existentes = int(resumo.get('existentes') or 0)
        erros = int(resumo.get('erros') or 0)
        detalhes = resumo.get('erros_detalhe') or []
        nao_inseridos = max(lidos - inseridos, 0)
        sem_detalhe = max(nao_inseridos - existentes - erros, 0)

        if not existentes and not erros and not sem_detalhe:
            continue

        if not encontrou_detalhe:
            print()
            print("  DETALHES DO QUE NAO INSERIU")
            encontrou_detalhe = True

        print()
        print(f"  Tabela {resumo.get('tabela')}:")
        if existentes:
            print(f"    - {existentes} registro(s): {motivo_reaproveitamento_resumo(resumo)}.")
        if sem_detalhe:
            print(
                f"    - {sem_detalhe} registro(s): nao houve INSERT e a rotina nao retornou "
                "motivo individual; confira os avisos acima desta tabela."
            )
        if erros:
            print(f"    - {erros} registro(s) com erro:")
            if detalhes:
                for detalhe in detalhes:
                    print(f"      - {detalhe}")
            else:
                print("      - A rotina contou erro, mas nao retornou detalhe individual.")


def linha_unica(cursor, sql, params=None):
    cursor.execute(sql, params or [])
    return cursor.fetchone()


def valor_comparavel(valor, tipo):
    if tipo == 'count':
        return int(valor or 0)
    return valor_decimal_ou_zero(valor)


def comparar_total(nome, campos, giv_row, web_row):
    divergencias = []
    partes = []
    for indice, (campo, tipo) in enumerate(campos):
        valor_giv = valor_comparavel(giv_row[indice], tipo)
        valor_web = valor_comparavel(web_row[indice], tipo)
        partes.append(f"{campo}: GIV={valor_giv} Web={valor_web}")
        if valor_giv != valor_web:
            divergencias.append((campo, valor_giv, valor_web))

    if divergencias:
        print(f"  [DIVERGENTE] {nome}")
        for campo, valor_giv, valor_web in divergencias:
            diferenca = valor_web - valor_giv if campo != 'qtd' else int(valor_web) - int(valor_giv)
            print(f"    - {campo}: GIV={valor_giv} Web={valor_web} Dif={diferenca}")
        return False

    print(f"  [OK] {nome}: " + "; ".join(partes))
    return True


def adicionar_validacao(validacoes, chave_tabela, nome, tabela_web, campos, sql_giv, sql_web, params_web):
    if tabela_web:
        validacoes.append((chave_tabela, nome, campos, sql_giv, sql_web, params_web))


def comparar_totais_pos_conversao(cursor_giv, cursor_web, tabelas_selecionadas, tabelas_web, tenant_id, cd_empresa, cd_empresa_giv=None):
    """Compara totais GIV x Web dentro da transacao aberta, antes do commit."""
    validacoes = []
    cd_empresa_giv = int(cd_empresa_giv if cd_empresa_giv is not None else cd_empresa)

    if 'pedido_compra' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'pedido_compra',
            'pedido_compra',
            tabelas_web.get('pedido_compra'),
            [('qtd', 'count'), ('vl_total_pedido', 'decimal')],
            f"SELECT COUNT(*), COALESCE(SUM(vl_pedido), 0) FROM pedido_compra WHERE cd_empresa = {cd_empresa_giv}",
            f"""
            SELECT COUNT(*), COALESCE(SUM(vl_total_pedido), 0)
              FROM {tabelas_web.get('pedido_compra')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )
        adicionar_validacao(
            validacoes,
            'pedido_compra_item',
            'pedido_compra_item',
            tabelas_web.get('pedido_compra_item'),
            [('qtd', 'count'), ('qt_produto', 'decimal'), ('vl_total_itens', 'decimal')],
            f"SELECT COUNT(*), COALESCE(SUM(qt_pedida), 0), COALESCE(SUM(qt_pedida * vl_unitario), 0) FROM pedido_compra_item WHERE cd_empresa = {cd_empresa_giv}",
            f"""
            SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0)
              FROM {tabelas_web.get('pedido_compra_item')}
             WHERE tenant_id = %s
            """,
            (tenant_id,)
        )

    if 'nota_fiscal_entrada' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'nota_fiscal_entrada',
            'nota_fiscal_entrada',
            tabelas_web.get('nota_fiscal_entrada'),
            [('qtd', 'count'), ('vl_total_nota', 'decimal'), ('vl_total_produto', 'decimal')],
            f"SELECT COUNT(*), COALESCE(SUM(vl_total_nota), 0), COALESCE(SUM(vl_produto), 0) FROM nota_fiscal_entrada WHERE cd_empresa = {cd_empresa_giv}",
            f"""
            SELECT COUNT(*), COALESCE(SUM(vl_total_nota), 0), COALESCE(SUM(vl_total_produto), 0)
              FROM {tabelas_web.get('nota_fiscal_entrada')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )
        adicionar_validacao(
            validacoes,
            'nota_fiscal_entrada_item',
            'nota_fiscal_entrada_item',
            tabelas_web.get('nota_fiscal_entrada_item'),
            [('qtd', 'count'), ('qt_produto', 'decimal'), ('vl_total_itens', 'decimal')],
            f"SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0) FROM nota_fiscal_entrada_item WHERE cd_empresa = {cd_empresa_giv}",
            f"""
            SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0)
              FROM {tabelas_web.get('nota_fiscal_entrada_item')}
             WHERE tenant_id = %s
            """,
            (tenant_id,)
        )
        # pr_rel_pedido_compra calcula o valor por situacao com:
        # Round(vl_unitario * (1 + pr_ipi / 100) * quantidade, 2).
        adicionar_validacao(
            validacoes,
            'pedido_compra_item',
            'pedido_compra_relatorio',
            tabelas_web.get('pedido_compra_item'),
            [('qtd', 'count'), ('vl_relatorio', 'decimal')],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(ROUND(
                    vl_unitario * (1 + pr_ipi / 100) * qt_pedida,
                    2
                )), 0)
              FROM pedido_compra_item
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(ROUND(
                    vl_unitario * (1 + pr_ipi / 100) * qt_produto,
                    2
                )), 0)
              FROM {tabelas_web.get('pedido_compra_item')}
             WHERE tenant_id = %s
            """,
            (tenant_id,)
        )
        # Formula usada por pr_rel_nota_fiscal_entrada no GIV:
        # Round(Round(vl_unitario * qt_produto, 2) - vl_desconto
        #       + vl_substituicao + vl_ipi
        #       + frete condicionado ao cabecalho
        #       + vl_outras_despesas, 2).
        # O schema Web atual nao possui vl_substituicao no item; o GIV desta
        # conversao tem esse componente zerado. Mantemos 0 explicito para que
        # uma futura base com substituicao diferente de zero seja denunciada.
        adicionar_validacao(
            validacoes,
            'nota_fiscal_entrada_item',
            'nota_fiscal_entrada_relatorio',
            tabelas_web.get('nota_fiscal_entrada_item'),
            [
                ('qtd', 'count'),
                ('qt_produto', 'decimal'),
                ('vl_relatorio', 'decimal'),
                ('vl_ipi', 'decimal'),
                ('vl_frete', 'decimal'),
                ('vl_outras_despesas', 'decimal'),
            ],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(i.qt_produto), 0),
                COALESCE(SUM(ROUND(
                    ROUND(i.vl_unitario * i.qt_produto, 2)
                    - i.vl_desconto
                    + i.vl_substituicao
                    + i.vl_ipi
                    + CASE WHEN n.id_soma_frete_total_nota = 'S'
                           THEN i.vl_frete ELSE 0 END
                    + i.vl_outras_despesas,
                    2
                )), 0),
                COALESCE(SUM(i.vl_ipi), 0),
                COALESCE(SUM(i.vl_frete), 0),
                COALESCE(SUM(i.vl_outras_despesas), 0)
              FROM nota_fiscal_entrada_item i
              JOIN nota_fiscal_entrada n ON
                   n.nr_nota = i.nr_nota
               AND n.serie = i.serie
               AND n.cd_empresa = i.cd_empresa
               AND n.cd_fornecedor = i.cd_fornecedor
               AND n.id_pessoa = i.id_pessoa
             WHERE n.cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(i.qt_produto), 0),
                COALESCE(SUM(ROUND(
                    ROUND(i.vl_unitario * i.qt_produto, 2)
                    - i.vl_desconto
                    + 0
                    + i.vl_ipi
                    + CASE WHEN n.id_soma_frete_total_nota = 'S'
                           THEN i.vl_frete ELSE 0 END
                    + i.vl_outras_despesas,
                    2
                )), 0),
                COALESCE(SUM(i.vl_ipi), 0),
                COALESCE(SUM(i.vl_frete), 0),
                COALESCE(SUM(i.vl_outras_despesas), 0)
              FROM {tabelas_web.get('nota_fiscal_entrada_item')} i
              JOIN {tabelas_web.get('nota_fiscal_entrada')} n ON
                   n.nf_id = i.nf_id
               AND n.tenant_id = i.tenant_id
             WHERE i.tenant_id = %s
               AND n.cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'prevenda' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'prevenda',
            'prevenda',
            tabelas_web.get('prevenda'),
            [
                ('qtd', 'count'),
                ('vl_total', 'decimal'),
                ('vl_desconto', 'decimal'),
                ('vl_acrescimo_produto', 'decimal'),
                ('vl_acrescimo_financeiro', 'decimal'),
            ],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_total), 0),
                COALESCE(SUM(vl_desconto_total), 0),
                COALESCE(SUM(vl_acrescimo_total_item), 0),
                COALESCE(SUM(vl_acrescimo_total), 0)
              FROM prevenda
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_total), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo_produto), 0),
                COALESCE(SUM(vl_acrescimo_financeiro), 0)
              FROM {tabelas_web.get('prevenda')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )
        adicionar_validacao(
            validacoes,
            'prevenda_item',
            'prevenda_item',
            tabelas_web.get('prevenda_item'),
            [('qtd', 'count'), ('qt_produto', 'decimal'), ('vl_total_itens', 'decimal')],
            f"SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0) FROM prevenda_item WHERE cd_empresa = {cd_empresa_giv}",
            f"""
            SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0)
              FROM {tabelas_web.get('prevenda_item')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'condicional' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'condicional',
            'condicional',
            tabelas_web.get('condicional'),
            [
                ('qtd', 'count'),
                ('vl_total', 'decimal'),
                ('qt_produto_total', 'decimal'),
                ('vl_acrescimo', 'decimal'),
                ('vl_desconto', 'decimal'),
            ],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_total_orcamento), 0),
                COALESCE((
                    SELECT SUM(qt_cotada)
                      FROM orcamento_item oi
                     WHERE oi.cd_empresa = {cd_empresa_giv}
                ), 0),
                COALESCE(SUM(COALESCE(vl_acrescimo_total, 0) + COALESCE(vl_acrescimo_total_item, 0)), 0),
                COALESCE(SUM(vl_desconto_total), 0)
              FROM orcamento
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_total), 0),
                COALESCE(SUM(qt_produto_total), 0),
                COALESCE(SUM(vl_acrescimo), 0),
                COALESCE(SUM(vl_desconto), 0)
              FROM {tabelas_web.get('condicional')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )
        adicionar_validacao(
            validacoes,
            'condicional_item',
            'condicional_item',
            tabelas_web.get('condicional_item'),
            [
                ('qtd', 'count'),
                ('qt_produto', 'decimal'),
                ('qt_faturado', 'decimal'),
                ('qt_devolvido', 'decimal'),
                ('vl_bruto_itens', 'decimal'),
                ('vl_desconto_itens', 'decimal'),
                ('vl_acrescimo_itens', 'decimal'),
            ],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(qt_cotada), 0),
                COALESCE(SUM(qt_atendida), 0),
                COALESCE(SUM(qt_suspenso), 0),
                COALESCE(SUM(qt_cotada * vl_unitario), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo), 0)
              FROM orcamento_item
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(qt_produto), 0),
                COALESCE(SUM(qt_faturado), 0),
                COALESCE(SUM(qt_devolvido), 0),
                COALESCE(SUM(qt_produto * vl_unitario), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo_produto + vl_acrescimo_financeiro), 0)
              FROM {tabelas_web.get('condicional_item')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'nota_fiscal_saida' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'nota_fiscal_saida',
            'nota_fiscal_saida',
            tabelas_web.get('nota_fiscal_saida'),
            [
                ('qtd', 'count'),
                ('vl_total', 'decimal'),
                ('vl_desconto', 'decimal'),
                ('vl_acrescimo_produto', 'decimal'),
                ('vl_acrescimo_financeiro', 'decimal'),
            ],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_total_nota), 0),
                COALESCE(SUM(vl_desconto_total), 0),
                COALESCE(SUM(vl_acrescimo_total_item), 0),
                COALESCE(SUM(vl_acrescimo), 0)
              FROM nota_fiscal_saida
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_total), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo_produto), 0),
                COALESCE(SUM(vl_acrescimo_financeiro), 0)
              FROM {tabelas_web.get('nota_fiscal_saida')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )
        adicionar_validacao(
            validacoes,
            'nota_fiscal_saida_item',
            'nota_fiscal_saida_item',
            tabelas_web.get('nota_fiscal_saida_item'),
            [('qtd', 'count'), ('qt_produto', 'decimal'), ('vl_total_itens', 'decimal')],
            f"SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0) FROM nota_fiscal_saida_item WHERE cd_empresa = {cd_empresa_giv}",
            f"""
            SELECT COUNT(*), COALESCE(SUM(qt_produto), 0), COALESCE(SUM(qt_produto * vl_unitario), 0)
              FROM {tabelas_web.get('nota_fiscal_saida_item')}
             WHERE tenant_id = %s
            """,
            (tenant_id,)
        )
        # pr_rel_nota_fiscal_saida calcula o produto, PIS/COFINS e a nota a
        # partir dos valores do item; nao usa apenas o total do cabecalho.
        adicionar_validacao(
            validacoes,
            'nota_fiscal_saida_item',
            'nota_fiscal_saida_relatorio',
            tabelas_web.get('nota_fiscal_saida_item'),
            [
                ('qtd', 'count'),
                ('qt_produto', 'decimal'),
                ('vl_produto_relatorio', 'decimal'),
                ('vl_pis_relatorio', 'decimal'),
                ('vl_cofins_relatorio', 'decimal'),
                ('vl_frete', 'decimal'),
                ('vl_ipi', 'decimal'),
                ('vl_substituicao', 'decimal'),
                ('vl_outras_despesas', 'decimal'),
                ('vl_nota_relatorio', 'decimal'),
            ],
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(i.qt_produto), 0),
                COALESCE(SUM(
                    i.qt_produto * i.vl_unitario
                    - i.vl_desconto
                    + i.vl_acrescimo
                    + i.vl_acrescimo_financeiro
                ), 0),
                COALESCE(SUM(ROUND((
                    ROUND(i.vl_unitario * i.qt_produto, 2)
                    - i.vl_desconto
                    + i.vl_acrescimo
                    + i.vl_acrescimo_financeiro
                    + i.vl_frete
                ) * i.pr_pis / 100, 2)), 0),
                COALESCE(SUM(ROUND((
                    ROUND(i.vl_unitario * i.qt_produto, 2)
                    - i.vl_desconto
                    + i.vl_acrescimo
                    + i.vl_acrescimo_financeiro
                    + i.vl_frete
                ) * i.pr_cofins / 100, 2)), 0),
                COALESCE(SUM(i.vl_frete), 0),
                COALESCE(SUM(i.vl_ipi), 0),
                COALESCE(SUM(i.vl_substituicao), 0),
                COALESCE(SUM(i.vl_outras_despesas), 0),
                COALESCE(SUM(
                    i.qt_produto * i.vl_unitario
                    - i.vl_desconto
                    + i.vl_acrescimo
                    + i.vl_acrescimo_financeiro
                    + i.vl_ipi
                    + i.vl_substituicao
                    + i.vl_frete
                    + i.vl_outras_despesas
                ), 0)
              FROM nota_fiscal_saida_item i
              JOIN nota_fiscal_saida n ON
                   n.nr_nota = i.nr_nota
               AND n.serie = i.serie
               AND n.cd_empresa = i.cd_empresa
             WHERE n.cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(i.qt_produto), 0),
                COALESCE(SUM(
                    i.qt_produto * i.vl_unitario
                    - i.vl_desconto
                    + i.vl_acrescimo_produto
                    + i.vl_acrescimo_financeiro
                ), 0),
                COALESCE(SUM(ROUND((
                    ROUND(i.vl_unitario * i.qt_produto, 2)
                    - i.vl_desconto
                    + i.vl_acrescimo_produto
                    + i.vl_acrescimo_financeiro
                    + i.vl_frete
                ) * i.pr_pis / 100, 2)), 0),
                COALESCE(SUM(ROUND((
                    ROUND(i.vl_unitario * i.qt_produto, 2)
                    - i.vl_desconto
                    + i.vl_acrescimo_produto
                    + i.vl_acrescimo_financeiro
                    + i.vl_frete
                ) * i.pr_cofins / 100, 2)), 0),
                COALESCE(SUM(i.vl_frete), 0),
                COALESCE(SUM(i.vl_ipi), 0),
                COALESCE(SUM(i.vl_substituicao), 0),
                COALESCE(SUM(i.vl_outras_despesas), 0),
                COALESCE(SUM(
                    i.qt_produto * i.vl_unitario
                    - i.vl_desconto
                    + i.vl_acrescimo_produto
                    + i.vl_acrescimo_financeiro
                    + i.vl_ipi
                    + i.vl_substituicao
                    + i.vl_frete
                    + i.vl_outras_despesas
                ), 0)
              FROM {tabelas_web.get('nota_fiscal_saida_item')} i
              JOIN {tabelas_web.get('nota_fiscal_saida')} n ON
                   n.nf_id = i.nf_id
               AND n.tenant_id = i.tenant_id
             WHERE i.tenant_id = %s
               AND n.cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'banco_conta' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'banco_conta',
            'banco_conta',
            tabelas_web.get('banco_conta'),
            [('qtd', 'count'), ('vl_saldo', 'decimal'), ('vl_limite', 'decimal')],
            f"""
            SELECT COUNT(*), COALESCE(SUM(vl_saldo), 0), COALESCE(SUM(vl_limite), 0)
              FROM banco_conta
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT COUNT(*), COALESCE(SUM(vl_saldo), 0), COALESCE(SUM(vl_limite), 0)
              FROM {tabelas_web.get('banco_conta')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'cartao_administradora' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'cartao_administradora',
            'cartao_administradora',
            tabelas_web.get('cartao_administradora'),
            [('qtd', 'count')],
            "SELECT COUNT(*) FROM cartao_administradora",
            f"""
            SELECT COUNT(*)
              FROM {tabelas_web.get('cartao_administradora')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'titulo_receber' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'titulo_receber',
            'titulo_receber',
            tabelas_web.get('titulo_receber'),
            [
                ('qtd', 'count'),
                ('vl_titulo', 'decimal'),
                ('vl_desconto', 'decimal'),
                ('vl_acrescimo', 'decimal'),
                ('vl_liquido', 'decimal'),
                ('vl_pago', 'decimal'),
                ('vl_saldo_aberto', 'decimal'),
            ],
            # Mesma composicao das procedures do GIV (pr_rel_balanco_patrimonial):
            # saldo = vl_titulo - vl_desconto + vl_acrescimo - vl_pago.
            # vl_original NAO entra: e o total da venda repetido em cada parcela.
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_titulo), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo), 0),
                COALESCE(SUM(vl_titulo - vl_desconto + vl_acrescimo), 0),
                COALESCE(SUM(vl_pago), 0),
                COALESCE(SUM(
                    CASE WHEN id_situacao = 'AB'
                         THEN vl_titulo - vl_desconto + vl_acrescimo - vl_pago
                         ELSE 0 END
                ), 0)
              FROM titulo_receber
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_titulo), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo), 0),
                COALESCE(SUM(vl_titulo - vl_desconto + vl_acrescimo), 0),
                COALESCE(SUM(vl_pago), 0),
                COALESCE(SUM(
                    CASE WHEN id_status = 'A'
                         THEN vl_titulo - vl_desconto + vl_acrescimo - vl_pago
                         ELSE 0 END
                ), 0)
              FROM {tabelas_web.get('titulo_receber')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if 'titulo_pagar' in tabelas_selecionadas:
        adicionar_validacao(
            validacoes,
            'titulo_pagar',
            'titulo_pagar',
            tabelas_web.get('titulo_pagar'),
            [('qtd', 'count'), ('vl_titulo', 'decimal'), ('vl_desconto', 'decimal'),
             ('vl_acrescimo', 'decimal'), ('vl_pago', 'decimal'), ('vl_saldo_aberto', 'decimal')],
            # saldo em aberto pela mesma conta do GIV (pr_rel_fluxo_caixa_novo).
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_titulo), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo), 0),
                COALESCE(SUM(vl_pago), 0),
                COALESCE(SUM(
                    CASE WHEN id_situacao = 'AB'
                         THEN vl_titulo - vl_desconto + vl_acrescimo - vl_pago
                         ELSE 0 END
                ), 0)
              FROM titulo_pagar
             WHERE cd_empresa = {cd_empresa_giv}
            """,
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(vl_titulo), 0),
                COALESCE(SUM(vl_desconto), 0),
                COALESCE(SUM(vl_acrescimo), 0),
                COALESCE(SUM(vl_pago), 0),
                COALESCE(SUM(
                    CASE WHEN id_status = 'A'
                         THEN vl_titulo - vl_desconto + vl_acrescimo - vl_pago
                         ELSE 0 END
                ), 0)
              FROM {tabelas_web.get('titulo_pagar')}
             WHERE tenant_id = %s AND cd_empresa = %s
            """,
            (tenant_id, cd_empresa)
        )

    if not validacoes:
        return True

    print()
    print("=" * 60)
    print("  VALIDACAO GIV X WEB")
    print("  Comparando dados dentro da transacao aberta.")
    tudo_ok = True
    for _chave, nome, campos, sql_giv, sql_web, params_web in validacoes:
        try:
            giv_row = linha_unica(cursor_giv, sql_giv)
            web_row = linha_unica(cursor_web, sql_web, params_web)
            if not comparar_total(nome, campos, giv_row, web_row):
                tudo_ok = False
        except Exception as e:
            tudo_ok = False
            print(f"  [AVISO] Nao foi possivel validar {nome}: {e}")

    print("=" * 60)
    if tudo_ok:
        print("[OK] Validacao GIV x Web sem divergencias nos totais comparados.")
    else:
        print("[AVISO] Existem divergencias na validacao GIV x Web. Confira antes do COMMIT.")
    return tudo_ok


def literal_sql(valor):
    """Escapa valor como literal SQL simples."""
    return "'" + str(valor).replace("'", "''") + "'"


def setval_sql(sequence_nome, valor_maximo):
    """Gera comando seguro para voltar uma sequence ao valor anterior."""
    sequence_literal = literal_sql(sequence_nome)
    valor_maximo = int(valor_maximo or 0)
    if valor_maximo > 0:
        return f"SELECT setval({sequence_literal}::regclass, {valor_maximo}, true);"
    return f"SELECT setval({sequence_literal}::regclass, 1, false);"


def sql_remove_cep(sql):
    """Detecta DELETE acidental na tabela cep dentro do script de reversao."""
    return bool(re.search(
        r'\bDELETE\s+FROM\s+(?:(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\.)?"?cep"?\b',
        sql,
        flags=re.IGNORECASE
    ))


def max_coluna_web(cursor_web, tabela_web, coluna):
    """Busca MAX(coluna) de uma tabela Web."""
    cursor_web.execute(f"SELECT COALESCE(MAX({quote_identificador(coluna)}), 0) FROM {tabela_web}")
    row = cursor_web.fetchone()
    return int(row[0] or 0)


def item_reversao(cursor_web, tabela_web, coluna, sequence_coluna=None):
    """Monta metadados de reversao para uma tabela sequencial."""
    if not tabela_web:
        return None
    item = {
        'tabela': tabela_web,
        'coluna': coluna,
        'max': max_coluna_web(cursor_web, tabela_web, coluna),
        'sequence': None,
    }
    if sequence_coluna:
        try:
            item['sequence'] = buscar_sequence_coluna_web(cursor_web, tabela_web, sequence_coluna)
        except Exception:
            item['sequence'] = None
    return item


def capturar_estado_reversao(
    cursor_web,
    tabelas_selecionadas,
    tenant_id,
    cd_empresa,
    tabelas_web
):
    """Captura os maiores codigos antes da conversao para gerar reverter.txt."""
    estado = {
        'tenant_id': tenant_id,
        'cd_empresa': cd_empresa,
        'capturado_em': datetime.datetime.now().isoformat(timespec='seconds'),
        'itens': {},
        'produto_global': None,
        'produto_estoque_existentes': None,
    }

    defs = {
        'grupo': ('grupo', 'cd_grupo'),
        'marca': ('marca', 'cd_marca'),
        'cor': ('cor', 'cd_cor'),
        'departamento': ('departamento', 'cd_departamento'),
        'sub_grupo': ('sub_grupo', 'cd_sub_grupo'),
        'tamanho': ('tamanho', 'cd_tamanho'),
        'unidade': ('unidade', 'cd_unidade'),
        'colecao': ('colecao', 'cd_colecao'),
        'grade': ('grade', 'cd_grade'),
        'fornecedor': ('fornecedor', 'cd_fornecedor'),
        'usuario': ('usuario', 'cd_usuario'),
        'cliente': ('cliente', 'cd_cliente'),
        'banco': ('banco', 'cd_banco'),
        'banco_conta': ('banco_conta', 'cd_conta'),
        'condicao_pagamento': ('condicao_pagamento', 'cd_condicao_pagto'),
        'forma_pagamento': ('forma_pagamento', 'cd_forma_pagto'),
        'cartao_administradora': ('cartao_administradora', 'cd_administradora'),
        'condicional': ('condicional', 'nr_condicional'),
        'pedido_compra': ('pedido_compra', 'nr_pedido'),
        'nota_fiscal_entrada': ('nota_fiscal_entrada', 'nf_id'),
        'prevenda': ('prevenda', 'nr_prevenda'),
        'nota_fiscal_saida': ('nota_fiscal_saida', 'nf_id'),
        'titulo_receber': ('titulo_receber', 'tr_id'),
        'titulo_pagar': ('titulo_pagar', 'tp_id'),
    }
    for chave, (nome_tabela, coluna) in defs.items():
        selecionada = chave in tabelas_selecionadas
        if chave == 'forma_pagamento':
            selecionada = 'condicao_pagamento' in tabelas_selecionadas
        if selecionada and tabelas_web.get(nome_tabela):
            estado['itens'][chave] = item_reversao(
                cursor_web,
                tabelas_web.get(nome_tabela),
                coluna,
                coluna
            )

    if 'titulo_receber' in tabelas_selecionadas and tabelas_web.get('titulo_receber_historico'):
        estado['itens']['titulo_receber_historico'] = item_reversao(
            cursor_web,
            tabelas_web.get('titulo_receber_historico'),
            'trh_id',
            'trh_id'
        )
    if 'titulo_pagar' in tabelas_selecionadas and tabelas_web.get('titulo_pagar_historico'):
        estado['itens']['titulo_pagar_historico'] = item_reversao(
            cursor_web,
            tabelas_web.get('titulo_pagar_historico'),
            'tph_id',
            'tph_id'
        )

    if 'produto' in tabelas_selecionadas or ROTINAS_COM_PRODUTO.intersection(tabelas_selecionadas):
        produto = tabelas_web.get('produto')
        produto_filho = tabelas_web.get('produto_filho')
        if produto and produto_filho:
            sequence_produto = buscar_sequence_coluna_web(cursor_web, produto, 'cd_produto')
            max_produto = max_coluna_web(cursor_web, produto, 'cd_produto')
            max_produto_filho = max_coluna_web(cursor_web, produto_filho, 'cd_produto_filho')
            estado['produto_global'] = {
                'tabela_produto': produto,
                'tabela_produto_filho': produto_filho,
                'max': max(max_produto, max_produto_filho),
                'max_produto': max_produto,
                'max_produto_filho': max_produto_filho,
                'sequence': sequence_produto,
            }
            if 'produto' in tabelas_selecionadas and tabelas_web.get('produto_estoque'):
                cursor_web.execute(
                    f"""
                    SELECT {quote_identificador('cd_produto')}
                      FROM {tabelas_web['produto_estoque']}
                     WHERE {quote_identificador('tenant_id')} = %s
                       AND {quote_identificador('cd_empresa')} = %s
                    """,
                    (tenant_id, cd_empresa)
                )
                estado['produto_estoque_existentes'] = sorted({
                    int(row[0])
                    for row in cursor_web.fetchall()
                    if row[0] is not None
                })

    return estado


def delete_maior_que(tabela, coluna, maximo, tenant_id=None, cd_empresa=None):
    """Gera DELETE por chave maior que o snapshot."""
    if re.search(r'(?:(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\.)?"?cep"?$', str(tabela), flags=re.IGNORECASE):
        return f"-- Mantido {tabela}: CEPs nao sao removidos pelo reverter."

    condicoes = [f"{quote_identificador(coluna)} > {int(maximo or 0)}"]
    if tenant_id is not None:
        condicoes.append(f"{quote_identificador('tenant_id')} = {int(tenant_id)}")
    if cd_empresa is not None:
        condicoes.append(f"{quote_identificador('cd_empresa')} = {int(cd_empresa)}")
    return f"DELETE FROM {tabela} WHERE {' AND '.join(condicoes)};"


def delete_produto_estoque_criado(tabela, tenant_id, cd_empresa, existentes_antes):
    """Remove estoques criados para a empresa, inclusive de produtos reaproveitados."""
    if not tabela:
        return None
    if existentes_antes is None:
        return None

    condicoes = [
        f"{quote_identificador('tenant_id')} = {int(tenant_id)}",
        f"{quote_identificador('cd_empresa')} = {int(cd_empresa)}",
    ]
    existentes_antes = sorted({int(valor) for valor in existentes_antes if valor is not None})
    if existentes_antes:
        codigos = ", ".join(str(valor) for valor in existentes_antes)
        condicoes.append(f"{quote_identificador('cd_produto')} NOT IN ({codigos})")

    return f"DELETE FROM {tabela} WHERE {' AND '.join(condicoes)};"


def delete_por_tenant_empresa(tabela, tenant_id=None, cd_empresa=None, coluna_tenant='tenant_id', coluna_empresa='cd_empresa'):
    """Gera DELETE por tenant/empresa para reversao de criacao de empresa."""
    if not tabela:
        return None
    if re.search(r'(?:(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)\.)?"?cep"?$', str(tabela), flags=re.IGNORECASE):
        return f"-- Mantido {tabela}: CEPs nao sao removidos pelo reverter."
    condicoes = []
    if tenant_id is not None:
        condicoes.append(f"{quote_identificador(coluna_tenant)} = {int(tenant_id)}")
    if cd_empresa is not None:
        condicoes.append(f"{quote_identificador(coluna_empresa)} = {int(cd_empresa)}")
    if not condicoes:
        return f"-- Sem filtro seguro para {tabela}; DELETE nao gerado."
    return f"DELETE FROM {tabela} WHERE {' AND '.join(condicoes)};"


def delete_item_por_produto_convertido(tabela, limite_produto, tenant_id):
    """Remove itens/documentos que ainda apontam para produtos criados na conversao."""
    if not tabela:
        return None
    return delete_maior_que(tabela, 'cd_produto', limite_produto, tenant_id)


def condicao_tenant_empresa_sql(tenant_id=None, cd_empresa=None, prefixo=None):
    """Monta filtro SQL por tenant/empresa, opcionalmente qualificando colunas."""
    def coluna(nome):
        if prefixo:
            return f"{prefixo}.{quote_identificador(nome)}"
        return quote_identificador(nome)

    condicoes = []
    if tenant_id is not None:
        condicoes.append(f"{coluna('tenant_id')} = {int(tenant_id)}")
    if cd_empresa is not None:
        condicoes.append(f"{coluna('cd_empresa')} = {int(cd_empresa)}")
    return condicoes


def delete_in_subquery(tabela, coluna, subquery, tenant_id=None, cd_empresa=None):
    """Gera DELETE usando IN (subquery), mantendo filtros seguros quando a tabela possui essas colunas."""
    if not tabela:
        return None
    condicoes = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
    condicoes.append(f"{quote_identificador(coluna)} IN ({subquery})")
    return f"DELETE FROM {tabela} WHERE {' AND '.join(condicoes)};"


def adicionar_deletes_por_referencias_convertidas(linhas, itens, tabelas_web, tenant_id, cd_empresa):
    """Remove filhos/documentos que referenciam cadastros criados na conversao.

    Isso protege o reverter quando algum documento reaproveita numeracao antiga,
    mas aponta para cliente/fornecedor novo.
    """
    adicionou = False

    if 'cliente' in itens:
        limite_cliente = int(itens['cliente']['max'] or 0)
        linhas.append("-- Dependencias por cliente convertido")
        adicionou = True

        if tabelas_web.get('condicional'):
            cond_condicional = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_condicional.append(f"{quote_identificador('cd_cliente')} > {limite_cliente}")
            sub_condicional = (
                f"SELECT {quote_identificador('nr_condicional')} "
                f"FROM {tabelas_web['condicional']} "
                f"WHERE {' AND '.join(cond_condicional)}"
            )
            if tabelas_web.get('condicional_item'):
                linhas.append(delete_in_subquery(
                    tabelas_web['condicional_item'],
                    'nr_condicional',
                    sub_condicional,
                    tenant_id,
                    cd_empresa
                ))
            linhas.append(f"DELETE FROM {tabelas_web['condicional']} WHERE {' AND '.join(cond_condicional)};")

        if tabelas_web.get('prevenda'):
            cond_prevenda = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_prevenda.append(f"{quote_identificador('cd_cliente')} > {limite_cliente}")
            sub_prevenda = (
                f"SELECT {quote_identificador('nr_prevenda')} "
                f"FROM {tabelas_web['prevenda']} "
                f"WHERE {' AND '.join(cond_prevenda)}"
            )
            if tabelas_web.get('prevenda_item'):
                linhas.append(delete_in_subquery(
                    tabelas_web['prevenda_item'],
                    'nr_prevenda',
                    sub_prevenda,
                    tenant_id,
                    cd_empresa
                ))
            linhas.append(f"DELETE FROM {tabelas_web['prevenda']} WHERE {' AND '.join(cond_prevenda)};")

        if tabelas_web.get('nota_fiscal_saida'):
            cond_nf_saida = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_nf_saida.append(f"{quote_identificador('cd_cliente')} > {limite_cliente}")
            sub_nf_saida = (
                f"SELECT {quote_identificador('nf_id')} "
                f"FROM {tabelas_web['nota_fiscal_saida']} "
                f"WHERE {' AND '.join(cond_nf_saida)}"
            )
            if tabelas_web.get('prevenda'):
                cond_prevenda_nf = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
                cond_prevenda_nf.append(f"{quote_identificador('nf_id_documento_fiscal')} IN ({sub_nf_saida})")
                sub_prevenda_nf = (
                    f"SELECT {quote_identificador('nr_prevenda')} "
                    f"FROM {tabelas_web['prevenda']} "
                    f"WHERE {' AND '.join(cond_prevenda_nf)}"
                )
                if tabelas_web.get('prevenda_item'):
                    linhas.append(delete_in_subquery(
                        tabelas_web['prevenda_item'],
                        'nr_prevenda',
                        sub_prevenda_nf,
                        tenant_id,
                        cd_empresa
                    ))
                linhas.append(f"DELETE FROM {tabelas_web['prevenda']} WHERE {' AND '.join(cond_prevenda_nf)};")
            if tabelas_web.get('titulo_receber'):
                cond_titulo_nf = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
                cond_titulo_nf.append(f"{quote_identificador('nf_id')} IN ({sub_nf_saida})")
                sub_titulo_nf = (
                    f"SELECT {quote_identificador('tr_id')} "
                    f"FROM {tabelas_web['titulo_receber']} "
                    f"WHERE {' AND '.join(cond_titulo_nf)}"
                )
                if tabelas_web.get('titulo_receber_historico'):
                    linhas.append(delete_in_subquery(
                        tabelas_web['titulo_receber_historico'],
                        'tr_id',
                        sub_titulo_nf,
                        tenant_id
                    ))
                linhas.append(f"DELETE FROM {tabelas_web['titulo_receber']} WHERE {' AND '.join(cond_titulo_nf)};")
            if tabelas_web.get('nota_fiscal_saida_item'):
                linhas.append(delete_in_subquery(
                    tabelas_web['nota_fiscal_saida_item'],
                    'nf_id',
                    sub_nf_saida,
                    tenant_id
                ))
            if tabelas_web.get('nota_fiscal_saida_info'):
                linhas.append(delete_in_subquery(
                    tabelas_web['nota_fiscal_saida_info'],
                    'nf_id',
                    sub_nf_saida,
                    tenant_id
                ))
                linhas.append(delete_maior_que(
                    tabelas_web['nota_fiscal_saida_info'],
                    'clienteCd_cliente',
                    limite_cliente,
                    tenant_id
                ))
            linhas.append(f"DELETE FROM {tabelas_web['nota_fiscal_saida']} WHERE {' AND '.join(cond_nf_saida)};")

        if tabelas_web.get('titulo_receber'):
            cond_titulo = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_titulo.append(f"{quote_identificador('cd_cliente')} > {limite_cliente}")
            sub_titulo = (
                f"SELECT {quote_identificador('tr_id')} "
                f"FROM {tabelas_web['titulo_receber']} "
                f"WHERE {' AND '.join(cond_titulo)}"
            )
            if tabelas_web.get('titulo_receber_historico'):
                linhas.append(delete_in_subquery(
                    tabelas_web['titulo_receber_historico'],
                    'tr_id',
                    sub_titulo,
                    tenant_id
                ))
            linhas.append(f"DELETE FROM {tabelas_web['titulo_receber']} WHERE {' AND '.join(cond_titulo)};")

        linhas.append("")

    if 'fornecedor' in itens:
        limite_fornecedor = int(itens['fornecedor']['max'] or 0)
        linhas.append("-- Dependencias por fornecedor convertido")
        adicionou = True

        if tabelas_web.get('pedido_compra'):
            cond_pedido = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_pedido.append(
                "("
                f"{quote_identificador('cd_fornecedor')} > {limite_fornecedor} "
                "OR "
                f"{quote_identificador('cd_transportador')} > {limite_fornecedor}"
                ")"
            )
            sub_pedido = (
                f"SELECT {quote_identificador('nr_pedido')} "
                f"FROM {tabelas_web['pedido_compra']} "
                f"WHERE {' AND '.join(cond_pedido)}"
            )
            if tabelas_web.get('pedido_compra_item'):
                linhas.append(delete_in_subquery(
                    tabelas_web['pedido_compra_item'],
                    'nr_pedido',
                    sub_pedido,
                    tenant_id
                ))
            linhas.append(f"DELETE FROM {tabelas_web['pedido_compra']} WHERE {' AND '.join(cond_pedido)};")

        if tabelas_web.get('nota_fiscal_entrada'):
            cond_nf_entrada = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_nf_entrada.append(
                "("
                f"{quote_identificador('cd_fornecedor')} > {limite_fornecedor} "
                "OR "
                f"{quote_identificador('cd_transportador')} > {limite_fornecedor}"
                ")"
            )
            sub_nf_entrada = (
                f"SELECT {quote_identificador('nf_id')} "
                f"FROM {tabelas_web['nota_fiscal_entrada']} "
                f"WHERE {' AND '.join(cond_nf_entrada)}"
            )
            if tabelas_web.get('titulo_pagar'):
                cond_titulo_nf_entrada = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
                cond_titulo_nf_entrada.append(f"{quote_identificador('nf_id_entrada')} IN ({sub_nf_entrada})")
                sub_titulo_nf_entrada = (
                    f"SELECT {quote_identificador('tp_id')} "
                    f"FROM {tabelas_web['titulo_pagar']} "
                    f"WHERE {' AND '.join(cond_titulo_nf_entrada)}"
                )
                if tabelas_web.get('titulo_pagar_historico'):
                    linhas.append(delete_in_subquery(
                        tabelas_web['titulo_pagar_historico'],
                        'tp_id',
                        sub_titulo_nf_entrada,
                        tenant_id
                    ))
                linhas.append(f"DELETE FROM {tabelas_web['titulo_pagar']} WHERE {' AND '.join(cond_titulo_nf_entrada)};")
            if tabelas_web.get('nota_fiscal_entrada_item'):
                linhas.append(delete_in_subquery(
                    tabelas_web['nota_fiscal_entrada_item'],
                    'nf_id',
                    sub_nf_entrada,
                    tenant_id
                ))
            linhas.append(f"DELETE FROM {tabelas_web['nota_fiscal_entrada']} WHERE {' AND '.join(cond_nf_entrada)};")

        if tabelas_web.get('titulo_pagar'):
            cond_titulo_pagar = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_titulo_pagar.append(f"{quote_identificador('cd_fornecedor')} > {limite_fornecedor}")
            sub_titulo_pagar = (
                f"SELECT {quote_identificador('tp_id')} "
                f"FROM {tabelas_web['titulo_pagar']} "
                f"WHERE {' AND '.join(cond_titulo_pagar)}"
            )
            if tabelas_web.get('titulo_pagar_historico'):
                linhas.append(delete_in_subquery(
                    tabelas_web['titulo_pagar_historico'],
                    'tp_id',
                    sub_titulo_pagar,
                    tenant_id
                ))
            linhas.append(f"DELETE FROM {tabelas_web['titulo_pagar']} WHERE {' AND '.join(cond_titulo_pagar)};")

        if tabelas_web.get('nota_fiscal_saida_info'):
            linhas.append(delete_maior_que(
                tabelas_web['nota_fiscal_saida_info'],
                'cd_transportador',
                limite_fornecedor,
                tenant_id
            ))

        linhas.append("")

    if 'cartao_administradora' in itens:
        limite_cartao = int(itens['cartao_administradora']['max'] or 0)
        linhas.append("-- Dependencias por administradora de cartao convertida")
        adicionou = True

        if tabelas_web.get('prevenda'):
            cond_prevenda = condicao_tenant_empresa_sql(tenant_id, cd_empresa)
            cond_prevenda.append(f"{quote_identificador('cd_administradora')} > {limite_cartao}")
            sub_prevenda = (
                f"SELECT {quote_identificador('nr_prevenda')} "
                f"FROM {tabelas_web['prevenda']} "
                f"WHERE {' AND '.join(cond_prevenda)}"
            )
            if tabelas_web.get('prevenda_item'):
                linhas.append(delete_in_subquery(
                    tabelas_web['prevenda_item'],
                    'nr_prevenda',
                    sub_prevenda,
                    tenant_id,
                    cd_empresa
                ))
            linhas.append(f"DELETE FROM {tabelas_web['prevenda']} WHERE {' AND '.join(cond_prevenda)};")

        linhas.append("")

    return adicionou


def gerar_sql_reversao_empresa_base(estado, tabelas_web):
    """Gera comandos SQL para desfazer a criacao de tenant/empresa/base minima."""
    tenant_id = estado['tenant_id']
    cd_empresa = estado['cd_empresa']
    itens = estado.get('itens', {})
    linhas = []

    linhas.append("-- Script gerado automaticamente pelo converter.py")
    linhas.append(f"-- Gerado em: {datetime.datetime.now().isoformat(timespec='seconds')}")
    linhas.append(f"-- Snapshot anterior capturado em: {estado.get('capturado_em')}")
    linhas.append("-- Tipo: reversao de criacao/importacao de empresa")
    linhas.append(f"-- tenant_id vinculado: {tenant_id}")
    linhas.append(f"-- cd_empresa criada: {cd_empresa}")
    linhas.append("-- Execute somente depois de conferir o banco alvo.")
    linhas.append("-- CEPs nao sao removidos automaticamente por serem cadastro compartilhado.")
    linhas.append("-- O tenant vinculado nao sera removido por esta reversao.")
    linhas.append("")
    linhas.append("BEGIN;")
    linhas.append("")

    def item(chave):
        return itens.get(chave) or {}

    def delete_item(chave, coluna, empresa=False):
        dados_item = item(chave)
        if dados_item.get('tabela'):
            linhas.append(delete_maior_que(
                dados_item['tabela'],
                coluna,
                dados_item.get('max'),
                tenant_id,
                cd_empresa if empresa else None
            ))

    if tabelas_web.get('condicao_pagamento_forma') and item('condicao_pagamento').get('max') is not None:
        linhas.append(delete_maior_que(
            tabelas_web['condicao_pagamento_forma'],
            'cd_condicao_pagto',
            item('condicao_pagamento').get('max'),
            tenant_id
        ))

    # Tabelas sem sequence ou com chave operacional da empresa criada.
    for chave, filtro_empresa in (
        ('sys_config_values', cd_empresa),
        ('sequencial_nota_fiscal', cd_empresa),
        ('tenant_sequencial_empresa', cd_empresa),
        ('sequencial', cd_empresa),
        ('local_estoque', cd_empresa),
        ('situacao_tributaria', cd_empresa),
    ):
        sql = delete_por_tenant_empresa(tabelas_web.get(chave), tenant_id, filtro_empresa)
        if sql:
            linhas.append(sql)

    # Cadastros/documentos vinculados a empresa criada.
    delete_item('cliente', 'cd_cliente', empresa=True)
    delete_item('banco_conta', 'cd_conta', empresa=True)
    delete_item('banco', 'cd_banco', empresa=True)
    delete_item('caixa', 'cd_caixa', empresa=True)
    delete_item('grade', 'cd_grade', empresa=True)
    delete_item('grupo', 'cd_grupo', empresa=True)
    delete_item('departamento', 'cd_departamento', empresa=True)
    delete_item('colecao', 'cd_colecao', empresa=True)

    # Cadastros compartilhados do tenant: remover somente o que nasceu depois do snapshot.
    delete_item('operacao_estoque', 'cd_operacao_estoque')
    delete_item('forma_pagamento', 'cd_forma_pagto')
    delete_item('condicao_pagamento', 'cd_condicao_pagto')
    delete_item('tabela_preco', 'cd_tabela_preco')
    delete_item('categoria_cliente', 'cd_categoria')
    delete_item('marca', 'cd_marca')
    delete_item('tamanho', 'cd_tamanho')
    delete_item('cor', 'cd_cor')
    delete_item('unidade', 'cd_unidade')
    delete_item('usuario', 'cd_usuario')
    delete_item('empresa', 'cd_empresa')

    if estado.get('tenant_criado') and tabelas_web.get('tenant') and tenant_id is not None:
        linhas.append(f"DELETE FROM {tabelas_web['tenant']} WHERE {quote_identificador('id')} = {int(tenant_id)};")

    linhas.append("")
    linhas.append("-- Reverter sequences")
    for item in itens.values():
        if item and item.get('sequence'):
            linhas.append(setval_sql(item['sequence'], item['max']))

    linhas.append("")
    linhas.append("COMMIT;")
    linhas.append("")
    return "\n".join(linhas)


def gerar_sql_reversao(estado, tabelas_web):
    """Gera comandos SQL para desfazer os inserts da conversao atual."""
    if estado.get('tipo') == 'empresa_base':
        return gerar_sql_reversao_empresa_base(estado, tabelas_web)

    tenant_id = estado['tenant_id']
    cd_empresa = estado['cd_empresa']
    itens = estado.get('itens', {})
    produto_global = estado.get('produto_global')
    linhas = []

    linhas.append("-- Script gerado automaticamente pelo converter.py")
    linhas.append(f"-- Gerado em: {datetime.datetime.now().isoformat(timespec='seconds')}")
    linhas.append(f"-- Snapshot anterior capturado em: {estado.get('capturado_em')}")
    linhas.append(f"-- tenant_id: {tenant_id}")
    linhas.append(f"-- cd_empresa: {cd_empresa}")
    linhas.append("-- Execute somente depois de conferir o banco alvo.")
    linhas.append("-- CEPs cadastrados pela API nao sao removidos automaticamente por serem cadastro compartilhado.")
    linhas.append("")
    linhas.append("BEGIN;")
    linhas.append("")

    adicionar_deletes_por_referencias_convertidas(
        linhas,
        itens,
        tabelas_web,
        tenant_id,
        cd_empresa
    )

    if 'titulo_receber_historico' in itens:
        linhas.append(delete_maior_que(itens['titulo_receber_historico']['tabela'], 'trh_id', itens['titulo_receber_historico']['max'], tenant_id))
    if 'titulo_receber' in itens:
        linhas.append(delete_maior_que(itens['titulo_receber']['tabela'], 'tr_id', itens['titulo_receber']['max'], tenant_id, cd_empresa))
    if 'titulo_pagar_historico' in itens:
        linhas.append(delete_maior_que(itens['titulo_pagar_historico']['tabela'], 'tph_id', itens['titulo_pagar_historico']['max'], tenant_id))
    if 'titulo_pagar' in itens:
        linhas.append(delete_maior_que(itens['titulo_pagar']['tabela'], 'tp_id', itens['titulo_pagar']['max'], tenant_id, cd_empresa))

    if 'condicional' in itens:
        limite_condicional = itens['condicional']['max']
        if tabelas_web.get('condicional_item'):
            linhas.append(delete_maior_que(
                tabelas_web['condicional_item'],
                'nr_condicional',
                limite_condicional,
                tenant_id,
                cd_empresa
            ))
        linhas.append(delete_maior_que(
            itens['condicional']['tabela'],
            'nr_condicional',
            limite_condicional,
            tenant_id,
            cd_empresa
        ))

    if 'nota_fiscal_saida' in itens:
        limite_nf_saida = itens['nota_fiscal_saida']['max']
        if tabelas_web.get('nota_fiscal_saida_item'):
            linhas.append(delete_maior_que(tabelas_web['nota_fiscal_saida_item'], 'nf_id', limite_nf_saida, tenant_id))
        if tabelas_web.get('nota_fiscal_saida_info'):
            linhas.append(delete_maior_que(tabelas_web['nota_fiscal_saida_info'], 'nf_id', limite_nf_saida, tenant_id))
        linhas.append(delete_maior_que(itens['nota_fiscal_saida']['tabela'], 'nf_id', limite_nf_saida, tenant_id, cd_empresa))

    if 'prevenda' in itens:
        limite_prevenda = itens['prevenda']['max']
        if tabelas_web.get('prevenda_item'):
            linhas.append(delete_maior_que(tabelas_web['prevenda_item'], 'nr_prevenda', limite_prevenda, tenant_id, cd_empresa))
        linhas.append(delete_maior_que(itens['prevenda']['tabela'], 'nr_prevenda', limite_prevenda, tenant_id, cd_empresa))

    if 'nota_fiscal_entrada' in itens:
        limite_nf_entrada = itens['nota_fiscal_entrada']['max']
        if tabelas_web.get('nota_fiscal_entrada_item'):
            linhas.append(delete_maior_que(tabelas_web['nota_fiscal_entrada_item'], 'nf_id', limite_nf_entrada, tenant_id))
        linhas.append(delete_maior_que(itens['nota_fiscal_entrada']['tabela'], 'nf_id', limite_nf_entrada, tenant_id, cd_empresa))

    if 'pedido_compra' in itens:
        limite_pedido = itens['pedido_compra']['max']
        if tabelas_web.get('pedido_compra_item'):
            linhas.append(delete_maior_que(tabelas_web['pedido_compra_item'], 'nr_pedido', limite_pedido, tenant_id))
        linhas.append(delete_maior_que(itens['pedido_compra']['tabela'], 'nr_pedido', limite_pedido, tenant_id, cd_empresa))

    if produto_global:
        limite_produto = produto_global['max']
        linhas.append("")
        linhas.append("-- Produtos e dependencias")
        for chave_item_produto in (
            'condicional_item',
            'pedido_compra_item',
            'nota_fiscal_entrada_item',
            'prevenda_item',
            'nota_fiscal_saida_item',
            'orcamento_item',
            'inventario_estoque_item',
            'movimento_estoque',
            'produto_local_estoque',
        ):
            sql_item_produto = delete_item_por_produto_convertido(
                tabelas_web.get(chave_item_produto),
                limite_produto,
                tenant_id
            )
            if sql_item_produto:
                linhas.append(sql_item_produto)
        if tabelas_web.get('produto_estoque'):
            sql_estoque_snapshot = delete_produto_estoque_criado(
                tabelas_web['produto_estoque'],
                tenant_id,
                cd_empresa,
                estado.get('produto_estoque_existentes')
            )
            if sql_estoque_snapshot:
                linhas.append(sql_estoque_snapshot)
            else:
                linhas.append(delete_maior_que(
                    tabelas_web['produto_estoque'],
                    'cd_produto',
                    limite_produto,
                    tenant_id,
                    cd_empresa
                ))
        if tabelas_web.get('produto_preco'):
            linhas.append(delete_maior_que(tabelas_web['produto_preco'], 'cd_produto', limite_produto, tenant_id))
        if tabelas_web.get('produto_colecao'):
            linhas.append(delete_maior_que(tabelas_web['produto_colecao'], 'cd_produto', limite_produto, tenant_id))
        if tabelas_web.get('produto_filho'):
            linhas.append(delete_maior_que(tabelas_web['produto_filho'], 'cd_produto_filho', limite_produto, tenant_id))
            linhas.append(delete_maior_que(tabelas_web['produto_filho'], 'cd_produto_pai', limite_produto, tenant_id))
        if tabelas_web.get('produto_info'):
            linhas.append(delete_maior_que(tabelas_web['produto_info'], 'cd_produto', limite_produto, tenant_id))
        if tabelas_web.get('produto'):
            linhas.append(delete_maior_que(tabelas_web['produto'], 'cd_produto', limite_produto, tenant_id))
        linhas.append("")

    # Dependencias antes das tabelas principais
    if 'grade' in itens and tabelas_web.get('grade_tamanho'):
        linhas.append("-- Relacionamento grade x tamanho")
        condicoes_grade_tamanho = [
            f"{quote_identificador('A')} > {int(itens['grade']['max'])}"
        ]
        if 'tamanho' in itens:
            condicoes_grade_tamanho.append(
                f"{quote_identificador('B')} > {int(itens['tamanho']['max'])}"
            )
        linhas.append(
            f"DELETE FROM {tabelas_web['grade_tamanho']} "
            f"WHERE {' OR '.join(condicoes_grade_tamanho)};"
        )
        linhas.append("")

    if 'sub_grupo' in itens:
        linhas.append(delete_maior_que(itens['sub_grupo']['tabela'], 'cd_sub_grupo', itens['sub_grupo']['max'], tenant_id, cd_empresa))
    if 'cliente' in itens:
        linhas.append(delete_maior_que(itens['cliente']['tabela'], 'cd_cliente', itens['cliente']['max'], tenant_id, cd_empresa))
    if 'fornecedor' in itens:
        linhas.append(delete_maior_que(itens['fornecedor']['tabela'], 'cd_fornecedor', itens['fornecedor']['max'], tenant_id))
    if 'usuario' in itens:
        linhas.append(delete_maior_que(itens['usuario']['tabela'], 'cd_usuario', itens['usuario']['max'], tenant_id))
    if 'departamento' in itens:
        linhas.append(delete_maior_que(itens['departamento']['tabela'], 'cd_departamento', itens['departamento']['max'], tenant_id, cd_empresa))
    if 'grupo' in itens:
        linhas.append(delete_maior_que(itens['grupo']['tabela'], 'cd_grupo', itens['grupo']['max'], tenant_id, cd_empresa))
    if 'marca' in itens:
        linhas.append(delete_maior_que(itens['marca']['tabela'], 'cd_marca', itens['marca']['max'], tenant_id))
    if 'cor' in itens:
        linhas.append(delete_maior_que(itens['cor']['tabela'], 'cd_cor', itens['cor']['max'], tenant_id))

    if 'grade' in itens:
        linhas.append(delete_maior_que(itens['grade']['tabela'], 'cd_grade', itens['grade']['max'], tenant_id, cd_empresa))
    if 'tamanho' in itens:
        linhas.append(delete_maior_que(itens['tamanho']['tabela'], 'cd_tamanho', itens['tamanho']['max'], tenant_id))
    if 'unidade' in itens:
        linhas.append(delete_maior_que(itens['unidade']['tabela'], 'cd_unidade', itens['unidade']['max'], tenant_id))
    if 'colecao' in itens:
        linhas.append(delete_maior_que(itens['colecao']['tabela'], 'cd_colecao', itens['colecao']['max'], tenant_id, cd_empresa))
    if 'condicao_pagamento' in itens:
        if tabelas_web.get('condicao_pagamento_forma'):
            linhas.append(delete_maior_que(
                tabelas_web['condicao_pagamento_forma'],
                'cd_condicao_pagto',
                itens['condicao_pagamento']['max'],
                tenant_id
            ))
        linhas.append(delete_maior_que(itens['condicao_pagamento']['tabela'], 'cd_condicao_pagto', itens['condicao_pagamento']['max'], tenant_id))
    if 'forma_pagamento' in itens:
        linhas.append(delete_maior_que(
            itens['forma_pagamento']['tabela'],
            'cd_forma_pagto',
            itens['forma_pagamento']['max'],
            tenant_id
        ))
    if 'cartao_administradora' in itens:
        linhas.append(delete_maior_que(
            itens['cartao_administradora']['tabela'],
            'cd_administradora',
            itens['cartao_administradora']['max'],
            tenant_id,
            cd_empresa
        ))
    if 'banco_conta' in itens:
        linhas.append(delete_maior_que(itens['banco_conta']['tabela'], 'cd_conta', itens['banco_conta']['max'], tenant_id, cd_empresa))
    if 'banco' in itens:
        if tabelas_web.get('banco_conta') and 'banco_conta' not in itens:
            linhas.append(delete_maior_que(
                tabelas_web['banco_conta'],
                'cd_banco',
                itens['banco']['max'],
                tenant_id,
                cd_empresa
            ))
        linhas.append(delete_maior_que(itens['banco']['tabela'], 'cd_banco', itens['banco']['max'], tenant_id, cd_empresa))

    linhas.append("")
    linhas.append("-- Reverter sequences")
    if produto_global and produto_global.get('sequence'):
        linhas.append(setval_sql(produto_global['sequence'], produto_global['max']))
    for item in itens.values():
        if item and item.get('sequence'):
            linhas.append(setval_sql(item['sequence'], item['max']))

    linhas.append("")
    linhas.append("COMMIT;")
    linhas.append("")
    return "\n".join(linhas)


def gravar_reverter_txt(estado_reversao, tabelas_web):
    """Grava reverter.txt com comandos para desfazer a conversao confirmada."""
    if MODO_DRY_RUN:
        print()
        print("[DRY-RUN] reverter.txt nao foi gravado (nenhuma alteracao sera efetivada).")
        return None
    pasta_saida = limpar_valor(os.environ.get("CONVERTER_OUTPUT_DIR"))
    if not pasta_saida:
        if getattr(sys, "frozen", False):
            pasta_saida = os.path.dirname(sys.executable)
        else:
            pasta_saida = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(pasta_saida, exist_ok=True)
    caminho = os.path.join(pasta_saida, "reverter.txt")
    sql = gerar_sql_reversao(estado_reversao, tabelas_web)
    if sql_remove_cep(sql):
        raise RuntimeError("Seguranca: reverter.txt tentou gerar DELETE na tabela cep.")
    with open(caminho, "w", encoding="utf-8") as arquivo:
        arquivo.write(sql)
    print()
    print(f"[OK] Arquivo de reversao gerado: {caminho}")
    print("[INFO] Guarde esse arquivo se decidir confirmar o COMMIT.")
    return caminho


def confirmar_transacao(conn_web):
    if MODO_DRY_RUN:
        conn_web.rollback()
        print()
        print("=" * 60)
        print("[DRY-RUN] Simulacao concluida. ROLLBACK executado automaticamente.")
        print("[DRY-RUN] Nenhum INSERT, UPDATE ou DELETE foi efetivado no banco Web.")
        print("[DRY-RUN] Os numeros acima mostram exatamente o que seria gravado.")
        print("=" * 60)
        return

    print()
    print(">>> A transacao esta ABERTA no PostgreSQL remoto. <<<")
    print(">>> Nenhum dado foi efetivado ainda.              <<<")
    print()

    while True:
        print("Deseja CONFIRMAR (commit) ou CANCELAR (rollback)? [C/R]:")
        decisao = input().strip().upper()
        if decisao == 'C':
            conn_web.commit()
            print()
            print("[OK] COMMIT realizado! Dados efetivados no banco Web.")
            break
        if decisao == 'R':
            conn_web.rollback()
            print()
            print("[OK] ROLLBACK realizado! Nenhum dado foi alterado no banco Web.")
            break

        print("Opcao invalida. Digite 'C' para commit ou 'R' para rollback.")


def solicitar_tabelas_para_converter():
    """Pergunta quais tabelas devem ser convertidas."""
    opcoes = {
        '0': OPCAO_NOVA_EMPRESA,
        'nova_empresa': OPCAO_NOVA_EMPRESA,
        'criar_empresa': OPCAO_NOVA_EMPRESA,
        'empresa_nova': OPCAO_NOVA_EMPRESA,
        '1': OPCAO_EMPRESA_GIV,
        'empresa_giv': OPCAO_EMPRESA_GIV,
        'giv_empresa': OPCAO_EMPRESA_GIV,
        'empresa': OPCAO_EMPRESA_GIV,
        '2': 'grupo',
        'g': 'grupo',
        'grupo': 'grupo',
        'grupo_moda': 'grupo',
        '3': 'fornecedor',
        'f': 'fornecedor',
        'fornecedor': 'fornecedor',
        '4': 'cliente',
        'c': 'cliente',
        'cliente': 'cliente',
        '5': 'usuario',
        'u': 'usuario',
        'usuario': 'usuario',
        'usuarios': 'usuario',
        '6': 'marca',
        'm': 'marca',
        'marca': 'marca',
        'marcas': 'marca',
        '7': 'cor',
        'cor': 'cor',
        'cores': 'cor',
        '8': 'sub_grupo',
        's': 'sub_grupo',
        'sub_grupo': 'sub_grupo',
        'subgrupo': 'sub_grupo',
        'subgrupo_moda': 'sub_grupo',
        '9': 'departamento',
        'd': 'departamento',
        'departamento': 'departamento',
        'departamentos': 'departamento',
        'genero_moda': 'departamento',
        '10': 'tamanho',
        'tam': 'tamanho',
        'tamanho': 'tamanho',
        'tamanhos': 'tamanho',
        '11': 'unidade',
        'un': 'unidade',
        'unidade': 'unidade',
        'unidades': 'unidade',
        '12': 'colecao',
        'colecao': 'colecao',
        'colecoes': 'colecao',
        '13': 'grade',
        'grade': 'grade',
        'grades': 'grade',
        '14': 'produto',
        'p': 'produto',
        'produto': 'produto',
        'produtos': 'produto',
        '24': 'condicional',
        'condicional': 'condicional',
        'condicionais': 'condicional',
        '15': 'banco',
        'banco': 'banco',
        'bancos': 'banco',
        '23': 'banco_conta',
        'banco_conta': 'banco_conta',
        'conta_bancaria': 'banco_conta',
        'contas_bancarias': 'banco_conta',
        '16': 'condicao_pagamento',
        'condicao': 'condicao_pagamento',
        'condicao_pagamento': 'condicao_pagamento',
        'condicao_pagto': 'condicao_pagamento',
        'condicoes': 'condicao_pagamento',
        '25': 'cartao_administradora',
        'cartao': 'cartao_administradora',
        'cartao_administradora': 'cartao_administradora',
        'administradora_cartao': 'cartao_administradora',
        'administradoras_cartao': 'cartao_administradora',
        '17': 'pedido_compra',
        'pedido_compra': 'pedido_compra',
        'pedido': 'pedido_compra',
        'pedidos': 'pedido_compra',
        '18': 'nota_fiscal_entrada',
        'nfe': 'nota_fiscal_entrada',
        'nota_fiscal_entrada': 'nota_fiscal_entrada',
        '19': 'prevenda',
        'prevenda': 'prevenda',
        'prevendas': 'prevenda',
        '20': 'nota_fiscal_saida',
        'nfs': 'nota_fiscal_saida',
        'nota_fiscal_saida': 'nota_fiscal_saida',
        '21': 'titulo_receber',
        'contas_receber': 'titulo_receber',
        'contas_a_receber': 'titulo_receber',
        'titulo_receber': 'titulo_receber',
        '22': 'titulo_pagar',
        'contas_pagar': 'titulo_pagar',
        'contas_a_pagar': 'titulo_pagar',
        'titulo_pagar': 'titulo_pagar',
    }

    print("Quais tabelas deseja converter?")
    print("  0 - criar nova empresa/tenant manualmente")
    print("  1 - empresa do GIV -> empresa/base Web")
    print("  Ordem segura de processamento:")
    print("  2 - Grupo_moda -> Grupo")
    print("  9 - genero_moda -> departamento")
    print("  8 - subgrupo_moda -> sub_grupo")
    print("  6 - marca -> marca")
    print("  7 - cor -> cor")
    print("  10 - tamanho -> tamanho")
    print("  11 - unidade -> unidade")
    print("  12 - colecao -> colecao")
    print("  13 - grade -> grade/_gradeTotamanho")
    print("  3 - fornecedor -> fornecedor")
    print("  5 - usuario -> usuario")
    print("  4 - cliente -> cliente")
    print("  15 - banco -> banco")
    print("  23 - banco_conta -> banco_conta")
    print("  16 - condicao_pagto -> condicao_pagamento")
    print("  25 - cartao_administradora -> cartao_administradora")
    print("  14 - produto -> produto/produto_info/produto_preco/estoque")
    print("  24 - orcamento -> condicional/condicional_item")
    print("  17 - pedido_compra -> pedido_compra/pedido_compra_item")
    print("  18 - nota_fiscal_entrada -> nota_fiscal_entrada/itens")
    print("  19 - prevenda -> prevenda/prevenda_item")
    print("  20 - nota_fiscal_saida -> nota_fiscal_saida/info/itens")
    print("  21 - titulo_receber -> titulo_receber/historico")
    print("  22 - titulo_pagar -> titulo_pagar/historico")
    print("  T - todas na ordem segura")
    print("Voce pode informar mais de uma opcao, exemplo: 2,9,8,6,11,14,21")

    while True:
        resposta = input("Tabelas [T/0..25]: ").strip().lower()
        if not resposta or resposta in ('t', 'todas', 'todos', 'all'):
            return list(TABELAS_DISPONIVEIS)

        tokens = resposta.replace(';', ',').replace(' ', ',').split(',')
        selecionadas = []
        invalidas = []
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            tabela = opcoes.get(token)
            if tabela is None:
                invalidas.append(token)
                continue
            if tabela not in selecionadas:
                selecionadas.append(tabela)

        if any(tabela in (OPCAO_NOVA_EMPRESA, OPCAO_EMPRESA_GIV) for tabela in selecionadas):
            if len(selecionadas) > 1:
                print("As opcoes 0 e 1 criam empresa/base e devem ser executadas sozinhas.")
                continue
            if invalidas:
                print("Opcao invalida. Use 0 para nova empresa ou 1 para empresa do GIV.")
                continue
            return selecionadas

        if selecionadas and not invalidas:
            return [tabela for tabela in TABELAS_DISPONIVEIS if tabela in selecionadas]

        print("Opcao invalida. Use T, 0..24 ou uma lista como 2,9,8,6,11,14,21.")


def solicitar_tenant_id():
    """Pergunta o tenant_id que sera usado nos registros."""
    while True:
        tenant_id = input("Informe o TENANT_ID para os registros: ").strip()
        if not tenant_id:
            print("[ERRO] tenant_id eh obrigatorio.")
            continue
        try:
            return int(tenant_id)
        except ValueError:
            print("[ERRO] tenant_id deve ser numerico.")


def solicitar_cd_empresa():
    """Pergunta o cd_empresa que sera usado nos registros do Web."""
    while True:
        cd_empresa = input("Informe o CD_EMPRESA Web para os registros: ").strip()
        if not cd_empresa:
            print("[ERRO] cd_empresa eh obrigatorio.")
            continue
        try:
            return int(cd_empresa)
        except ValueError:
            print("[ERRO] cd_empresa deve ser numerico.")


def solicitar_cd_empresa_giv():
    """Pergunta o cd_empresa de origem no GIV usado nos filtros da conversao."""
    while True:
        cd_empresa = input("Informe o CD_EMPRESA GIV de origem: ").strip()
        if not cd_empresa:
            print("[ERRO] cd_empresa GIV eh obrigatorio.")
            continue
        try:
            return int(cd_empresa)
        except ValueError:
            print("[ERRO] cd_empresa GIV deve ser numerico.")


def obter_mapa_status_produto_padrao():
    """Retorna o de/para padrao de status de produto."""
    print()
    print("[PRODUTO] Status padrao: L -> A, E -> I, F -> I.")
    return dict(MAPA_STATUS_PRODUTO_PADRAO)


def solicitar_uso_api_cep():
    """Pergunta se deve chamar API para CEPs ausentes no Web."""
    while True:
        resposta = input("Usar API de CEP para cadastrar CEPs ausentes? [S/N] (N): ").strip().upper()
        if resposta in ('', 'N', 'NAO', 'NÃO'):
            print("[INFO] API de CEP desativada. Somente CEPs ja existentes no Web serao mantidos.")
            return False
        if resposta in ('S', 'SIM'):
            return True
        print("Opcao invalida. Digite S para usar API ou N para pular.")


# Flags que pertencem a converter_gui.py; o conversor as ignora ao ler argv.
ARGUMENTOS_DA_INTERFACE = ('--run-converter', '--test-giv', '--test-web')


def _valor_booleano_env(valor):
    return str(valor or '').strip().lower() in ('1', 's', 'sim', 'true', 'y', 'yes')


def parse_argumentos_execucao(argv=None):
    """
    Le os modos de execucao segura da linha de comando ou do ambiente:
      --dry-run              conecta, valida e monta tudo, mas termina em ROLLBACK.
      --limit-products N     converte apenas N produtos raiz (com todos os filhos deles).
    Equivalentes por variavel de ambiente, usados pela interface grafica:
      CONVERTER_DRY_RUN=1
      CONVERTER_LIMIT_PRODUCTS=25
    """
    global MODO_DRY_RUN, LIMITE_PRODUTOS_CONVERSAO

    if _valor_booleano_env(os.environ.get("CONVERTER_DRY_RUN")):
        MODO_DRY_RUN = True
    limite_env = limpar_valor(os.environ.get("CONVERTER_LIMIT_PRODUCTS"))
    if limite_env and limite_env.isdigit():
        LIMITE_PRODUTOS_CONVERSAO = int(limite_env)

    argv = list(sys.argv[1:] if argv is None else argv)
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ARGUMENTOS_DA_INTERFACE:
            pass
        elif arg in ('--dry-run', '--simular'):
            MODO_DRY_RUN = True
        elif arg in ('--limit-products', '--limite-produtos'):
            i += 1
            if i >= len(argv):
                print("[ERRO] --limit-products exige um numero.")
                sys.exit(2)
            LIMITE_PRODUTOS_CONVERSAO = int(argv[i])
        elif arg.startswith('--limit-products=') or arg.startswith('--limite-produtos='):
            LIMITE_PRODUTOS_CONVERSAO = int(arg.split('=', 1)[1])
        elif arg in ('-h', '--help'):
            print("Uso: converter.py [--dry-run] [--limit-products N]")
            sys.exit(0)
        else:
            print(f"[ERRO] Argumento desconhecido: {arg}")
            sys.exit(2)
        i += 1


def main():
    parse_argumentos_execucao()

    print("=" * 60)
    print("  CONVERSOR DE DADOS - GIV -> Web")
    print("  Tabelas: cadastros base | produto | documentos | financeiro")
    print("=" * 60)
    if MODO_DRY_RUN:
        print("  MODO DRY-RUN: nada sera efetivado; a transacao termina em ROLLBACK.")
    if LIMITE_PRODUTOS_CONVERSAO:
        print(f"  LIMITE DE PRODUTOS: {LIMITE_PRODUTOS_CONVERSAO} raizes (mais os filhos delas).")
    print()

    tabelas_selecionadas = solicitar_tabelas_para_converter()
    if tabelas_selecionadas == [OPCAO_NOVA_EMPRESA]:
        main_criar_empresa(OPCAO_NOVA_EMPRESA)
        return
    if tabelas_selecionadas == [OPCAO_EMPRESA_GIV]:
        main_criar_empresa(OPCAO_EMPRESA_GIV)
        return

    tabelas_selecionadas = ordenar_tabelas_para_processamento(tabelas_selecionadas)
    avisar_dependencias_nao_selecionadas(tabelas_selecionadas)

    tenant_id = solicitar_tenant_id()
    cd_empresa = solicitar_cd_empresa()
    cd_empresa_giv_filtro = solicitar_cd_empresa_giv()
    precisa_cd_empresa_giv_produto = (
        'produto' in tabelas_selecionadas
        or bool(ROTINAS_COM_PRODUTO.intersection(tabelas_selecionadas))
    )
    cd_empresa_giv_produto = cd_empresa_giv_filtro if precisa_cd_empresa_giv_produto else None
    mapa_status_produto = None
    if 'produto' in tabelas_selecionadas:
        mapa_status_produto = obter_mapa_status_produto_padrao()

    print()
    print(f"[INFO] Tabelas selecionadas: {', '.join(tabelas_selecionadas)}")
    print(f"[INFO] tenant_id informado: {tenant_id}")
    print(f"[INFO] cd_empresa Web destino: {cd_empresa}")
    print(f"[INFO] cd_empresa GIV origem: {cd_empresa_giv_filtro}")
    if cd_empresa_giv_produto is not None:
        print(f"[INFO] Produto/saldo tambem usara cd_empresa GIV origem: {cd_empresa_giv_produto}")
    print(f"[INFO] id_status padrao para fornecedor: {ID_STATUS_FORNECEDOR_PADRAO}")
    print()

    session_api_cep = None
    base_url_api_cep = None
    tabelas_com_api_cep = [tabela for tabela in tabelas_selecionadas if tabela in ('fornecedor', 'cliente')]
    if tabelas_com_api_cep:
        usar_api_cep = solicitar_uso_api_cep()
        if usar_api_cep:
            try:
                config_api_cep = solicitar_config_api_cep()
                base_url_api_cep = config_api_cep['base_url']
                session_api_cep = autenticar_api_cep(config_api_cep)
                validar_api_cep(session_api_cep, base_url_api_cep)
            except Exception as e:
                print()
                print(f"[ERRO] API de CEP indisponivel: {e}")
                print(f"[INFO] A conversao de {', '.join(tabelas_com_api_cep)} nao sera iniciada.")
                sys.exit(1)

    conn_giv = conectar_giv()
    conn_web = conectar_web()

    cursor_giv = conn_giv.cursor()
    cursor_web = conn_web.cursor()

    try:
        print()
        print("[...] Resolvendo nomes das tabelas no PostgreSQL remoto...")
        tabela_web_grupo = None
        tabela_web_fornecedor = None
        tabela_web_cliente = None
        tabela_web_usuario = None
        tabela_web_marca = None
        tabela_web_cor = None
        tabela_web_sub_grupo = None
        tabela_web_departamento = None
        tabelas_web_produto = {}
        tabelas_web_rotinas = {}
        auxiliares_produto_selecionados = [
            tabela for tabela in AUXILIARES_PRODUTO
            if tabela in tabelas_selecionadas
        ]
        precisa_auxiliares_produto = bool(auxiliares_produto_selecionados) or 'produto' in tabelas_selecionadas
        rotinas_documento_selecionadas = {
            'condicional',
            'pedido_compra',
            'nota_fiscal_entrada',
            'prevenda',
            'nota_fiscal_saida',
            'titulo_receber',
            'titulo_pagar',
        }.intersection(tabelas_selecionadas)
        precisa_mapas_rotinas = bool(rotinas_documento_selecionadas)
        precisa_produto_completo = 'produto' in tabelas_selecionadas or bool(
            ROTINAS_COM_PRODUTO.intersection(tabelas_selecionadas)
        )
        if precisa_mapas_rotinas:
            precisa_auxiliares_produto = True

        if 'grupo' in tabelas_selecionadas or 'sub_grupo' in tabelas_selecionadas or precisa_produto_completo:
            tabela_web_grupo = resolver_tabela_web(cursor_web, "Grupo")
            if 'grupo' in tabelas_selecionadas:
                print(f"[OK] Tabela Web Grupo: {tabela_web_grupo}")
            else:
                print(f"[OK] Tabela Web Grupo para mapeamento: {tabela_web_grupo}")
        if 'fornecedor' in tabelas_selecionadas or precisa_produto_completo or precisa_mapas_rotinas:
            tabela_web_fornecedor = resolver_tabela_web(cursor_web, "fornecedor")
            if 'fornecedor' in tabelas_selecionadas:
                print(f"[OK] Tabela Web fornecedor: {tabela_web_fornecedor}")
            else:
                print(f"[OK] Tabela Web fornecedor para mapeamento: {tabela_web_fornecedor}")
        if 'cliente' in tabelas_selecionadas or precisa_mapas_rotinas:
            tabela_web_cliente = resolver_tabela_web(cursor_web, "cliente")
            if 'cliente' in tabelas_selecionadas:
                print(f"[OK] Tabela Web cliente: {tabela_web_cliente}")
            else:
                print(f"[OK] Tabela Web cliente para mapeamento: {tabela_web_cliente}")
        if 'usuario' in tabelas_selecionadas or precisa_mapas_rotinas:
            tabela_web_usuario = resolver_tabela_web(cursor_web, "usuario")
            if 'usuario' in tabelas_selecionadas:
                print(f"[OK] Tabela Web usuario: {tabela_web_usuario}")
            else:
                print(f"[OK] Tabela Web usuario para mapeamento: {tabela_web_usuario}")
        if 'marca' in tabelas_selecionadas or precisa_produto_completo:
            tabela_web_marca = resolver_tabela_web(cursor_web, "marca")
            if 'marca' in tabelas_selecionadas:
                print(f"[OK] Tabela Web marca: {tabela_web_marca}")
            else:
                print(f"[OK] Tabela Web marca para mapeamento de produto: {tabela_web_marca}")
        if 'cor' in tabelas_selecionadas or precisa_produto_completo:
            tabela_web_cor = resolver_tabela_web(cursor_web, "cor")
            if 'cor' in tabelas_selecionadas:
                print(f"[OK] Tabela Web cor: {tabela_web_cor}")
            else:
                print(f"[OK] Tabela Web cor para mapeamento de produto: {tabela_web_cor}")
        if 'departamento' in tabelas_selecionadas or precisa_produto_completo:
            tabela_web_departamento = resolver_tabela_web(cursor_web, "departamento")
            if 'departamento' in tabelas_selecionadas:
                print(f"[OK] Tabela Web departamento: {tabela_web_departamento}")
            else:
                print(f"[OK] Tabela Web departamento para mapeamento de produto: {tabela_web_departamento}")
        if 'sub_grupo' in tabelas_selecionadas or precisa_produto_completo:
            tabela_web_sub_grupo = resolver_tabela_web(cursor_web, "sub_grupo")
            if 'sub_grupo' in tabelas_selecionadas:
                print(f"[OK] Tabela Web sub_grupo: {tabela_web_sub_grupo}")
            else:
                print(f"[OK] Tabela Web sub_grupo para mapeamento de produto: {tabela_web_sub_grupo}")

        if precisa_auxiliares_produto:
            tabelas_web_produto.update({
                'tamanho': resolver_tabela_web(cursor_web, "tamanho"),
                'grade': resolver_tabela_web(cursor_web, "grade"),
                'grade_tamanho': resolver_tabela_web(cursor_web, "_gradeTotamanho"),
                'unidade': resolver_tabela_web(cursor_web, "unidade"),
                'colecao': resolver_tabela_web(cursor_web, "colecao"),
            })
            if auxiliares_produto_selecionados:
                print(
                    "[OK] Tabelas Web auxiliares de produto resolvidas: "
                    f"{', '.join(auxiliares_produto_selecionados)}"
                )

        if precisa_produto_completo:
            tabelas_web_produto.update({
                'produto': resolver_tabela_web(cursor_web, "produto"),
                'produto_info': resolver_tabela_web(cursor_web, "produto_info"),
                'produto_filho': resolver_tabela_web(cursor_web, "produto_filho"),
                'produto_preco': resolver_tabela_web(cursor_web, "produto_preco"),
                'produto_estoque': resolver_tabela_web(cursor_web, "produto_estoque"),
                'produto_colecao': resolver_tabela_web(cursor_web, "produto_colecao"),
                'marca': tabela_web_marca,
                'cor': tabela_web_cor,
                'grupo': tabela_web_grupo,
                'departamento': tabela_web_departamento,
                'sub_grupo': tabela_web_sub_grupo,
                'fornecedor': tabela_web_fornecedor,
            })
            if 'produto' in tabelas_selecionadas:
                print("[OK] Tabelas Web de produto resolvidas.")
            else:
                print("[OK] Tabelas Web de produto para documentos resolvidas.")

        if 'banco' in tabelas_selecionadas or 'banco_conta' in tabelas_selecionadas or 'titulo_pagar' in tabelas_selecionadas:
            tabelas_web_rotinas['banco'] = resolver_tabela_web(cursor_web, "banco")
            if 'banco' in tabelas_selecionadas:
                print(f"[OK] Tabela Web banco: {tabelas_web_rotinas['banco']}")
            else:
                print(f"[OK] Tabela Web banco para mapeamento: {tabelas_web_rotinas['banco']}")
        if 'banco_conta' in tabelas_selecionadas or 'titulo_pagar' in tabelas_selecionadas:
            tabelas_web_rotinas['banco_conta'] = resolver_tabela_web(cursor_web, "banco_conta")
            if 'banco_conta' in tabelas_selecionadas:
                print(f"[OK] Tabela Web banco_conta: {tabelas_web_rotinas['banco_conta']}")
            else:
                print(f"[OK] Tabela Web banco_conta para mapeamento: {tabelas_web_rotinas['banco_conta']}")
        if 'condicao_pagamento' in tabelas_selecionadas or precisa_mapas_rotinas:
            tabelas_web_rotinas['condicao_pagamento'] = resolver_tabela_web(cursor_web, "condicao_pagamento")
            if 'condicao_pagamento' in tabelas_selecionadas:
                print(f"[OK] Tabela Web condicao_pagamento: {tabelas_web_rotinas['condicao_pagamento']}")
            else:
                print(f"[OK] Tabela Web condicao_pagamento para mapeamento: {tabelas_web_rotinas['condicao_pagamento']}")
        if 'condicao_pagamento' in tabelas_selecionadas:
            tabelas_web_rotinas['condicao_pagamento_forma'] = resolver_tabela_web(cursor_web, "condicao_pagamento_forma")
            tabelas_web_rotinas['forma_pagamento'] = resolver_tabela_web(cursor_web, "forma_pagamento")
            print(f"[OK] Tabela Web condicao_pagamento_forma: {tabelas_web_rotinas['condicao_pagamento_forma']}")
            print(f"[OK] Tabela Web forma_pagamento para vinculos: {tabelas_web_rotinas['forma_pagamento']}")
        if 'cartao_administradora' in tabelas_selecionadas or 'prevenda' in tabelas_selecionadas:
            tabelas_web_rotinas['cartao_administradora'] = resolver_tabela_web(cursor_web, "cartao_administradora")
            if 'cartao_administradora' in tabelas_selecionadas:
                print(f"[OK] Tabela Web cartao_administradora: {tabelas_web_rotinas['cartao_administradora']}")
            else:
                print(f"[OK] Tabela Web cartao_administradora para mapeamento: {tabelas_web_rotinas['cartao_administradora']}")
        if precisa_mapas_rotinas:
            tabelas_web_rotinas.update({
                'pedido_compra': resolver_tabela_web(cursor_web, "pedido_compra"),
                'pedido_compra_item': resolver_tabela_web(cursor_web, "pedido_compra_item"),
                'condicional': resolver_tabela_web(cursor_web, "condicional"),
                'condicional_item': resolver_tabela_web(cursor_web, "condicional_item"),
                'nota_fiscal_entrada': resolver_tabela_web(cursor_web, "nota_fiscal_entrada"),
                'nota_fiscal_entrada_item': resolver_tabela_web(cursor_web, "nota_fiscal_entrada_item"),
                'prevenda': resolver_tabela_web(cursor_web, "prevenda"),
                'prevenda_item': resolver_tabela_web(cursor_web, "prevenda_item"),
                'nota_fiscal_saida': resolver_tabela_web(cursor_web, "nota_fiscal_saida"),
                'nota_fiscal_saida_item': resolver_tabela_web(cursor_web, "nota_fiscal_saida_item"),
                'nota_fiscal_saida_info': resolver_tabela_web(cursor_web, "nota_fiscal_saida_info"),
                'titulo_receber': resolver_tabela_web(cursor_web, "titulo_receber"),
                'titulo_receber_historico': resolver_tabela_web(cursor_web, "titulo_receber_historico"),
                'titulo_pagar': resolver_tabela_web(cursor_web, "titulo_pagar"),
                'titulo_pagar_historico': resolver_tabela_web(cursor_web, "titulo_pagar_historico"),
                'forma_pagamento': resolver_tabela_web(cursor_web, "forma_pagamento"),
                'cfop': resolver_tabela_web(cursor_web, "cfop"),
                'empresa': resolver_tabela_web(cursor_web, "empresa"),
            })
            tabelas_web_rotinas.setdefault('banco_conta', resolver_tabela_web(cursor_web, "banco_conta"))
            tabelas_web_produto.setdefault('produto_info', resolver_tabela_web(cursor_web, "produto_info"))
            tabelas_web_produto.setdefault('produto', resolver_tabela_web(cursor_web, "produto"))
            tabelas_web_produto.setdefault('produto_filho', resolver_tabela_web(cursor_web, "produto_filho"))
            tabelas_web_produto.setdefault('unidade', resolver_tabela_web(cursor_web, "unidade"))
            tabelas_web_produto.setdefault('cor', resolver_tabela_web(cursor_web, "cor"))
            tabelas_web_produto.setdefault('tamanho', resolver_tabela_web(cursor_web, "tamanho"))
            print("[OK] Tabelas Web das rotinas/documentos resolvidas.")

        tabelas_web_reversao = {
            'grupo': tabela_web_grupo,
            'fornecedor': tabela_web_fornecedor,
            'cliente': tabela_web_cliente,
            'usuario': tabela_web_usuario,
            'marca': tabela_web_marca,
            'cor': tabela_web_cor,
            'sub_grupo': tabela_web_sub_grupo,
            'departamento': tabela_web_departamento,
        }
        tabelas_web_reversao.update(tabelas_web_produto)
        tabelas_web_reversao.update(tabelas_web_rotinas)
        dependencias_reversao = {}
        if 'cliente' in tabelas_selecionadas:
            dependencias_reversao.update({
                'condicional': 'condicional',
                'condicional_item': 'condicional_item',
                'prevenda': 'prevenda',
                'prevenda_item': 'prevenda_item',
                'nota_fiscal_saida': 'nota_fiscal_saida',
                'nota_fiscal_saida_item': 'nota_fiscal_saida_item',
                'nota_fiscal_saida_info': 'nota_fiscal_saida_info',
                'titulo_receber': 'titulo_receber',
                'titulo_receber_historico': 'titulo_receber_historico',
            })
        if 'fornecedor' in tabelas_selecionadas:
            dependencias_reversao.update({
                'pedido_compra': 'pedido_compra',
                'pedido_compra_item': 'pedido_compra_item',
                'nota_fiscal_entrada': 'nota_fiscal_entrada',
                'nota_fiscal_entrada_item': 'nota_fiscal_entrada_item',
                'titulo_pagar': 'titulo_pagar',
                'titulo_pagar_historico': 'titulo_pagar_historico',
                'nota_fiscal_saida_info': 'nota_fiscal_saida_info',
            })
        if 'produto' in tabelas_selecionadas:
            dependencias_reversao.update({
                'orcamento_item': 'orcamento_item',
                'inventario_estoque_item': 'inventario_estoque_item',
                'movimento_estoque': 'movimento_estoque',
                'produto_local_estoque': 'produto_local_estoque',
            })
        if 'cartao_administradora' in tabelas_selecionadas:
            dependencias_reversao.update({
                'prevenda': 'prevenda',
                'prevenda_item': 'prevenda_item',
            })
        for chave, nome_tabela in dependencias_reversao.items():
            if not tabelas_web_reversao.get(chave):
                tabela_opcional = resolver_tabela_web_opcional(cursor_web, nome_tabela)
                if tabela_opcional:
                    tabelas_web_reversao[chave] = tabela_opcional
        estado_reversao = capturar_estado_reversao(
            cursor_web,
            tabelas_selecionadas,
            tenant_id,
            cd_empresa,
            tabelas_web_reversao
        )

        cidades_giv = {}
        cidades_web = {}
        if (
            'fornecedor' in tabelas_selecionadas
            or 'cliente' in tabelas_selecionadas
            or 'usuario' in tabelas_selecionadas
        ):
            print()
            print("[...] Carregando tabelas de cidades para mapeamento de fornecedor/cliente/usuario...")
            cidades_giv = buscar_cidades_giv(cursor_giv)
            cidades_web = buscar_cidades_web(cursor_web)

        resumos = []
        mapa_usuarios = None
        mapa_grupos = None
        mapa_bancos = {}
        mapa_banco_contas = {}
        mapa_condicoes = None
        mapa_cartao_administradoras = {}
        mapas_auxiliares_produto = {}
        tamanhos_por_grade_produto = None
        if 'grupo' in tabelas_selecionadas:
            gui_progress_tabela('grupo')
            resumo_grupo = processar_grupos(
                cursor_giv,
                cursor_web,
                tabela_web_grupo,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            mapa_grupos = resumo_grupo.get('mapa_grupos', {})
            resumos.append(resumo_grupo)
        if 'departamento' in tabelas_selecionadas:
            gui_progress_tabela('departamento')
            resumos.append(
                processar_departamentos(
                    cursor_giv,
                    cursor_web,
                    tabela_web_departamento,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
            )
        if 'sub_grupo' in tabelas_selecionadas:
            gui_progress_tabela('sub_grupo')
            resumos.append(
                processar_sub_grupos(
                    cursor_giv,
                    cursor_web,
                    tabela_web_sub_grupo,
                    tabela_web_grupo,
                    tenant_id,
                    cd_empresa,
                    mapa_grupos,
                    cd_empresa_giv_filtro
                )
            )
        if 'marca' in tabelas_selecionadas:
            gui_progress_tabela('marca')
            resumos.append(processar_marcas(cursor_giv, cursor_web, tabela_web_marca, tenant_id, cd_empresa_giv_filtro))
        if 'cor' in tabelas_selecionadas:
            gui_progress_tabela('cor')
            resumo_cor = processar_cores(cursor_giv, cursor_web, tabela_web_cor, tenant_id, cd_empresa_giv_filtro)
            mapas_auxiliares_produto['cor'] = resumo_cor.get('mapa_cores', {})
            resumos.append(resumo_cor)
        if 'tamanho' in tabelas_selecionadas:
            gui_progress_tabela('tamanho')
            mapa_tamanhos, resumo = processar_tamanhos_produto(
                cursor_giv,
                cursor_web,
                tabelas_web_produto['tamanho'],
                tenant_id,
                cd_empresa_giv_filtro
            )
            mapas_auxiliares_produto['tamanho'] = mapa_tamanhos
            resumos.append(resumo)
        if 'unidade' in tabelas_selecionadas:
            gui_progress_tabela('unidade')
            mapa_unidades, resumo = processar_unidades_produto(
                cursor_giv,
                cursor_web,
                tabelas_web_produto['unidade'],
                tenant_id,
                cd_empresa_giv_filtro
            )
            mapas_auxiliares_produto['unidade'] = mapa_unidades
            resumos.append(resumo)
        if 'colecao' in tabelas_selecionadas:
            gui_progress_tabela('colecao')
            mapa_colecoes, resumo = processar_colecoes_produto(
                cursor_giv,
                cursor_web,
                tabelas_web_produto['colecao'],
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            mapas_auxiliares_produto['colecao'] = mapa_colecoes
            resumos.append(resumo)
        if 'grade' in tabelas_selecionadas:
            gui_progress_tabela('grade')
            mapa_tamanhos_grade = mapas_auxiliares_produto.get('tamanho')
            if mapa_tamanhos_grade is None:
                mapa_tamanhos_grade = carregar_mapas_auxiliares_produto(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_produto,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                ).get('tamanho', {})
            mapa_grades, tamanhos_por_grade_produto, resumo = processar_grades_produto(
                cursor_giv,
                cursor_web,
                tabelas_web_produto['grade'],
                tabelas_web_produto['grade_tamanho'],
                mapa_tamanhos_grade,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            mapas_auxiliares_produto['grade'] = mapa_grades
            resumos.append(resumo)
        if 'fornecedor' in tabelas_selecionadas:
            gui_progress_tabela('fornecedor')
            resumos.append(
                processar_fornecedores(
                    cursor_giv,
                    cursor_web,
                    tabela_web_fornecedor,
                    cidades_giv,
                    cidades_web,
                    tenant_id,
                    session_api_cep,
                    base_url_api_cep,
                    cd_empresa_giv_filtro
                )
            )
        if 'usuario' in tabelas_selecionadas:
            gui_progress_tabela('usuario')
            resumo_usuario = processar_usuarios(
                cursor_giv,
                cursor_web,
                tabela_web_usuario,
                cidades_giv,
                cidades_web,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            mapa_usuarios = resumo_usuario.get('mapa_usuarios', {})
            resumos.append(resumo_usuario)
        if 'cliente' in tabelas_selecionadas:
            gui_progress_tabela('cliente')
            resumos.append(
                processar_clientes(
                    cursor_giv,
                    cursor_web,
                    tabela_web_cliente,
                    cidades_giv,
                    cidades_web,
                    tenant_id,
                    cd_empresa,
                    session_api_cep,
                    base_url_api_cep,
                    mapa_usuarios,
                    cd_empresa_giv_filtro
                )
            )
        if 'banco' in tabelas_selecionadas:
            gui_progress_tabela('banco')
            mapa_bancos, resumo = processar_bancos(
                cursor_giv,
                cursor_web,
                tabelas_web_rotinas['banco'],
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            resumos.append(resumo)
        if 'banco_conta' in tabelas_selecionadas:
            gui_progress_tabela('banco_conta')
            if not mapa_bancos:
                mapa_bancos = carregar_mapa_banco_rotinas(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas['banco'],
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
            mapa_banco_contas, resumo = processar_banco_contas(
                cursor_giv,
                cursor_web,
                tabelas_web_rotinas['banco_conta'],
                mapa_bancos,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            resumos.append(resumo)
        if 'condicao_pagamento' in tabelas_selecionadas:
            gui_progress_tabela('condicao_pagamento')
            mapa_condicoes, resumos_condicao = processar_condicoes_pagamento(
                cursor_giv,
                cursor_web,
                tabelas_web_rotinas['condicao_pagamento'],
                tabelas_web_rotinas['condicao_pagamento_forma'],
                tabelas_web_rotinas['forma_pagamento'],
                tenant_id,
                cd_empresa_giv_filtro
            )
            resumos.extend(resumos_condicao)
        if 'cartao_administradora' in tabelas_selecionadas:
            gui_progress_tabela('cartao_administradora')
            mapa_cartao_administradoras, resumo = processar_cartao_administradoras(
                cursor_giv,
                cursor_web,
                tabelas_web_rotinas['cartao_administradora'],
                tenant_id,
                cd_empresa
            )
            resumos.append(resumo)
        if 'produto' in tabelas_selecionadas:
            gui_progress_tabela('produto')
            resumos.extend(
                processar_produtos(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_produto,
                    tenant_id,
                    cd_empresa,
                    mapa_status_produto,
                    mapa_grupos,
                    mapas_auxiliares_produto,
                    tamanhos_por_grade_produto,
                    cd_empresa_giv_produto
                )
            )

        if rotinas_documento_selecionadas:
            print()
            print("[...] Montando mapas em memoria para rotinas/documentos...")
            if not mapa_bancos and tabelas_web_rotinas.get('banco'):
                mapa_bancos = carregar_mapa_banco_rotinas(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas['banco'],
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
            if mapa_condicoes is None:
                sql_condicoes_giv = (
                    "SELECT cd_condicao_pagto, ds_condicao_pagto "
                    "FROM condicao_pagto "
                    f"WHERE cd_empresa = {int(cd_empresa_giv_filtro)} "
                    "ORDER BY cd_condicao_pagto"
                    if tabela_giv_tem_coluna(cursor_giv, 'condicao_pagto', 'cd_empresa')
                    else "SELECT cd_condicao_pagto, ds_condicao_pagto FROM condicao_pagto ORDER BY cd_condicao_pagto"
                )
                mapa_condicoes = buscar_mapa_giv_para_web_por_nome(
                    cursor_giv,
                    cursor_web,
                    sql_condicoes_giv,
                    'cd_condicao_pagto',
                    'ds_condicao_pagto',
                    tabelas_web_rotinas['condicao_pagamento'],
                    'cd_condicao_pagto',
                    'ds_condicao_pagto',
                    tenant_id=tenant_id
                )
            mapa_usuario_rotinas, usuario_padrao = carregar_mapa_usuario_rotinas(
                cursor_giv,
                cursor_web,
                tabela_web_usuario,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            if mapa_usuarios:
                mapa_usuario_rotinas.update(mapa_usuarios)

            mapa_cliente_rotinas = carregar_mapa_cliente_rotinas(
                cursor_giv,
                cursor_web,
                tabela_web_cliente,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            cliente_padrao_rotinas = buscar_cliente_padrao_web(
                cursor_web,
                tabela_web_cliente,
                tenant_id,
                cd_empresa
            )
            mapas_aux_rotinas = carregar_mapas_auxiliares_produto(
                cursor_giv,
                cursor_web,
                tabelas_web_produto,
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )

            mapas_rotinas = {
                'fornecedor': buscar_mapa_fornecedor_produto(
                    cursor_giv,
                    cursor_web,
                    tabela_web_fornecedor,
                    tenant_id,
                    cd_empresa_giv_filtro
                ),
                'cliente': mapa_cliente_rotinas,
                'cliente_padrao': cliente_padrao_rotinas,
                'usuario': mapa_usuario_rotinas,
                'usuario_padrao': usuario_padrao,
                'condicao_pagamento': mapa_condicoes,
                'condicao_padrao': buscar_codigo_minimo_web(
                    cursor_web,
                    tabelas_web_rotinas['condicao_pagamento'],
                    'cd_condicao_pagto',
                    tenant_id=tenant_id
                ),
                'produto': carregar_mapa_produto_rotinas(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_produto,
                    tenant_id,
                    mapas_aux_rotinas
                ),
                'cfop': carregar_mapa_cfop(cursor_web, tabelas_web_rotinas['cfop']),
                'empresa': carregar_dados_empresa(
                    cursor_web,
                    tabelas_web_rotinas['empresa'],
                    cd_empresa,
                    tenant_id
                ),
                'banco': mapa_bancos,
            }
            mapas_rotinas['nf_saida'] = carregar_mapa_nf_saida_existente(
                cursor_web,
                tabelas_web_rotinas['nota_fiscal_saida'],
                tenant_id,
                cd_empresa,
                cd_empresa_giv_filtro
            )
            mapas_rotinas['nf_entrada'] = carregar_mapa_nf_entrada_existente(
                cursor_web,
                tabelas_web_rotinas['nota_fiscal_entrada'],
                mapas_rotinas['fornecedor'],
                tenant_id,
                cd_empresa
            )

            mapas_forma, forma_padrao = carregar_mapa_forma_pagamento(
                cursor_giv,
                cursor_web,
                tabelas_web_rotinas['forma_pagamento'],
                tenant_id,
                cd_empresa_giv_filtro
            )
            mapas_rotinas['forma_pagamento'] = mapas_forma
            mapas_rotinas['forma_pagamento_padrao'] = forma_padrao
            if mapa_cartao_administradoras:
                mapas_rotinas['cartao_administradora'] = mapa_cartao_administradoras
            elif tabelas_web_rotinas.get('cartao_administradora'):
                mapas_rotinas['cartao_administradora'] = carregar_mapa_cartao_administradora_rotinas(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas['cartao_administradora'],
                    tenant_id,
                    cd_empresa
                )
            if mapa_banco_contas:
                mapas_rotinas['banco_conta'] = mapa_banco_contas
            else:
                mapas_rotinas['banco_conta'] = carregar_mapa_banco_conta_rotinas(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas['banco_conta'],
                    mapas_rotinas.get('banco', {}),
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
            mapas_rotinas.update({
                'unidade': mapas_aux_rotinas.get('unidade', {}),
                'cor': mapas_aux_rotinas.get('cor', {}),
                'tamanho': mapas_aux_rotinas.get('tamanho', {}),
            })

            if ROTINAS_COM_PRODUTO.intersection(tabelas_selecionadas):
                resumo_produtos_faltantes = cadastrar_produtos_faltantes_rotinas(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_produto,
                    tabelas_selecionadas,
                    tenant_id,
                    cd_empresa,
                    mapas_rotinas['produto'],
                    cd_empresa_giv_produto,
                    mapas_auxiliares_produto,
                    mapas_rotinas.get('nf_saida')
                )
                if resumo_produtos_faltantes.get('lidos') or resumo_produtos_faltantes.get('inseridos') or resumo_produtos_faltantes.get('erros'):
                    resumos.append(resumo_produtos_faltantes)

            if 'condicional' in tabelas_selecionadas:
                gui_progress_tabela('condicional')
                mapa_condicional, resumos_condicional = processar_condicionais_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                mapas_rotinas['condicional'] = mapa_condicional
                resumos.extend(resumos_condicional)
            if 'pedido_compra' in tabelas_selecionadas:
                gui_progress_tabela('pedido_compra')
                _, resumos_pedido = processar_pedido_compra_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                resumos.extend(resumos_pedido)
            if 'nota_fiscal_entrada' in tabelas_selecionadas:
                gui_progress_tabela('nota_fiscal_entrada')
                mapa_nf_entrada, resumos_nf_entrada = processar_nota_fiscal_entrada_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                mapas_rotinas['nf_entrada'].update(mapa_nf_entrada)
                resumos.extend(resumos_nf_entrada)
            if 'prevenda' in tabelas_selecionadas:
                gui_progress_tabela('prevenda')
                _, resumos_prevenda = processar_prevenda_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                resumos.extend(resumos_prevenda)
            if 'nota_fiscal_saida' in tabelas_selecionadas:
                gui_progress_tabela('nota_fiscal_saida')
                mapa_nf_saida, resumos_nf_saida = processar_nota_fiscal_saida_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                mapas_rotinas['nf_saida'].update(mapa_nf_saida)
                resumos.extend(resumos_nf_saida)
            if 'titulo_receber' in tabelas_selecionadas:
                gui_progress_tabela('titulo_receber')
                _, resumos_receber = processar_titulo_receber_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                resumos.extend(resumos_receber)
            if 'titulo_pagar' in tabelas_selecionadas:
                gui_progress_tabela('titulo_pagar')
                _, resumos_pagar = processar_titulo_pagar_rotina(
                    cursor_giv,
                    cursor_web,
                    tabelas_web_rotinas,
                    mapas_rotinas,
                    tenant_id,
                    cd_empresa,
                    cd_empresa_giv_filtro
                )
                resumos.extend(resumos_pagar)

        imprimir_resumo(resumos)
        comparar_totais_pos_conversao(
            cursor_giv,
            cursor_web,
            tabelas_selecionadas,
            tabelas_web_reversao,
            tenant_id,
            cd_empresa,
            cd_empresa_giv_filtro
        )
        gravar_reverter_txt(estado_reversao, tabelas_web_reversao)
        confirmar_transacao(conn_web)

    except ErroConfiguracaoConversao as e:
        print()
        print("=" * 60)
        print("[PARADA SEGURA] Pre-requisito ausente no banco Web.")
        print(f"  {e}")
        print("  Nenhum dado foi efetivado; a transacao inteira sera desfeita.")
        print("=" * 60)
        try:
            conn_web.rollback()
            print("[OK] ROLLBACK realizado com sucesso.")
        except Exception:
            print("[ERRO] Falha ao realizar rollback.")
        sys.exit(1)

    except Exception as e:
        print()
        print(f"[ERRO FATAL] {e}")
        print("[...] Realizando ROLLBACK automatico...")
        try:
            conn_web.rollback()
            print("[OK] ROLLBACK realizado com sucesso.")
        except Exception:
            print("[ERRO] Falha ao realizar rollback.")
        sys.exit(1)

    finally:
        cursor_giv.close()
        cursor_web.close()
        conn_giv.close()
        conn_web.close()
        print()
        print("[OK] Conexoes encerradas.")
        print("Fim da conversao.")


if __name__ == '__main__':
    main()
