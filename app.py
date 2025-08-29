# Importar bibliotecas necessárias
import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz

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
    "plc": "PLC"
}

# --- Funções Utilitárias ---
def classify_req(segment):
    """
    Classifica um requerimento com base no texto do segmento.
    """
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
    """
    Classe para processar o texto de um Diário do Legislativo,
    extraindo normas, proposições, requerimentos e pareceres.
    """
    def __init__(self, text):
        self.text = text

    def process_normas(self):
        """Extrai normas do texto."""
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

    def process_proposicoes(self):
        """Extrai proposições do texto."""
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
            
            # Adicionada a verificação para "Na publicação da matéria em epígrafe"
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
            categoria = "Utilidade Pública" if pattern_utilidade.search(subseq_text) else ""
            proposicoes.append([sigla, numero, ano, '', '', categoria])
        return pd.DataFrame(proposicoes, columns=['Sigla', 'Número', 'Ano', 'Categoria 1', 'Categoria 2', 'Categoria'])

    def process_requerimentos(self):
        """Extrai requerimentos do texto, incluindo RQC, RQN e os não recebidos."""
        requerimentos = []
        
        # Expressão regular para o padrão a ser ignorado
        ignore_pattern = re.compile(
            r"Ofício nº .*?,.*?relativas ao Requerimento\s*nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        
        # Lista para armazenar requerimentos a serem ignorados
        reqs_to_ignore = set()
        for match in ignore_pattern.finditer(self.text):
            numero_ano = match.group(1).replace(".", "")
            reqs_to_ignore.add(numero_ano)

        # 1. Nova busca focada no padrão "É recebido pela presidência..."
        new_rqc_pattern = re.compile(
            r"É recebido pela presidência, submetido a votação e aprovado o\s+Requerimento(?:s)?(?: nº| Nº)? (\d{1,5}\.?\d{0,3})[/](\d{4})",
            re.IGNORECASE
        )
        for match in new_rqc_pattern.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Aprovado"])
        
        # 2. Busca por RQC (hipótese de requerimentos aprovados que foram "recebidos")
        rqc_pattern_aprovado = re.compile(
            r"recebido pela presidência, submetido a votação e aprovado o Requerimento(?:s)?(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)/\s*(\d{4})",
            re.IGNORECASE
        )
        for match in rqc_pattern_aprovado.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Aprovado"])
            
        # 3. Busca por RQN e RQC (lógica original)
        rqn_pattern = re.compile(r"^(?:\s*)(Nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
        rqc_old_pattern = re.compile(r"^(?:\s*)(nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)

        for pattern, sigla_prefix in [(rqn_pattern, "RQN"), (rqc_old_pattern, "RQC")]:
            for match in pattern.finditer(self.text):
                start_idx = match.start()
                next_match = re.search(r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})", self.text[start_idx + 1:], flags=re.MULTILINE)
                end_idx = (next_match.start() + start_idx + 1) if next_match else len(self.text)
                block = self.text[start_idx:end_idx].strip()
                nums_in_block = re.findall(r'\d{2}\.?\d{3}/\d{4}', block)
                if not nums_in_block:
                    continue
                num_part, ano = nums_in_block[0].replace(".", "").split("/")
                numero_ano = f"{num_part}/{ano}"
                if numero_ano not in reqs_to_ignore:
                    classif = classify_req(block)
                    requerimentos.append([sigla_prefix, num_part, ano, "", "", classif])
        
        # 4. Busca por RQN não recebidos
        nao_recebidas_header_pattern = re.compile(r"PROPOSIÇÕES\s*NÃO\s*RECEBIDAS", re.IGNORECASE)
        header_match = nao_recebidas_header_pattern.search(self.text)
        if header_match:
            start_idx = header_match.end()
            next_section_pattern = re.compile(r"^\s*(\*?)\s*.*\s*(\*?)\s*$", re.MULTILINE)
            next_section_match = next_section_pattern.search(self.text, start_idx)
            end_idx = next_section_match.start() if next_section_match else len(self.text)
            nao_recebidos_block = self.text[start_idx:end_idx]
            rqn_nao_recebido_pattern = re.compile(r"REQUERIMENTO Nº (\d{2}\.?\d{3}/\d{4})", re.IGNORECASE)
            for match in rqn_nao_recebido_pattern.finditer(nao_recebidos_block):
                numero_ano = match.group(1).replace(".", "")
                num_part, ano = numero_ano.split("/")
                if numero_ano not in reqs_to_ignore:
                    requerimentos.append(["RQN", num_part, ano, "", "", "NÃO RECEBIDO"])
        
        # Remove duplicatas
        unique_reqs = []
        seen = set()
        for r in requerimentos:
            key = (r[0], r[1], r[2])
            if key not in seen:
                seen.add(key)
                unique_reqs.append(r)
        
        return pd.DataFrame(unique_reqs)

    def process_pareceres(self):
        """Extrai pareceres do texto."""
        found_projects = {}
        
        # 1. Isola o texto relevante de pareceres, excluindo as votações.
        # Atualização do padrão para o novo título
        pareceres_start_pattern = re.compile(r"TRAMITAÇÃO DE PROPOSIÇÕES")
        votacao_pattern = re.compile(r"(Votação do Requerimento[\s\S]*?)(?=Votação do Requerimento|Diário do Legislativo|Projetos de Lei Complementar|Diário do Legislativo - Poder Legislativo|$)", re.IGNORECASE)
        
        pareceres_start = pareceres_start_pattern.search(self.text)
        if not pareceres_start:
            return pd.DataFrame(columns=['Sigla', 'Número', 'Ano', 'Tipo'])
        
        pareceres_text = self.text[pareceres_start.end():]
        
        # Remove os blocos de votação do texto a ser processado
        clean_text = pareceres_text
        for match in votacao_pattern.finditer(pareceres_text):
            clean_text = clean_text.replace(match.group(0), "")
        
        # 2. Processa o texto limpo para extrair os pareceres
        emenda_completa_pattern = re.compile(
            r"EMENDA Nº (\d+)\s+AO\s+(?:SUBSTITUTIVO Nº \d+\s+AO\s+)?PROJETO DE LEI(?: COMPLEMENTAR)? Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE
        )
        emenda_pattern = re.compile(r"^(?:\s*)EMENDA Nº (\d+)\s*", re.MULTILINE)
        substitutivo_pattern = re.compile(r"^(?:\s*)SUBSTITUTIVO Nº (\d+)\s*", re.MULTILINE)
        
        # Padrão para capturar "xxx/xxxx" ou "x.xxx/xxxx" e "xx/xxxx" ou "x.xx/xxxx"
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
        
        pareceres = []
        for (sigla, numero, ano), types in found_projects.items():
            type_str = "SUB/EMENDA" if len(types) > 1 else list(types)[0]
            pareceres.append([sigla, numero, ano, type_str])
        return pd.DataFrame(pareceres)
        
    def process_all(self):
        """Orquestra a extração de todos os dados do Diário do Legislativo."""
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
    """
    Classe para processar bytes de um Diário Administrativo,
    extraindo normas e retornando dados CSV.
    """
    def __init__(self, pdf_bytes):
        self.pdf_bytes = pdf_bytes

    def process_pdf(self):
        """Processa bytes de um arquivo PDF para extrair normas administrativas."""
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
        """Converte os resultados processados para o formato CSV."""
        resultados = self.process_pdf()
        if resultados is None:
            return None
        
        output_csv = io.StringIO()
        writer = csv.writer(output_csv, delimiter="\t")
        writer.writerows(resultados)
        return output_csv.getvalue().encode('utf-8')

# --- Função Principal da Aplicação Streamlit ---
def run_app():
    """Configura a interface e a lógica da aplicação Streamlit."""
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
                    processor = LegislativeProcessor(text)
                    extracted_data = processor.process_all()
                    
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

            else:  # Executivo (placeholder)
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
