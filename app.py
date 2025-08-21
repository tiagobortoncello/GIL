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
    # Extração de Proposições, Requerimentos e Pareceres
    # ==========================
    
    # 1. Mapeamento de siglas
    tipo_map = {
        "PROJETO DE LEI": "PL", "PROJETO DE LEI COMPLEMENTAR": "PLC", "INDICAÇÃO": "IND",
        "PROJETO DE RESOLUÇÃO": "PRE", "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
        "MENSAGEM": "MSG", "VETO": "VET", "REQUERIMENTO": "RQN", "DELIBERAÇÃO DA MESA": "DLB"
    }
    
    # 2. Padrões de expressões regulares
    pattern_prop_start = re.compile(
        r"^\s*(?:- )?\s*(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})",
        re.MULTILINE
    )
    ignore_publicada_antes = re.compile(r"foi publicad[oa] na edição anterior.", re.IGNORECASE)
    ignore_redacao_final = re.compile(r"Assim sendo, opinamos por se dar à proposição a seguinte redação final, que está de acordo com o aprovado.")
    pattern_utilidade = re.compile(r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL)
    
    # Padrão para Requerimentos de Conclusão e de Votação
    rqn_pattern = re.compile(r"^(?:\s*)(Nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
    rqc_pattern = re.compile(r"^(?:\s*)(nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
    nao_recebidas_header_pattern = re.compile(r"PROPOSIÇÕES\s*NÃO\s*RECEBIDAS", re.IGNORECASE)
    
    # Novo padrão robusto para capturar Emendas e Substitutivos
    emenda_substitutivo_pattern = re.compile(
        r"(EMENDA|SUBSTITUTIVO)[\s\S]+Nº[\s\S]+(\d+)[\s\S]+AO[\s\S]+(?:(SUBSTITUTIVO)[\s\S]+Nº[\s\S]+(\d+)[\s\S]+AO[\s\S]+)?(PROJETO DE LEI|PL|PROJETO DE LEI COMPLEMENTAR|PLC|PROJETO DE RESOLUÇÃO|PRE|PROPOSTA DE EMENDA À CONSTITUIÇÃO|PEC|REQUERIMENTO)[\s\S]+Nº[\s\S]+(\d{1,}\.?\d{3})[\s\S]*/[\s\S]*(\d{4})",
        re.IGNORECASE | re.DOTALL
    )

    # 3. Extração principal
    documents = {}

    # Extrai proposições
    matches = list(pattern_prop_start.finditer(text))
    for i, match in enumerate(matches):
        start_idx = match.start()
        end_idx = matches[i+1].start() if i + 1 < len(matches) else len(text)
        proposicao_text = text[start_idx:end_idx]
        
        if ignore_redacao_final.search(proposicao_text) or ignore_publicada_antes.search(proposicao_text):
            continue
            
        tipo_extenso = match.group(1)
        numero_ano = match.group(2).replace(".", "")
        numero, ano = numero_ano.split("/")
        sigla = tipo_map[tipo_extenso]
        key = (sigla, numero, ano)
        
        category = "Utilidade Pública" if pattern_utilidade.search(proposicao_text) else ""
        
        documents[key] = {
            "sigla": sigla, "numero": numero, "ano": ano,
            "categoria": category, "pareceres": set(), "document_type": "Proposição"
        }

    # Extrai requerimentos
    def classify_req(segment):
        segment_lower = segment.lower()
        if "requer seja formulado voto de congratulações" in segment_lower: return "Voto de congratulações"
        if "manifestação de pesar" in segment_lower: return "Manifestação de pesar"
        if "moção de aplauso" in segment_lower: return "Moção de aplauso"
        return ""

    all_req_matches = list(rqn_pattern.finditer(text)) + list(rqc_pattern.finditer(text))
    all_req_matches.sort(key=lambda x: x.start())
    
    for i, match in enumerate(all_req_matches):
        start_idx = match.start()
        end_idx = all_req_matches[i+1].start() if i + 1 < len(all_req_matches) else len(text)
        block = text[start_idx:end_idx].strip()
        
        num_part, ano = match.group(2).replace(".", "").split("/")
        sigla = "RQN" if "Nº" in match.group(0) else "RQC"
        key = (sigla, num_part, ano)

        if key not in documents:
            documents[key] = {
                "sigla": sigla, "numero": num_part, "ano": ano,
                "categoria": classify_req(block), "pareceres": set(), "document_type": "Requerimento"
            }

    # Extrai pareceres e anexa aos documentos existentes
    for match in emenda_substitutivo_pattern.finditer(text):
        item_type_raw = match.group(1).upper()
        target_tipo_extenso = match.group(5)
        target_numero_raw = match.group(6).replace(".", "")
        target_ano = match.group(7)

        sigla = tipo_map.get(target_tipo_extenso.lower(), target_tipo_extenso.upper())
        key = (sigla, target_numero_raw, target_ano)
        
        if key in documents:
            documents[key]["pareceres"].add(item_type_raw)
            if match.group(3):
                documents[key]["pareceres"].add(match.group(3).upper())
    
    # 4. Formatação final dos dataframes
    final_proposicoes = []
    final_requerimentos = []
    final_pareceres = []
    
    for key, doc in documents.items():
        parecer_str = "/".join(sorted(list(doc["pareceres"])))
        if doc["document_type"] == "Proposição":
            final_proposicoes.append([doc["sigla"], doc["numero"], doc["ano"], doc["categoria"], parecer_str])
        elif doc["document_type"] == "Requerimento":
            final_requerimentos.append([doc["sigla"], doc["numero"], doc["ano"], doc["categoria"], parecer_str])
        
        if doc["pareceres"]:
            final_pareceres.append([doc["sigla"], doc["numero"], doc["ano"], parecer_str])

    df_proposicoes = pd.DataFrame(final_proposicoes, columns=['Sigla', 'Número', 'Ano', 'Categoria', 'Parecer'])
    df_requerimentos = pd.DataFrame(final_requerimentos, columns=['Sigla', 'Número', 'Ano', 'Categoria', 'Parecer'])
    df_pareceres = pd.DataFrame(final_pareceres, columns=['Sigla', 'Número', 'Ano', 'Tipo'])

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
    # --- Custom CSS para estilizar os títulos ---
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

    # --- Título e informações ---
    st.markdown("""
        <div class="title-container">
            <h1 class="main-title">Extrator de Documentos Oficiais</h1>
            <h4 class="subtitle-gil">GERÊNCIA DE INFORMAÇÃO LEGISLATIVA - GIL/GDI</h4>
        </div>
    """, unsafe_allow_html=True)
    
    st.divider()

    # --- Seletor de tipo de Diário ---
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

            else: # Executivo (placeholder)
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
