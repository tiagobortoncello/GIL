# Importar bibliotecas necessárias
import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz

# --- Funções de Processamento ---

def process_legislative_pdf(text):
    """
    Extrai dados de normas, proposições, requerimentos e pareceres do Diário do Legislativo.
    """
    # ==========================
    # ABA 1: Normas
    # ==========================
    tipo_map_norma = {
        "LEI": "LEI", "RESOLUÇÃO": "RAL", "LEI COMPLEMENTAR": "LCP",
        "EMENDA À CONSTITUIÇÃO": "EMC", "DELIBERAÇÃO DA MESA": "DLB"
    }
    pattern_norma = re.compile(
        r"^(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA) Nº (\d{1,5}(?:\.\d{0,3})?)(?:/(\d{4}))?(?:, DE .+ DE (\d{4}))?$",
        re.MULTILINE
    )
    normas = []
    for match in pattern_norma.finditer(text):
        tipo_extenso = match.group(1)
        numero_raw = match.group(2).replace(".", "")
        ano = match.group(3) if match.group(3) else match.group(4)
        if not ano:
            continue
        sigla = tipo_map_norma[tipo_extenso]
        normas.append([sigla, numero_raw, ano])
    df_normas = pd.DataFrame(normas)

    # ==========================
    # ABA 2: Proposições
    # ==========================
    tipo_map_prop = {
        "PROJETO DE LEI": "PL", "PROJETO DE LEI COMPLEMENTAR": "PLC", "INDICAÇÃO": "IND",
        "PROJETO DE RESOLUÇÃO": "PRE", "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
        "MENSAGEM": "MSG", "VETO": "VET"
    }
    pattern_prop = re.compile(
        r"^(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})$",
        re.MULTILINE
    )
    
    # Linha corrigida para evitar o erro de sintaxe
    pattern_utilidade = re.compile(r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL)

    proposicoes = []
    
    for match in pattern_prop.finditer(text):
        start_idx = match.end()
        subseq_text = text[start_idx:start_idx + 250]
        
        if "(Redação do Vencido)" in subseq_text:
            continue
        
        tipo_extenso = match.group(1)
        numero_ano = match.group(2).replace(".", "")
        numero, ano = numero_ano.split("/")
        sigla = tipo_map_prop[tipo_extenso]
        
        categoria = ""
        if pattern_utilidade.search(subseq_text):
            categoria = "Utilidade Pública"
        
        # Inserindo duas colunas vazias após a coluna 'ano'
        proposicoes.append([sigla, numero, ano, '', '', categoria])
    
    # Adicionando os nomes das novas colunas ao DataFrame
    df_proposicoes = pd.DataFrame(proposicoes, columns=['Sigla', 'Número', 'Ano', 'Categoria 1', 'Categoria 2', 'Categoria'])
    
    # ==========================
    # ABA 3: Requerimentos
    # ==========================
    def classify_req(segment):
        segment_lower = segment.lower()
        
        # Regras de exclusão para requerimentos de audiência
        if "realizada audiência pública" in segment_lower or "audiência de convidados" in segment_lower:
            return ""
        
        # Classifica outros tipos de requerimentos
        if "voto de congratula" in segment_lower or "formulado voto de congratula" in segment_lower:
            return "Voto de congratulações"
        if "manifestação de pesar" in segment_lower:
            return "Manifestação de pesar"
        if "manifestação de repúdio" in segment_lower:
            return "Manifestação de repúdio"
        if "moção de aplauso" in segment_lower:
            return "Moção de aplauso"
        return ""

    requerimentos = []
    rqn_pattern = re.compile(r"^(?:\s*)(Nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
    rqc_pattern = re.compile(r"^(?:\s*)(nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
    nao_recebidas_header_pattern = re.compile(r"PROPOSIÇÕES\s*NÃO\s*RECEBIDAS", re.IGNORECASE)
    
    for match in rqn_pattern.finditer(text):
        start_idx = match.start()
        next_match = re.search(r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})", text[start_idx + 1:], flags=re.MULTILINE)
        end_idx = (next_match.start() + start_idx + 1) if next_match else len(text)
        block = text[start_idx:end_idx].strip()
        
        nums_in_block = re.findall(r'\d{2}\.?\d{
