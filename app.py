# --- Apenas o código modificado ---

# --- Importações ---
import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz # PyMuPDF
import requests
import pdfplumber # Reintroduzindo o pdfplumber

# ... (Mantenha todas as outras constantes e classes sem alteração) ...

# --- Classe ExecutiveProcessor ajustada ---
class ExecutiveProcessor:
    """Processa o texto de um Diário do Executivo, extraindo normas e alterações."""
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes

    def process_pdf(self) -> pd.DataFrame:
        dados = []
        try:
            with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
                # Otimização: processar página por página
                for i, pagina in enumerate(pdf.pages, start=1):
                    largura, altura = pagina.width, pagina.height
                    
                    # Usa a lógica de duas colunas, mas para cada página individualmente
                    for col_num, (x0, x1) in enumerate([(0, largura/2), (largura/2, largura)], start=1):
                        coluna = pagina.crop((x0, 0, x1, altura)).extract_text(layout=True) or ""
                        texto_limpo = re.sub(r'\s+', ' ', coluna).strip()

                        # Verifica se a página ou coluna contém o trecho relevante
                        if not re.search(r'Leis\s*e\s*Decretos', texto_limpo, re.IGNORECASE):
                            continue

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
                            "LEI": "LEI", "LEI COMPLEMENTAR": "LCP",
                            "DECRETO": "DEC", "DECRETO NE": "DNE"
                        }
                        
                        # Processa o texto da coluna
                        eventos = []
                        for m in norma_regex.finditer(texto_limpo):
                            eventos.append(('published', m.start(), m))
                        for c in comandos_regex.finditer(texto_limpo):
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
                                    mes = meses[partes[1].upper()]
                                    ano = partes[2]
                                    sancao = f"{dia}/{mes}/{ano}"
                                except:
                                    sancao = ""

                                linha = {
                                    "Página": i,
                                    "Coluna": col_num,
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
                                end_block = min(len(texto_limpo), pos_ev + raio)
                                bloco = texto_limpo[start_block:end_block]

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
        except Exception as e:
            st.error(f"Ocorreu um erro ao processar o arquivo: {e}")
            return pd.DataFrame()

        return pd.DataFrame(dados) if dados else pd.DataFrame()

    def to_csv(self):
        df = self.process_pdf()
        if df.empty:
            return None
        output_csv = io.StringIO()
        df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return output_csv.getvalue().encode('utf-8')


# --- Função run_app ajustada ---
def run_app():
    # ... (Mantenha o código de UI sem alteração) ...

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
                reader = PdfReader(io.BytesIO(pdf_bytes))
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
