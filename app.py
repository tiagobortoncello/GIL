# Importar bibliotecas necessárias
import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz
from datetime import datetime

# --- Funções de Processamento ---

def process_legislative_pdf(uploaded_file):
    """
    Extrai dados de normas, proposições, requerimentos e pareceres do Diário do Legislativo.
    """
    # ==========================
    # ABA 1: Normas
    # ==========================
    
    # Dicionário para mapear mês por extenso para número
    meses = {
        'JANEIRO': '01', 'FEVEREIRO': '02', 'MARÇO': '03', 'ABRIL': '04',
        'MAIO': '05', 'JUNHO': '06', 'JULHO': '07', 'AGOSTO': '08',
        'SETEMBRO': '09', 'OUTUBRO': '10', 'NOVEMBRO': '11', 'DEZEMBRO': '12'
    }

    # Regex para capturar o título da norma, o número e a data completa
    pattern_norma = re.compile(
        r"^(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA)\s+Nº\s+(\d{1,5}(?:\.\d{0,3})?),\s+DE\s+(\d{1,2})\s+DE\s+(" + "|".join(meses.keys()) + r")\s+DE\s+(\d{4})",
        re.MULTILINE | re.IGNORECASE
    )

    normas = []
    
    reader = PdfReader(uploaded_file)
    for page_num, page in enumerate(reader.pages, 1):
        text = page.extract_text()
        if not text:
            continue
        
        # O padrão de regex é aplicado ao texto da página atual
        for match in pattern_norma.finditer(text):
            dia = match.group(3)
            mes_extenso = match.group(4).upper()
            ano = match.group(5)
            
            mes_numero = meses.get(mes_extenso)
            if mes_numero:
                data_san = f"{int(dia):02d}/{mes_numero}/{ano}"
                # Adiciona à lista de normas as informações solicitadas
                normas.append([page_num, 1, data_san])

    # Cria o DataFrame com as colunas corretas
    df_normas = pd.DataFrame(normas, columns=['Página', 'Coluna', 'Data de sanção'])

    # ==========================
    # ABA 2: Proposições
    # ==========================
    # Mantém o código original para esta seção, se não houver problemas
    tipo_map_prop = {
        "PROJETO DE LEI": "PL", "PROJETO DE LEI COMPLEMENTAR": "PLC", "INDICAÇÃO": "IND",
        "PROJETO DE RESOLUÇÃO": "PRE", "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
        "MENSAGEM": "MSG", "VETO": "VET"
    }
    pattern_prop = re.compile(
        r"^(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})$",
        re.MULTILINE
    )
    
    pattern_utilidade = re.compile(
        r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL
    )

    proposicoes = []
    uploaded_file.seek(0)
    text_full = "".join([p.extract_text() for p in PdfReader(uploaded_file).pages if p.extract_text()])
    
    for match in pattern_prop.finditer(text_full):
        start_idx = match.end()
        subseq_text = text_full[start_idx:start_idx + 250]
        
        if "(Redação do Vencido)" in subseq_text:
            continue
        
        tipo_extenso = match.group(1)
        numero_ano = match.group(2).replace(".", "")
        numero, ano = numero_ano.split("/")
        sigla = tipo_map_prop[tipo_extenso]
        
        categoria = ""
        if pattern_utilidade.search(subseq_text):
            categoria = "Utilidade Pública"
        
        proposicoes.append([sigla, numero, ano, '', '', categoria])
    
    df_proposicoes = pd.DataFrame(proposicoes, columns=['Sigla', 'Número', 'Ano', 'Categoria 1', 'Categoria 2', 'Categoria'])
    
    # ==========================
    # ABA 3: Requerimentos
    # ==========================
    # Mantém o código original para esta seção, se não houver problemas
    def classify_req(segment):
        segment_lower = segment.lower()
        if "requer seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "em requerem seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "manifestação de pesar" in segment_lower: return "Manifestação de pesar"
        if "manifestação de repúdio" in segment_lower: return "Manifestação de repúdio"
        if "moção de aplauso" in segment_lower: return "Moção de aplauso"
        return ""

    requerimentos = []
    uploaded_file.seek(0)
    text_full = "".join([p.extract_text() for p in PdfReader(uploaded_file).pages if p.extract_text()])
    rqn_pattern = re.compile(r"^(?:\s*)(Nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
    rqc_pattern = re.compile(r"^(?:\s*)(nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
    nao_recebidas_header_pattern = re.compile(r"PROPOSIÇÕES\s*NÃO\s*RECEBIDAS", re.IGNORECASE)

    for match in rqn_pattern.finditer(text_full):
        start_idx = match.start()
        next_match = re.search(r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})", text_full[start_idx + 1:], flags=re.MULTILINE)
        end_idx = (next_match.start() + start_idx + 1) if next_match else len(text_full)
        block = text_full[start_idx:end_idx].strip()
        nums_in_block = re.findall(r'\d{2}\.?\d{3}/\d{4}', block)
        if not nums_in_block: continue
        num_part, ano = nums_in_block[0].replace(".", "").split("/")
        classif = classify_req(block)
        requerimentos.append(["RQN", num_part, ano, "", "", classif])

    for match in rqc_pattern.finditer(text_full):
        start_idx = match.start()
        next_match = re.search(r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})", text_full[start_idx + 1:], flags=re.MULTILINE)
        end_idx = (next_match.start() + start_idx + 1) if next_match else len(text_full)
        block = text_full[start_idx:end_idx].strip()
        nums_in_block = re.findall(r'\d{2}\.?\d{3}/\d{4}', block)
        if not nums_in_block: continue
        num_part, ano = nums_in_block[0].replace(".", "").split("/")
        classif = classify_req(block)
        requerimentos.append(["RQC", num_part, ano, "", "", classif])
    
    header_match = nao_recebidas_header_pattern.search(text_full)
    if header_match:
        start_idx = header_match.end()
        next_section_pattern = re.compile(r"^\s*(\*?)\s*.*\s*(\*?)\s*$", re.MULTILINE)
        next_section_match = next_section_pattern.search(text_full, start_idx)
        end_idx = next_section_match.start() if next_section_match else len(text_full)
        nao_recebidos_block = text_full[start_idx:end_idx]
        rqn_nao_recebido_pattern = re.compile(r"REQUERIMENTO Nº (\d{2}\.?\d{3}/\d{4})", re.IGNORECASE)
        for match in rqn_nao_recebido_pattern.finditer(nao_recebidos_block):
            numero_ano = match.group(1).replace(".", "")
            num_part, ano = numero_ano.split("/")
            requerimentos.append(["RQN", num_part, ano, "", "", "NÃO RECEBIDO"])

    unique_reqs = []
    seen = set()
    for r in requerimentos:
        key = (r[0], r[1], r[2])
        if key not in seen:
            seen.
