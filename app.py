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
        r"^\s*(?:- )?\s*(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})",
        re.MULTILINE
    )
    
    pattern_utilidade = re.compile(
        r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL
    )
    
    ignore_redacao_final = re.compile(r"Assim sendo, opinamos por se dar à proposição a seguinte redação final, que está de acordo com o aprovado.")
    ignore_publicada_antes = re.compile(r"foi publicada na edição anterior.", re.IGNORECASE)

    proposicoes = []
    
    for match in pattern_prop.finditer(text):
        start_idx = match.start()
        end_idx = match.end()
        
        contexto_antes = text[max(0, start_idx - 200):start_idx]
        contexto_depois = text[end_idx:end_idx + 250]
        
        if ignore_redacao_final.search(contexto_antes) or ignore_publicada_antes.search(contexto_depois):
            continue
            
        subseq_text = text[match.end():match.end() + 250]
        
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
    def classify_req(segment):
        segment_lower = segment.lower()
        if "requer seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "em requerem seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "manifestação de pesar" in segment_lower: return "Manifestação de pesar"
        if "manifestação de repúdio" in segment_lower: return "Manifestação de repúdio"
        if "moção de aplauso" in segment_lower: return "Moção de aplauso"
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
        nums_in_block = re.findall(r'\d{2}\.?\d{3}/\d{4}', block)
        if not nums_in_block: continue
        num_part, ano = nums_in_block[0].replace(".", "").split("/")
        classif = classify_req(block)
        requerimentos.append(["RQN", num_part, ano, "", "", classif])

    for match in rqc_pattern.finditer(text):
        start_idx = match.start()
        next_match = re.search(r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})", text[start_idx + 1:], flags=re.MULTILINE)
        end_idx = (next_match.start() + start_idx + 1) if next_match else len(text)
        block = text[start_idx:end_idx].strip()
        nums_in_block = re.findall(r'\d{2}\.?\d{3}/\d{4}', block)
        if not nums_in_block: continue
        num_part, ano = nums_in_block[0].replace(".", "").split("/")
        classif = classify_req(block)
        requerimentos.append(["RQC", num_part, ano, "", "", classif])
    
    header_match = nao_recebidas_header_pattern.search(text)
    if header_match:
        start_idx = header_match.end()
        next_section_pattern = re.compile(r"^\s*(\*?)\s*.*\s*(\*?)\s*$", re.MULTILINE)
        next_section_match = next_section_pattern.search(text, start_idx)
        end_idx = next_section_match.start() if next_section_match else len(text)
        nao_recebidos_block = text[start_idx:end_idx]
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
            seen.add(key)
            unique_reqs.append(r)
    df_requerimentos = pd.DataFrame(unique_reqs)

    # ==========================
    # ABA 4: Pareceres
    # ==========================
    tipo_map_parecer = {
        "PROJETO DE LEI": "PL",
        "PL": "PL",
        "PROJETO DE LEI COMPLEMENTAR": "PLC",
        "PLC": "PLC",
        "PROJETO DE RESOLUÇÃO": "PRE",
        "PRE": "PRE",
        "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
        "PEC": "PEC",
        "REQUERIMENTO": "RQN",   # genérico, pode depois refinar entre RQN/RQC
    }

    NUM = r'(?P<num>\d{1,5}(?:\.\d{3})*)'
    ANO = r'(?P<ano>\d{4})'
    TIPO = (
        r'(?P<tipo>'
        r'PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|'
        r'PLC|PL|PRE|PEC|REQUERIMENTO)'
    )

    emenda_titulo_re = re.compile(
        rf'^\s*EMENDA\s*N[ºo]\s*\d+\s+AO\s+(?:SUBSTITUTIVO\s*N[ºo]\s*\d+\s+AO\s+)?{TIPO}\s*N[ºo]?\s*{NUM}\s*/\s*{ANO}\b',
        re.IGNORECASE | re.MULTILINE
    )

    substitutivo_titulo_re = re.compile(
        rf'^\s*SUBSTITUTIVO\s*N[ºo]\s*\d+\s+AO\s+{TIPO}\s*N[ºo]?\s*{NUM}\s*/\s*{ANO}\b',
        re.IGNORECASE | re.MULTILINE
    )

    found = {}

    for m in emenda_titulo_re.finditer(text):
        tipo_ext = m.group('tipo').upper()
        sigla = tipo_map_parecer.get(tipo_ext, tipo_ext)
        numero = m.group('num').replace('.', '')
        ano = m.group('ano')
        key = (sigla, numero, ano)
        found.setdefault(key, set()).add('EMENDA')

    for m in substitutivo_titulo_re.finditer(text):
        tipo_ext = m.group('tipo').upper()
        sigla = tipo_map_parecer.get(tipo_ext, tipo_ext)
        numero = m.group('num').replace('.', '')
        ano = m.group('ano')
        key = (sigla, numero, ano)
        found.setdefault(key, set()).add('SUBSTITUTIVO')

    pareceres = []
    for (sigla, numero, ano), tipos in found.items():
        tipo_str = 'SUB/EMENDA' if tipos == {'SUBSTITUTIVO', 'EMENDA'} else next(iter(tipos))
        pareceres.append([sigla, numero, ano, tipo_str])

    df_pareceres = pd.DataFrame(pareceres)
    
    return {
        "Normas": df_normas,
        "Proposicoes": df_proposicoes,
        "Requerimentos": df_requerimentos,
        "Pareceres": df_pareceres
    }

def process_administrative_pdf(pdf_bytes):
    """
    Processa bytes de um arquivo PDF para extrair normas administrativas e retorna dados CSV.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        st.error(f"Erro ao abrir o arquivo PDF: {e}")
        return None

    resultados = []
    regex = re.compile(
        r'(DELIBERAÇÃO DA MESA|PORTARIA DGE|ORDEM DE SERVIÇO PRES/PSEC)\s+Nº\s+([\d\.]+)\/(\d{4})'
    )
    regex_dcs = re.compile(r'DECIS[ÃA]O DA 1ª-SECRETARIA')

    for page in doc:
        text = page.get_text("text")
        text = re.sub(r'\s+', ' ', text)

        for match in regex.finditer(text):
            tipo_texto = match.group(1)
            numero = match.group(2).replace('.', '')
            ano = match.group(3)

            if tipo_texto.startswith("DELIBERAÇÃO DA MESA"):
                sigla = "DLB"
            elif tipo_texto.startswith("PORTARIA"):
                sigla = "PRT"
            elif tipo_texto.startswith("ORDEM DE SERVIÇO"):
                sigla = "OSV"
            else:
                continue
            resultados.append([sigla, numero, ano])

        if regex_dcs.search(text):
            resultados.append(["DCS", "", ""])
    doc.close()

    output_csv = io.StringIO()
    writer = csv.writer(output_csv, delimiter="\t")
    writer.writerows(resultados)
    return output_csv.getvalue().encode('utf-8')

# --- Função Principal da Aplicação ---

def run_app():
    st.markdown("""
        <style>
        .title-container {
            text-align: center;
            background-color: #f0f0f0;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .main-title {
            color: #d11a2a;
            font-size: 3em;
            font-weight: bold;
            margin-bottom: 0;
        }
        .subtitle-gil {
            color: gray;
            font-size: 1.5em;
            margin-top: 5px;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="title-container">
            <h1 class="main-title">Extrator de Documentos Oficiais</h1>
            <h4 class="subtitle-gil">GERÊNCIA DE INFORMAÇÃO LEGISLATIVA - GIL/GDI</h4>
        </div>
    """, unsafe_allow_html=True)
    
    st.divider()

    diario_escolhido = st.radio(
        "Selecione o tipo de Diário para extração:",
        ('Legislativo', 'Administrativo', 'Executivo (Em breve)'),
        horizontal=True
    )
    
    st.divider()

    uploaded_file = st.file_uploader(f"Faça o upload do arquivo PDF do **Diário {diario_escolhido}**.", type="pdf")

    if uploaded_file is not None:
        try:
            if diario_escolhido == 'Legislativo':
                reader = PdfReader(uploaded_file)
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n+", "\n", text)
                
                with st.spinner('Extraindo dados do Diário do Legislativo...'):
                    extracted_data = process_legislative_pdf(text)

                output = io.BytesIO()
                excel_file_name = "Legislativo_Extraido.xlsx"
                
                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    for sheet_name, df in extracted_data.items():
                        df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                
                output.seek(0)
                download_data = output
                file_name = excel_file_name
                mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

            elif diario_escolhido == 'Administrativo':
                pdf_bytes = uploaded_file.read()
                
                with st.spinner('Extraindo dados do Diário Administrativo...'):
                    csv_data = process_administrative_pdf(pdf_bytes)

                download_data = csv_data
                file_name = "Administrativo_Extraido.csv"
                mime_type = "text/csv"

            else: 
                st.info("A funcionalidade para o Diário do Executivo ainda está em desenvolvimento.")
                download_data = None
                file_name = None
                mime_type = None

            if download_data:
                st.success("Dados extraídos com sucesso! ✅")
                st.divider()
                st.download_button(
                    label="Clique aqui para baixar o arquivo",
                    data=download_data,
                    file_name=file_name,
                    mime=mime_type
                )
                st.info(f"O download do arquivo **{file_name}** está pronto.")

        except Exception as e:
            st.error(f"Ocorreu um erro ao processar o arquivo: {e}")

# Executa a função principal
if __name__ == "__main__":
    run_app()
