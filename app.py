import streamlit as st
import re
import pandas as pd
from PyPDF2 import PdfReader
import io
import csv
import fitz
import requests  # <--- NOVO

# ... (todas as suas classes e funções utilitárias continuam iguais)

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

    # Novo seletor de modo
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

    elif modo == "Link da internet":
        url = st.text_input("Cole o link do PDF aqui:")
        if url:
            try:
                response = requests.get(url)
                if response.status_code == 200 and "application/pdf" in response.headers.get("Content-Type", ""):
                    pdf_bytes = response.content
                else:
                    st.error("O link não parece apontar para um PDF válido.")
            except Exception as e:
                st.error(f"Erro ao baixar o PDF: {e}")

    # Se já temos pdf_bytes, processamos
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

if __name__ == "__main__":
    run_app()
