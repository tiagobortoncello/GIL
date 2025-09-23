# -*- coding: utf-8 -*-
# ======================================
# Extrator de Documentos Oficiais (Streamlit)
# Upload OU Link para PDF
# ======================================

# --- Importações ---
import streamlit as st
import re
import pandas as pd
import pypdf # Usado para encontrar as páginas relevantes de forma leve
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

def get_norma_from_match(match, mapa_tipos):
    """Função utilitária para extrair tipo, número e ano de um match de regex."""
    tipo_raw = match.group(1).strip()
    tipo = mapa_tipos.get(tipo_raw.upper(), tipo_raw)
    numero_raw = match.group(2).replace(" ", "").replace(".", "")
    ano = match.group(3) if len(match.groups()) > 2 and match.group(3) else ""
    return tipo, numero_raw, ano

# --- Classes de Processamento ---
class LegislativeProcessor:
    """ Processa o texto de um Diário do Legislativo, extraindo normas, proposições, requerimentos e pareceres. """
    def __init__(self, text: str):
        self.text = text
        self.mapa_tipos = TIPO_MAP_NORMA
        self.alteracoes_regex = re.compile(
            r"(revoga(?:da)?|altera(?:da)?|inclui|acrescenta|modifica|dispor|acrescentado(?:s)?)[\s\S]{0,100}?(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA)\s+Nº\s*([\d\s\.]+)(?:/(\d{4}))?",
            re.IGNORECASE
        )

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
            normas.append([sigla, numero_raw, ano, ""])
        return pd.DataFrame(normas, columns=['Sigla', 'Número', 'Ano', 'Alterações'])

    def process_alteracoes_normas(self) -> pd.DataFrame:
        alteracoes = []
        seen = set()
        for match in self.alteracoes_regex.finditer(self.text):
            comando = match.group(1).strip().lower()
            tipo_alterada_raw = match.group(2)
            numero_alterada_raw = match.group(3).replace(" ", "").replace(".", "")
            ano_alterada = match.group(4)
            
            if not ano_alterada:
                continue

            tipo_alterada = self.mapa_tipos.get(tipo_alterada_raw.upper(), tipo_alterada_raw)
            chave = (tipo_alterada, numero_alterada_raw, ano_alterada)

            if chave not in seen:
                descricao_alteracao = ""
                if "revoga" in comando:
                    descricao_alteracao = "Revogação"
                elif "altera" in comando or "inclui" in comando or "acrescenta" in comando or "modifica" in comando:
                    descricao_alteracao = "Alteração"
                elif "dispor" in comando:
                    descricao_alteracao = "Disposição"
                
                if descricao_alteracao:
                    alteracoes.append([tipo_alterada, numero_alterada_raw, ano_alterada, descricao_alteracao])
                    seen.add(chave)

        return pd.DataFrame(alteracoes, columns=['Sigla', 'Número', 'Ano', 'Alterações'])

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
        reqs_encontrados = []
        reqs_to_ignore = set()

        # Regex para ignorar requerimentos
        ignore_pattern = re.compile(r"Ofício nº .*?,.*?relativas ao Requerimento\s*nº (\d{1,4}\.?\d{0,3}/\d{4})", re.IGNORECASE | re.DOTALL)
        aprovado_pattern = re.compile(r"(da Comissão.*?, informando que, na.*?foi aprovado o Requerimento\s*nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4}))", re.IGNORECASE | re.DOTALL)
        
        for match in ignore_pattern.finditer(self.text):
            numero_ano = match.group(1).replace(".", "")
            reqs_to_ignore.add(numero_ano)

        for match in aprovado_pattern.finditer(self.text):
            num_part = match.group(2).replace('.', '')
            ano = match.group(3)
            numero_ano = f"{num_part}/{ano}"
            reqs_to_ignore.add(numero_ano)

        # 1) Requerimentos prejudicados
        req_prejudicado_pattern = re.compile(
            r"prejudicado o Requerimento em Comissão n[º°]?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in req_prejudicado_pattern.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            reqs_encontrados.append({
                "match": match,
                "data": ["RQC", num_part, ano, "", "", "Prejudicado"],
                "numero_ano": numero_ano
            })

        # 2) Requerimentos recebidos com padrão "RECEBIMENTO DE PROPOSIÇÃO"
        req_recebimento_pattern = re.compile(
            r"RECEBIMENTO DE PROPOSIÇÃO[\s\S]*?REQUERIMENTO Nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in req_recebimento_pattern.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            reqs_encontrados.append({
                "match": match,
                "data": ["RQN", num_part, ano, "", "", "Recebido"],
                "numero_ano": numero_ano
            })

        # 3) RQC recebidos e aprovados
        rqc_pattern_aprovado = re.compile(
            r"É\s+recebido\s+pela\s+presidência,\s+submetido\s+a\s+votação\s+e\s+aprovado\s+o\s+Requerimento(?:s)?(?: nº| Nº| n\u00ba| n\u00b0)?\s*(\d{1,5}(?:\.\d{0,3})?)/\s*(\d{4})",
            re.IGNORECASE
        )
        for match in rqc_pattern_aprovado.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            reqs_encontrados.append({
                "match": match,
                "data": ["RQC", num_part, ano, "", "", "Aprovado"],
                "numero_ano": numero_ano
            })

        # 4) RQC recebidos para apreciação
        rqc_recebido_apreciacao_pattern = re.compile(
            r"É recebido pela\s+presidência, para posterior apreciação, o Requerimento(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in rqc_recebido_apreciacao_pattern.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            reqs_encontrados.append({
                "match": match,
                "data": ["RQC", num_part, ano, "", "", "Recebido para apreciação"],
                "numero_ano": numero_ano
            })

        # 5) RQN e RQC (padrão antigo)
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
                
                classif = classify_req(block)
                reqs_encontrados.append({
                    "match": match,
                    "data": [sigla_prefix, num_part, ano, "", "", classif],
                    "numero_ano": numero_ano
                })

        # 6) RQN não recebidos
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
                reqs_encontrados.append({
                    "match": match,
                    "data": ["RQN", num_part, ano, "", "", "NÃO RECEBIDO"],
                    "numero_ano": numero_ano
                })
        
        # Ordena os requerimentos pela posição no texto
        reqs_encontrados.sort(key=lambda x: x['match'].start())
        
        # Processa e remove duplicatas
        unique_reqs = []
        seen = set()
        for r in reqs_encontrados:
            numero_ano = r['numero_ano']
            if numero_ano not in reqs_to_ignore:
                key = (r['data'][0], r['data'][1], r['data'][2])
                if key not in seen:
                    seen.add(key)
                    unique_reqs.append(r['data'])

        return pd.DataFrame(unique_reqs)

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
        df_alteracoes = self.process_alteracoes_normas()
        df_proposicoes = self.process_proposicoes()
        df_requerimentos = self.process_requerimentos()
        df_pareceres = self.process_pareceres()
        
        # Juntar as normas publicadas e as alteradas em um único DataFrame
        df_completo = pd.concat([df_normas, df_alteracoes], ignore_index=True)
        # Remover duplicatas, mantendo a mais detalhada se houver.
        df_completo.drop_duplicates(subset=['Sigla', 'Número', 'Ano', 'Alterações'], keep='first', inplace=True)

        return {
            "Normas": df_completo,
            "Proposicoes": df_proposicoes,
            "Requerimentos": df_requerimentos,
            "Pareceres": df_pareceres
        }

class AdministrativeProcessor:
    """ Processa bytes de um Diário Administrativo, extraindo normas e retornando dados CSV. """
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes
        self.mapa_tipos = {
            "DELIBERAÇÃO DA MESA": "DLB",
            "PORTARIA DGE": "PRT",
            "ORDEM DE SERVIÇO PRES/PSEC": "OSV",
            "DECISÃO DA 1ª-SECRETARIA": "DCS"
        }
        self.norma_regex = re.compile(
            r'(DELIBERAÇÃO DA MESA|PORTARIA DGE|ORDEM DE SERVIÇO PRES/PSEC|DECISÃO DA 1ª-SECRETARIA)\s+Nº\s+([\d\.]+)\/(\d{4})'
        )
        self.alteracoes_regex = re.compile(
            r"(?:revoga|altera|inclui|acrescenta|modifica|dispor|passam a vigorar|a \d{1,3}ª alteração)[\s\S]{0,100}?(DELIBERAÇÃO DA MESA|PORTARIA DGE|ORDEM DE SERVIÇO PRES/PSEC|RESOLUÇÃO|INSTRUÇÃO NORMATIVA|DECISÃO DA 1ª-SECRETARIA)\s+Nº\s*([\d\s\.]+)(?:/(\d{4}))?",
            re.IGNORECASE
        )

    def process_pdf(self) -> pd.DataFrame:
        try:
            doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
        except Exception as e:
            st.error(f"Erro ao abrir o arquivo PDF: {e}")
            return pd.DataFrame()

        texto_completo = ""
        for page in doc:
            texto_completo += page.get_text("text") + "\n"
        doc.close()
        texto_completo = re.sub(r'\s+', ' ', texto_completo)
        
        normas_encontradas = []
        for match in self.norma_regex.finditer(texto_completo):
            normas_encontradas.append({
                "start": match.start(),
                "end": match.end(),
                "tipo_texto": match.group(1),
                "numero": match.group(2).replace('.', ''),
                "ano": match.group(3)
            })

        dados = []
        seen_normas = set()

        for i, norma_info in enumerate(normas_encontradas):
            tipo_texto = norma_info["tipo_texto"]
            numero = norma_info["numero"]
            ano = norma_info["ano"]
            sigla = self.mapa_tipos.get(tipo_texto, tipo_texto)

            chave = (sigla, numero, ano, "Publicação")
            if chave not in seen_normas:
                dados.append([sigla, numero, ano, "Publicação"])
                seen_normas.add(chave)

            # Analisar o texto após a norma principal para buscar alterações
            end_of_norma = norma_info["end"]
            start_of_next_norma = normas_encontradas[i+1]["start"] if i+1 < len(normas_encontradas) else len(texto_completo)
            
            # Limitar a busca para não pegar o texto da próxima norma
            bloco_de_texto = texto_completo[end_of_norma:start_of_next_norma]
            
            for match_alteracao in self.alteracoes_regex.finditer(bloco_de_texto):
                comando = match_alteracao.group(1).strip().lower()
                tipo_alterada_raw = match_alteracao.group(2)
                
                numero_raw_match = match_alteracao.group(3)
                if not numero_raw_match:
                    continue
                
                numero_alterada_raw = numero_raw_match.replace(" ", "").replace(".", "")
                
                ano_alterada = match_alteracao.group(4)
                if not ano_alterada:
                    continue

                tipo_alterada = self.mapa_tipos.get(tipo_alterada_raw.upper(), tipo_alterada_raw)

                descricao_alteracao = ""
                if "revoga" in comando:
                    descricao_alteracao = "Revogação"
                elif "altera" in comando or "inclui" in comando or "acrescenta" in comando or "modifica" in comando or "passam a vigorar" in comando:
                    descricao_alteracao = "Alteração"
                
                if descricao_alteracao:
                    chave_alteracao = (tipo_alterada, numero_alterada_raw, ano_alterada, descricao_alteracao)
                    if chave_alteracao not in seen_normas:
                        dados.append([tipo_alterada, numero_alterada_raw, ano_alterada, descricao_alteracao])
                        seen_normas.add(chave_alteracao)

        return pd.DataFrame(dados, columns=['Sigla', 'Número', 'Ano', 'Status'])

    def to_csv(self):
        df = self.process_pdf()
        if df.empty:
            return None
        output_csv = io.StringIO()
        df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return output_csv.getvalue().encode('utf-8')

class ExecutiveProcessor:
    """Processa o texto de um Diário do Executivo, extraindo normas e alterações."""
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes
        self.mapa_tipos = {
            "LEI": "LEI",
            "LEI COMPLEMENTAR": "LCP",
            "DECRETO": "DEC",
            "DECRETO NE": "DNE"
        }
        self.norma_regex = re.compile(
            r'\b(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]\s*([\d\s\.]+),\s*DE\s+([A-Z\s\d]+)\b'
        )
        self.comandos_regex = re.compile(
            r'(Ficam\s+revogados|Fica\s+acrescentado|Ficam\s+alterados|passando\s+o\s+item|passa\s+a\s+vigorar|passam\s+a\s+vigorar)',
            re.IGNORECASE
        )
        self.norma_alterada_regex = re.compile(
            r'(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]?\s*([\d\s\./]+)(?:,\s*de\s*(.*?\d{4})?)?',
            re.IGNORECASE
        )
        
    def find_relevant_pages(self) -> tuple:
        """Encontra as páginas de início e fim da seção relevante de forma eficiente."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(self.pdf_bytes))
            start_page_num, end_page_num = None, None
            
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                if re.search(r'Leis\s*e\s*Decretos', text, re.IGNORECASE):
                    start_page_num = i
                if re.search(r'Atos\s*do\s*Governador', text, re.IGNORECASE):
                    end_page_num = i

            if start_page_num is None or end_page_num is None or start_page_num > end_page_num:
                st.warning("Não foi encontrado o trecho de 'Leis e Decretos' ou 'Atos do Governador' para delimitar a seção.")
                return None, None

            return start_page_num, end_page_num + 1

        except Exception as e:
            st.error(f"Erro ao buscar páginas relevantes com PyPDF: {e}")
            return None, None

    def process_pdf(self) -> pd.DataFrame:
        start_page_idx, end_page_idx = self.find_relevant_pages()
        if start_page_idx is None:
            return pd.DataFrame()

        trechos = []
        try:
            with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
                # Processa apenas o subconjunto de páginas relevante
                for i in range(start_page_idx, end_page_idx):
                    pagina = pdf.pages[i]
                    largura, altura = pagina.width, pagina.height
                    for col_num, (x0, x1) in enumerate([(0, largura/2), (largura/2, largura)], start=1):
                        coluna = pagina.crop((x0, 0, x1, altura)).extract_text(layout=True) or ""
                        texto_limpo = re.sub(r'\s+', ' ', coluna).strip()
                        trechos.append({
                            "pagina": i + 1,
                            "coluna": col_num,
                            "texto": texto_limpo
                        })
        except Exception as e:
            st.error(f"Erro ao extrair texto detalhado do PDF do Executivo: {e}")
            return pd.DataFrame()
            
        dados = []
        ultima_norma = None
        seen_alteracoes = set()

        for t in trechos:
            pagina = t["pagina"]
            coluna = t["coluna"]
            texto = t["texto"]

            eventos = []
            for m in self.norma_regex.finditer(texto):
                eventos.append(('published', m.start(), m))
            for c in self.comandos_regex.finditer(texto):
                eventos.append(('command', c.start(), c))
            eventos.sort(key=lambda e: e[1])

            for ev in eventos:
                tipo_ev, pos_ev, match_obj = ev
                command_text = match_obj.group(0).lower()

                if tipo_ev == 'published':
                    match = match_obj
                    tipo_raw = match.group(1).strip()
                    tipo = self.mapa_tipos.get(tipo_raw.upper(), tipo_raw)
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
                        "Página": pagina,
                        "Coluna": coluna,
                        "Sanção": sancao,
                        "Tipo": tipo,
                        "Número": numero,
                        "Alterações": "Publicação"
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

                    alteracoes_para_processar = []
                    if 'revogado' in command_text:
                        alteracoes_para_processar = list(self.norma_alterada_regex.finditer(bloco))
                    else:
                        alteracoes_candidatas = list(self.norma_alterada_regex.finditer(bloco))
                        if alteracoes_candidatas:
                            pos_comando_no_bloco = pos_ev - start_block
                            melhor_candidato = min(
                                alteracoes_candidatas,
                                key=lambda m: abs(m.start() - pos_comando_no_bloco)
                            )
                            alteracoes_para_processar = [melhor_candidato]
                    
                    for alt in alteracoes_para_processar:
                        tipo_alt_raw = alt.group(1).strip()
                        tipo_alt = self.mapa_tipos.get(tipo_alt_raw.upper(), tipo_alt_raw)
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

                        if ultima_norma["Alterações"] == "Publicação":
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
    pdf_bytes = None
    
    # Lógica condicional para exibir ou não a opção de link
    if diario_escolhido == 'Executivo':
        modo = "Upload de arquivo"
        st.info("Para o Diário do Executivo, é necessário fazer o upload do arquivo.")
    else:
        modo = st.radio(
            "Como deseja fornecer o PDF?",
            ("Upload de arquivo", "Link da internet"),
            horizontal=True
        )

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
                reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
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
                with pd.ExcelWriter(output, engine="openypxl") as writer:
                    for sheet_name, df in extracted_data.items():
                        df.to_excel(writer, sheet_name=sheet_name, index=False, header=True)
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
            st.error(f"Ocorreu um erro ao processar o arquivo. Detalhes do erro: {e}")
            st.exception(e)

if __name__ == '__main__':
    run_app()
