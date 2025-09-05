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
import fitz

# ============================
# Classe Principal
# ============================

class LegislativeProcessor:
    def __init__(self, text):
        self.text = text

    def process_requerimentos(self):
        requerimentos = []

        # 1) Padrão geral de RQC
        rqc_pattern = re.compile(
            r"Requerimento(?: de Congratulação e Aplausos)?(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)\/\s*(\d{4})",
            re.IGNORECASE
        )

        # 2) RQC que já aparecem como aprovados
        rqc_pattern_aprovado = re.compile(
            r"Requerimento(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)\/\s*(\d{4}).*?(aprovado|aprovada)",
            re.IGNORECASE | re.DOTALL
        )
        reqs_to_ignore = set(
            f"{m.group(1).replace('.', '')}/{m.group(2)}"
            for m in rqc_pattern_aprovado.finditer(self.text)
        )

        # 2b) NOVO: RQC no formato "É recebido pela presidência..."
        rqc_pattern_erecebido = re.compile(
            r"É recebido pela\s+presidência.*?Requerimento(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)\/\s*(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in rqc_pattern_erecebido.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Recebido e aprovado"])

        # 3) Captura os RQC não aprovados explicitamente
        for match in rqc_pattern.finditer(self.text):
            num_part = match.group(1).replace(".", "")
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Pendente"])

        return requerimentos

    def process_proposicoes(self):
        proposicoes = []

        patterns = {
            "PL": re.compile(r"Projeto de Lei nº\s*(\d{1,5})\/\s*(\d{4})", re.IGNORECASE),
            "PLC": re.compile(r"Projeto de Lei Complementar nº\s*(\d{1,5})\/\s*(\d{4})", re.IGNORECASE),
            "PLS": re.compile(r"Projeto de Lei Suplementar nº\s*(\d{1,5})\/\s*(\d{4})", re.IGNORECASE),
            "PEC": re.compile(r"Proposta de Emenda à Constituição nº\s*(\d{1,5})\/\s*(\d{4})", re.IGNORECASE),
        }

        for tipo, pattern in patterns.items():
            for match in pattern.finditer(self.text):
                proposicoes.append([tipo, match.group(1), match.group(2), "", "", "Em tramitação"])

        return proposicoes

    def to_dataframe(self):
        dados = []
        dados.extend(self.process_requerimentos())
        dados.extend(self.process_proposicoes())

        return pd.DataFrame(
            dados,
            columns=["Tipo", "Número", "Ano", "Autor", "Ementa", "Situação"],
        )

# ============================
# Função Principal Streamlit
# ============================

def main():
    st.title("📄 Extrator de Proposições Legislativas")

    uploaded_file = st.file_uploader("Carregue um PDF", type=["pdf"])
    url = st.text_input("Ou cole o link direto de um PDF")

    if uploaded_file is not None:
        pdf = PdfReader(uploaded_file)
        text = "".join([page.extract_text() for page in pdf.pages])
    elif url:
        with fitz.open(stream=url, filetype="pdf") as doc:
            text = "".join([page.get_text() for page in doc])
    else:
        text = ""

    if text:
        processor = LegislativeProcessor(text)
        df = processor.to_dataframe()

        st.dataframe(df)

        # Exportar para CSV
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False, quoting=csv.QUOTE_ALL)
        st.download_button(
            label="💾 Baixar CSV",
            data=csv_buffer.getvalue(),
            file_name="proposicoes.csv",
            mime="text/csv",
        )

if __name__ == "__main__":
    main()
