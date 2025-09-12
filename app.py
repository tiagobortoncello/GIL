# -*- coding: utf-8 -*-
# ======================================
# Extrator de Documentos Oficiais (Streamlit)
# Upload OU Link para PDF
# ======================================

# --- Importações ---
import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz # PyMuPDF
import requests
import pdfplumber

# --- Constantes e Mapeamentos ---
TIPO_MAP_NORMA = {
    "LEI": "LEI",
    "RESOLUÇÃO": "RAL",
    "LEI COMPLEMENTAR": "LCP",
    "EMENDA À CONSTITUIÇÃO": "EMC",
    "DELIBERAÇÃO DA MESA": "DLB"
}

TIPO_MAP_PROP = {
    "PROJETO DE LEI": "PL",
    "PROJETO DE LEI COMPLEMENTAR": "PLC",
    "INDICAÇÃO": "IND",
    "PROJETO DE RESOLUÇÃO": "PRE",
    "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
    "MENSAGEM": "MSG",
    "VETO": "VET"
}

SIGLA_MAP_PARECER = {
    "requerimento": "RQN",
    "projeto de lei": "PL",
    "pl": "PL",
    "projeto de resolução": "PRE",
    "pre": "PRE",
    "proposta de emenda à constituição": "PEC",
    "pec": "PEC",
    "projeto de lei complementar": "PLC",
    "plc": "PLC",
    "emendas ao projeto de lei": "EMENDA"
}

# Dicionário para converter meses em português para número
meses = {
    "JANEIRO": "01", "FEVEREIRO": "02", "MARÇO": "03", "MARCO": "03",
    "ABRIL": "04", "MAIO": "05", "JUNHO": "06", "JULHO": "07",
    "AGOSTO": "08", "SETEMBRO": "09", "OUTUBRO": "10", "NOVEMBRO": "11", "DEZEMBRO": "12"
}

# --- Funções Utilitárias ---
def classify_req(segment: str) -> str:
    """ Classifica um requerimento com base no texto do segmento. """
    segment_lower = segment.lower()
    if "seja formulado voto de congratulações" in segment_lower:
        return "Voto de congratulações"
    if "manifestação de pesar" in segment_lower:
        return "Manifestação de pesar"
    if "manifestação de repúdio" in segment_lower:
        return "Manifestação de repúdio"
    if "moção de aplauso" in segment_lower:
        return "Moção de aplauso"
    if "r seja formulada manifestação de apoio" in segment_lower:
        return "Manifestação de apoio"
    return ""

# --- Classes de Processamento ---
class LegislativeProcessor:
    """ Processa o texto de um Diário do Legislativo, extraindo normas, proposições, requerimentos e pareceres. """
    def __init__(self, text: str):
        self.text = text

    def process_normas(self) -> pd.DataFrame:
        pattern = re.compile(
            r"^(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA) Nº (\d{1,5}(?:\.\d{0,3})?)(?:/(\d{4}))?(?:, DE .+ DE (\d{4}))?$",
            re.MULTILINE
        )
        normas = []
        for match in pattern.finditer(self.text):
            tipo_extenso = match.group(1)
            numero_raw = match.group(2).replace(".", "")
            ano = match.group(3) if match.group(3) else match.group(4)
            if not ano:
                continue
            sigla = TIPO_MAP_NORMA[tipo_extenso]
            normas.append([sigla, numero_raw, ano])
        return pd.DataFrame(normas)

    def process_proposicoes(self) -> pd.DataFrame:
        pattern_prop = re.compile(
            r"^\s*(?:- )?\s*(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.MULTILINE
        )
        pattern_utilidade = re.compile(r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL)
        ignore_redacao_final = re.compile(r"opinamos por se dar à proposição a seguinte redação final", re.IGNORECASE)
        ignore_publicada_antes = re.compile(r"foi publicad[ao] na edição anterior\.", re.IGNORECASE)
        ignore_em_epigrafe = re.compile(r"Na publicação da matéria em epígrafe", re.IGNORECASE)

        proposicoes = []
        for match in pattern_prop.finditer(self.text):
            start_idx = match.start()
            end_idx = match.end()
            contexto_antes = self.text[max(0, start_idx - 200):start_idx]
            contexto_depois = self.text[end_idx:end_idx + 250]

            if ignore_em_epigrafe.search(contexto_depois):
                continue
            if ignore_redacao_final.search(contexto_antes) or ignore_publicada_antes.search(contexto_depois):
                continue
            subseq_text = self.text[end_idx:end_idx + 250]
            if "(Redação do Vencido)" in subseq_text:
                continue

            tipo_extenso = match.group(1)
            numero_ano = match.group(2).replace(".", "")
            numero, ano = numero_ano.split("/")
            sigla = TIPO_MAP_PROP[tipo_extenso]
            categoria = "UP" if pattern_utilidade.search(subseq_text) else ""
            proposicoes.append([sigla, numero, ano, categoria])

        return pd.DataFrame(
            proposicoes,
            columns=['Sigla', 'Número', 'Ano', 'Categoria']
        )

        def process_requerimentos(self) -> pd.DataFrame:
        requerimentos = []
        ignore_pattern = re.compile(
            r"Ofício nº .*?,.*?relativas ao Requerimento\s*nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        aprovado_pattern = re.compile(
            r"(da Comissão.*?, informando que, na.*?foi aprovado o Requerimento\s*nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4}))",
            re.IGNORECASE | re.DOTALL
        )
        reqs_to_ignore = set()
        for match in ignore_pattern.finditer(self.text):
            numero_ano = match.group(1).replace(".", "")
            reqs_to_ignore.add(numero_ano)

        for match in aprovado_pattern.finditer(self.text):
            num_part = match.group(2).replace('.', '')
            ano = match.group(3)
            numero_ano = f"{num_part}/{ano}"
            reqs_to_ignore.add(numero_ano)

        # --- NOVA REGRA: requerimentos prejudicados ---
        prejudicado_pattern = re.compile(
            r"prejudicialidade\s+dos\s+Requerimentos?\s+em\s+Comissão\s+nºs?\s+([\d\.\s/,e]+)",
            re.IGNORECASE
        )
        for match in prejudicado_pattern.finditer(self.text):
            numeros_raw = match.group(1)
            # Captura cada número/ano como "16391/2025"
            for num_ano in re.findall(r"(\d{1,5}(?:\.\d{0,3})?)/(\d{4})", numeros_raw):
                num_part = num_ano[0].replace(".", "")
                ano = num_ano[1]
                numero_ano = f"{num_part}/{ano}"
                if numero_ano not in reqs_to_ignore:
                    requerimentos.append(["RQC", num_part, ano, "", "", "Prejudicado"])
        # --- FIM DA NOVA REGRA ---

        # (continua com as outras regras já existentes)
        # ...


    def process_pareceres(self) -> pd.DataFrame:
        found_projects = {}
        pareceres_start_pattern = re.compile(r"TRAMITAÇÃO DE PROPOSIÇÕES")
        votacao_pattern = re.compile(
            r"(Votação do Requerimento[\s\S]*?)(?=Votação do Requerimento|Diário do Legislativo|Projetos de Lei Complementar|Diário do Legislativo - Poder Legislativo|$)",
            re.IGNORECASE
        )
        pareceres_start = pareceres_start_pattern.search(self.text)
        if not pareceres_start:
            return pd.DataFrame(columns=['Sigla', 'Número', 'Ano', 'Tipo'])

        pareceres_text = self.text[pareceres_start.end():]
        # remove blocos de votação
        clean_text = pareceres_text
        for match in votacao_pattern.finditer(pareceres_text):
            clean_text = clean_text.replace(match.group(0), "")

        # Adiciona a nova regra para "EMENDAS AO PROJETO DE LEI"
        emenda_projeto_lei_pattern = re.compile(
            r"EMENDAS AO PROJETO DE LEI Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in emenda_projeto_lei_pattern.finditer(clean_text):
            numero_raw = match.group(1).replace('.', '')
            ano = match.group(2)
            project_key = ("PL", numero_raw, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        emenda_completa_pattern = re.compile(
            r"EMENDA Nº (\d+)\s+AO\s+(?:SUBSTITUTIVO Nº \d+\s+AO\s+)?PROJETO DE LEI(?: COMPLEMENTAR)? Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE
        )
        emenda_pattern = re.compile(r"^(?:\s*)EMENDA Nº (\d+)\s*", re.MULTILINE)
        substitutivo_pattern = re.compile(r"^(?:\s*)SUBSTITUTIVO Nº (\d+)\s*", re.MULTILINE)
        project_pattern = re.compile(
            r"Conclusão\s*([\s\S]*?)(Projeto de Lei|PL|Projeto de Resolução|PRE|Proposta de Emenda à Constituição|PEC|Projeto de Lei Complementar|PLC|Requerimento)\s+(?:nº|Nº)?\s*(\d{1,4}(?:\.\d{1,3})?)\s*/\s*(\d{4})",
            re.IGNORECASE | re.DOTALL
        )

        for match in emenda_completa_pattern.finditer(clean_text):
            numero = match.group(2).replace(".", "")
            ano = match.group(3)
            sigla = "PLC" if "COMPLEMENTAR" in match.group(0).upper() else "PL"
            project_key = (sigla, numero, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        all_matches = sorted(
            list(emenda_pattern.finditer(clean_text)) + list(substitutivo_pattern.finditer(clean_text)),
            key=lambda x: x.start()
        )

        for title_match in all_matches:
            text_before_title = clean_text[:title_match.start()]
            last_project_match = None
            for match in project_pattern.finditer(text_before_title):
                last_project_match = match

            if last_project_match:
                sigla_raw = last_project_match.group(2)
                sigla = SIGLA_MAP_PARECER.get(sigla_raw.lower(), sigla_raw.upper())
                numero = last_project_match.group(3).replace(".", "")
                ano = last_project_match.group(4)
                project_key = (sigla, numero, ano)
                item_type = "EMENDA" if "EMENDA" in title_match.group(0).upper() else "SUBSTITUTIVO"
                if project_key not in found_projects:
                    found_projects[project_key] = set()
                found_projects[project_key].add(item_type)
            
        # Adiciona a nova regra
        emenda_projeto_lei_pattern = re.compile(r"EMENDAS AO PROJETO DE LEI Nº (\d{1,4}\.?\d{0,3})/(\d{4})", re.IGNORECASE)
        for match in emenda_projeto_lei_pattern.finditer(clean_text):
            numero_raw = match.group(1).replace('.', '')
            ano = match.group(2)
            project_key = ("PL", numero_raw, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        pareceres = []
        for (sigla, numero, ano), types in found_projects.items():
            type_str = "SUB/EMENDA" if len(types) > 1 else list(types)[0]
            pareceres.append([sigla, numero, ano, type_str])

        return pd.DataFrame(pareceres)

    def process_all(self) -> dict:
        df_normas = self.process_normas()
        df_proposicoes = self.process_proposicoes()
        df_requerimentos = self.process_requerimentos()
        df_pareceres = self.process_pareceres()
        return {
            "Normas": df_normas,
            "Proposicoes": df_proposicoes,
            "Requerimentos": df_requerimentos,
            "Pareceres": df_pareceres
        }

class AdministrativeProcessor:
    """ Processa bytes de um Diário Administrativo, extraindo normas e retornando dados CSV. """
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes

    def process_pdf(self):
        try:
            doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
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
                sigla = {
                    "DELIBERAÇÃO DA MESA": "DLB",
                    "PORTARIA DGE": "PRT",
                    "ORDEM DE SERVIÇO PRES/PSEC": "OSV"
                }.get(tipo_texto, None)
                if sigla:
                    resultados.append([sigla, numero, ano])
            if regex_dcs.search(text):
                resultados.append(["DCS", "", ""])
        doc.close()
        return resultados

    def to_csv(self):
        resultados = self.process_pdf()
        if resultados is None:
            return None
        output_csv = io.StringIO()
        writer = csv.writer(output_csv, delimiter="\t")
        writer.writerows(resultados)
        return output_csv.getvalue().encode('utf-8')

class ExecutiveProcessor:
    """Processa o texto de um Diário do Executivo, extraindo normas e alterações."""
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes

    def process_pdf(self) -> pd.DataFrame:
        trechos = []
        try:
            with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
                for i, pagina in enumerate(pdf.pages, start=1):
                    largura, altura = pagina.width, pagina.height
                    for col_num, (x0, x1) in enumerate([(0, largura/2), (largura/2, largura)], start=1):
                        coluna = pagina.crop((x0, 0, x1, altura)).extract_text(layout=True) or ""
                        texto_limpo = re.sub(r'\s+', ' ', coluna).strip()
                        trechos.append({
                            "pagina": i,
                            "coluna": col_num,
                            "texto": texto_limpo
                        })
        except Exception as e:
            st.error(f"Erro ao extrair texto do PDF do Executivo: {e}")
            return pd.DataFrame()

        start_idx = next((idx for idx, t in enumerate(trechos) if re.search(r'Leis\s*e\s*Decretos', t["texto"], re.IGNORECASE)), None)
        end_idx = next((idx for idx, t in reversed(list(enumerate(trechos))) if re.search(r'Atos\s*do\s*Governador', t["texto"], re.IGNORECASE)), None)

        if start_idx is None or end_idx is None or start_idx > end_idx:
            st.warning("Não foi encontrado o trecho de 'Leis e Decretos'.")
            return pd.DataFrame()

        norma_regex = re.compile(
            r'\b(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]\s*([\d\s\.]+),\s*DE\s+([A-Z\s\d]+)\b'
        )
        comandos_regex = re.compile(
            r'(Ficam\s+revogados|Fica\s+acrescentado|Ficam\s+alterados|passando\s+o\s+item|passa\s+a\s+vigorar|passam\s+a\s+vigorar)',
            re.IGNORECASE
        )
        norma_alterada_regex = re.compile(
            r'(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]?\s*([\d\s\./]+)(?:,\s*de\s*(.*?\d{4})?)?',
            re.IGNORECASE
        )
        mapa_tipos = {
            "LEI": "LEI",
            "LEI COMPLEMENTAR": "LCP",
            "DECRETO": "DEC",
            "DECRETO NE": "DNE"
        }

        dados = []
        for t in trechos[start_idx:end_idx+1]:
            pagina = t["pagina"]
            coluna = t["coluna"]
            texto = t["texto"]

            eventos = []
            for m in norma_regex.finditer(texto):
                eventos.append(('published', m.start(), m))
            for c in comandos_regex.finditer(texto):
                eventos.append(('command', c.start(), c))
            eventos.sort(key=lambda e: e[1])

            ultima_norma = None
            seen_alteracoes = set()

            for ev in eventos:
                tipo_ev, pos_ev, match_obj = ev
                command_text = match_obj.group(0).lower()

                if tipo_ev == 'published':
                    match = match_obj
                    tipo_raw = match.group(1).strip()
                    tipo = mapa_tipos.get(tipo_raw.upper(), tipo_raw)
                    numero = match.group(2).replace(" ", "").replace(".", "")
                    data_texto = match.group(3).strip()

                    try:
                        partes = data_texto.split(" DE ")
                        dia = partes[0].zfill(2)
                        mes = meses[partes[1]]
                        ano = partes[2]
                        sancao = f"{dia}/{mes}/{ano}"
                    except:
                        sancao = ""

                    linha = {
                        "Página": pagina,
                        "Coluna": coluna,
                        "Sanção": sancao,
                        "Tipo": tipo,
                        "Número": numero,
                        "Alterações": ""
                    }
                    dados.append(linha)
                    ultima_norma = linha
                    seen_alteracoes = set()

                elif tipo_ev == 'command':
                    if ultima_norma is None:
                        continue

                    raio = 150
                    start_block = max(0, pos_ev - raio)
                    end_block = min(len(texto), pos_ev + raio)
                    bloco = texto[start_block:end_block]

                    if 'revogado' in command_text:
                        alteracoes_para_processar = list(norma_alterada_regex.finditer(bloco))
                    else:
                        alteracoes_candidatas = list(norma_alterada_regex.finditer(bloco))
                        if not alteracoes_candidatas:
                            continue
                        
                        pos_comando_no_bloco = pos_ev - start_block
                        melhor_candidato = min(
                            alteracoes_candidatas,
                            key=lambda m: abs(m.start() - pos_comando_no_bloco)
                        )
                        alteracoes_para_processar = [melhor_candidato]

                    if not alteracoes_para_processar:
                        continue

                    for alt in alteracoes_para_processar:
                        tipo_alt_raw = alt.group(1).strip()
                        tipo_alt = mapa_tipos.get(tipo_alt_raw.upper(), tipo_alt_raw)
                        num_alt = alt.group(2).replace(" ", "").replace(".", "").replace("/", "")

                        data_texto_alt = alt.group(3)
                        ano_alt = ""
                        if data_texto_alt:
                            ano_match = re.search(r'(\d{4})', data_texto_alt)
                            if ano_match:
                                ano_alt = ano_match.group(1)
                        
                        chave_alt = f"{tipo_alt} {num_alt}"
                        if ano_alt:
                            chave_alt += f" {ano_alt}"

                        if tipo_alt == ultima_norma["Tipo"] and num_alt == ultima_norma["Número"]:
                            continue

                        if chave_alt in seen_alteracoes:
                            continue
                        seen_alteracoes.add(chave_alt)

                        if ultima_norma["Alterações"] == "":
                            ultima_norma["Alterações"] = chave_alt
                        else:
                            dados.append({
                                "Página": "",
                                "Coluna": "",
                                "Sanção": "",
                                "Tipo": "",
                                "Número": "",
                                "Alterações": chave_alt
                            })
        
        return pd.DataFrame(dados) if dados else pd.DataFrame()

    def to_csv(self):
        df = self.process_pdf()
        if df.empty:
            return None
        output_csv = io.StringIO()
        df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return output_csv.getvalue().encode('utf-8')

# --- Função Principal da Aplicação Streamlit ---
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
        ('Legislativo', 'Administrativo', 'Executivo'),
        horizontal=True
    )
    st.divider()

    # --- Modo de entrada do PDF ---
    modo = "Upload de arquivo"
    if diario_escolhido != 'Executivo':
        modo = st.radio(
            "Como deseja fornecer o PDF?",
            ("Upload de arquivo", "Link da internet"),
            horizontal=True
        )

    pdf_bytes = None
    if modo == "Upload de arquivo":
        uploaded_file = st.file_uploader(
            f"Faça o upload do arquivo PDF do **Diário {diario_escolhido}**.",
            type="pdf"
        )
        if uploaded_file is not None:
            pdf_bytes = uploaded_file.read()
    else:
        # Link da internet
        url = st.text_input("Cole o link do PDF aqui:")
        if url:
            try:
                with st.spinner("Baixando PDF..."):
                    resp = requests.get(url, timeout=30)
                    if resp.status_code == 200:
                        ctype = resp.headers.get("Content-Type", "")
                        if ("pdf" not in ctype.lower()) and (not url.lower().endswith(".pdf")):
                            st.warning("O link não parece apontar para um PDF (Content-Type != PDF). Tentarei processar mesmo assim.")
                        pdf_bytes = resp.content
                    else:
                        st.error(f"Falha ao baixar (status {resp.status_code}).")
            except Exception as e:
                st.error(f"Erro ao baixar o PDF: {e}")

    # --- Processamento ---
    if pdf_bytes:
        try:
            if diario_escolhido == 'Legislativo':
                # Usa PyPDF2 para extrair texto do PDF em memória
                reader = PdfReader(io.BytesIO(pdf_bytes))
                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                # Normalização básica
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n+", "\n", text)
                
                with st.spinner('Extraindo dados do Diário do Legislativo...'):
                    processor = LegislativeProcessor(text)
                    extracted_data = processor.process_all()

                    # Gera Excel em memória
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
                with st.spinner('Extraindo dados do Diário Administrativo...'):
                    processor = AdministrativeProcessor(pdf_bytes)
                    csv_data = processor.to_csv()
                    if csv_data:
                        download_data = csv_data
                        file_name = "Administrativo_Extraido.csv"
                        mime_type = "text/csv"
                    else:
                        download_data = None
                        file_name = None
                        mime_type = None
            else: # Executivo
                with st.spinner('Extraindo dados do Diário do Executivo...'):
                    processor = ExecutiveProcessor(pdf_bytes)
                    csv_data = processor.to_csv()
                    if csv_data:
                        download_data = csv_data
                        file_name = "Executivo_Extraido.csv"
                        mime_type = "text/csv"
                    else:
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

# --- Entrada ---
if __name__ == "__main__":
    run_app()

