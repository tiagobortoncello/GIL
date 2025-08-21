# Importar bibliotecas necessárias
import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz
import openpyxl

# --- Funções de Processamento ---
def process_legislative_pdf(text):
    # === ABA 1: Normas ===
    tipo_map_norma = {"LEI": "LEI", "RESOLUÇÃO": "RAL", "LEI COMPLEMENTAR": "LCP",
                      "EMENDA À CONSTITUIÇÃO": "EMC", "DELIBERAÇÃO DA MESA": "DLB"}
    pattern_norma = re.compile(
        r"^(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA) Nº (\d{1,5}(?:\.\d{0,3})?)(?:/(\d{4}))?(?:, DE .+ DE (\d{4}))?$",
        re.MULTILINE)
    normas = []
    for match in pattern_norma.finditer(text):
        tipo_extenso = match.group(1)
        numero_raw = match.group(2).replace(".", "")
        ano = match.group(3) if match.group(3) else match.group(4)
        if not ano: continue
        sigla = tipo_map_norma[tipo_extenso]
        normas.append([sigla, numero_raw, ano])
    df_normas = pd.DataFrame(normas)

    # === ABA 2: Proposições ===
    tipo_map_prop = {"PROJETO DE LEI": "PL", "PROJETO DE LEI COMPLEMENTAR": "PLC", "INDICAÇÃO": "IND",
                     "PROJETO DE RESOLUÇÃO": "PRE", "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
                     "MENSAGEM": "MSG", "VETO": "VET"}
    pattern_prop = re.compile(
        r"^\s*(?:- )?\s*(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})",
        re.MULTILINE)
    pattern_utilidade = re.compile(r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL)
    ignore_redacao_final = re.compile(
        r"Assim sendo, opinamos por se dar à proposição a seguinte redação final, que está de acordo com o aprovado.",
        re.IGNORECASE)
    ignore_publicada_antes = re.compile(r"foi publicad[ao] na edição anterior\.", re.IGNORECASE)
    proposicoes = []

    for match in pattern_prop.finditer(text):
        start_idx, end_idx = match.start(), match.end()
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
        categoria = "Utilidade Pública" if pattern_utilidade.search(subseq_text) else ""
        proposicoes.append([sigla, numero, ano, '', '', categoria])
    df_proposicoes = pd.DataFrame(proposicoes, columns=['Sigla', 'Número', 'Ano', 'Categoria 1', 'Categoria 2', 'Categoria'])

    # === ABA 3: Requerimentos ===
    def classify_req(segment):
        segment_lower = segment.lower()
        if "requer seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "requerem seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "manifestação de pesar" in segment_lower: return "Manifestação de pesar"
        if "manifestação de repúdio" in segment_lower: return "Manifestação de repúdio"
        if "moção de aplauso" in segment_lower: return "Moção de aplauso"
        if "requer seja formulada manifestação de apoio" in segment_lower: return "Manifestação de apoio"
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
    df_requerimentos = pd.DataFrame(unique_reqs, columns=['Sigla', 'Número', 'Ano', '', '', 'Classificação'])

    # === ABA 4: Pareceres (simplificado) ===
    df_pareceres = pd.DataFrame()

    return {
        "Normas": df_normas,
        "Proposicoes": df_proposicoes,
        "Requerimentos": df_requerimentos,
        "Pareceres": df_pareceres
    }

# ==========================
# Função Principal Streamlit
# ==========================
def run_app():
    st.title("Extrator de Documentos Oficiais")
    uploaded_file = st.file_uploader("Upload PDF", type="pdf")
    if uploaded_file:
        reader = PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text: text += page_text + "\n"
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n+", "\n", text)

        with st.spinner("Extraindo dados..."):
            extracted_data = process_legislative_pdf(text)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for sheet_name, df in extracted_data.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                
                if sheet_name == "Requerimentos":
                    ws = writer.sheets[sheet_name]
                    start_col = df.shape[1]
                    for row_idx, row in enumerate(df.itertuples(index=False), start=1):
                        classificacao = row[-1]
                        if classificacao:
                            ws.merge_cells(
                                start_row=row_idx + 1,
                                start_column=start_col + 1,
                                end_row=row_idx + 1,
                                end_column=start_col + 8
                            )
                            ws.cell(row=row_idx + 1, column=start_col + 1).value = classificacao

        output.seek(0)
        st.download_button("Baixar Excel", data=output, file_name="Extracao_Legislativo.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    run_app()
