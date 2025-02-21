from flask import Flask, render_template, request, send_file, abort
from docx import Document
import io
import os
import requests
import pandas as pd
from datetime import datetime
from decimal import Decimal, InvalidOperation

app = Flask(__name__)

# Configuração da série histórica do BACEN
SERIES_BACEN = {'pessoal_fisica': 4390}

def get_bacen_taxa_juros():
    """Obtém a última taxa de juros do BACEN"""
    try:
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{SERIES_BACEN['pessoal_fisica']}/dados/ultimos/1?formato=json"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        dados = response.json()
        if not dados:
            return None
            
        df = pd.DataFrame(dados)
        df['data'] = pd.to_datetime(df['data'], dayfirst=True)
        df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
        
        return {
            'taxa': float(df.iloc[0]['valor']),
            'data_consulta': datetime.now().strftime('%d/%m/%Y %H:%M'),
            'data_atualizacao': df.iloc[0]['data'].strftime('%d/%m/%Y')
        }
        
    except Exception as e:
        app.logger.error(f"Erro na API BACEN: {str(e)}")
        return None

def calcular_diferenca(valor, taxa_contrato, taxa_media, parcelas):
    """Calcula a diferença financeira entre as taxas"""
    try:
        valor_dec = Decimal(str(valor))
        contrato = Decimal(str(taxa_contrato)) / 100
        media = Decimal(str(taxa_media)) / 100
        parcelas_dec = Decimal(str(parcelas))
        
        return float((valor_dec * contrato * parcelas_dec) - (valor_dec * media * parcelas_dec))
    except InvalidOperation:
        return 0.0

@app.route('/')
def index():
    """Rota principal que renderiza o formulário"""
    bacen_data = get_bacen_taxa_juros()
    return render_template(
        'index.html',
        taxa_bacen=bacen_data['taxa'] if bacen_data else None,
        data_atualizacao=bacen_data['data_atualizacao'] if bacen_data else None
    )

@app.route('/gerar-peticao', methods=['POST'])
def gerar_peticao():
    """Processa o formulário e gera o documento"""
    try:
        # Validação inicial
        if 'modelo_peticao' not in request.form:
            return "Número de empréstimos não especificado", 400
            
        num_emprestimos = int(request.form['modelo_peticao'])
        if num_emprestimos not in [1, 2, 3]:
            return "Número de empréstimos inválido", 400

        # Obter dados do BACEN
        bacen_data = get_bacen_taxa_juros()
        if not bacen_data:
            return "Não foi possível obter a taxa do BACEN", 500

        # Processar dados do formulário
        dados = {
            'taxa_media_bacen': bacen_data['taxa'],
            'data_consulta': bacen_data['data_consulta'],
            'data_atualizacao_bacen': bacen_data['data_atualizacao'],
            'renda_mensal': request.form['renda_mensal'].replace(",", "."),
            'parcela_pessoal': request.form['parcela_pessoal'].replace(",", "."),
            'emprestimos': []
        }

        # Processar cada empréstimo
        total_consignado = Decimal('0')
        for i in range(num_emprestimos):
            emprestimo = {
                'data': request.form.get(f'emprestimos[{i}][data]', ''),
                'valor': request.form.get(f'emprestimos[{i}][valor]', '0').replace(",", "."),
                'parcela': request.form.get(f'emprestimos[{i}][parcela_consignada]', '0').replace(",", "."),
                'parcelas': request.form.get(f'emprestimos[{i}][parcelas]', '0'),
                'taxa': request.form.get(f'emprestimos[{i}][taxa]', '0').replace(",", ".")
            }
            
            # Conversão e cálculos
            valor = Decimal(emprestimo['valor'])
            parcela = Decimal(emprestimo['parcela'])
            parcelas = int(emprestimo['parcelas'])
            taxa = Decimal(emprestimo['taxa'])
            
            emprestimo['total'] = float(parcela * parcelas)
            emprestimo['diferenca'] = calcular_diferenca(valor, taxa, dados['taxa_media_bacen'], parcelas)
            
            dados['emprestimos'].append(emprestimo)
            total_consignado += parcela

        # Cálculos finais
        renda = Decimal(dados['renda_mensal'])
        parcela_pessoal = Decimal(dados['parcela_pessoal'])
        
        dados['valor_liquido'] = f"{(renda - (parcela_pessoal + total_consignado)):.2f}"
        dados['comprometimento'] = f"{((parcela_pessoal + total_consignado) / renda * 100):.2f}%" if renda > 0 else "0%"

        # Carregar template correto
        template_path = os.path.join("modelos", f"modelo_{num_emprestimos}.docx")
        if not os.path.exists(template_path):
            return "Modelo não encontrado", 404

        doc = Document(template_path)
        
        # Função para aplanar dicionário
        def flatten_dict(d, parent_key='', sep='_'):
            items = []
            for k, v in d.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                if isinstance(v, dict):
                    items.extend(flatten_dict(v, new_key, sep=sep).items())
                elif isinstance(v, list):
                    for i, item in enumerate(v):
                        items.extend(flatten_dict(item, f"{new_key}{sep}{i}", sep=sep).items())
                else:
                    items.append((new_key, v))
            return dict(items)
        
        replacements = flatten_dict(dados)

        # Substituir placeholders
        for paragraph in doc.paragraphs:
            for key, value in replacements.items():
                paragraph.text = paragraph.text.replace(f'{{{{{key}}}}}', str(value))

        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for key, value in replacements.items():
                        cell.text = cell.text.replace(f'{{{{{key}}}}}', str(value))

        # Gerar resposta
        output = io.BytesIO()
        doc.save(output)
        output.seek(0)
        
        return send_file(
            output,
            download_name=f"peticao_{datetime.now().strftime('%Y%m%d%H%M%S')}.docx",
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    except InvalidOperation as e:
        return f"Erro de conversão numérica: {str(e)}", 400
    except Exception as e:
        app.logger.error(f"Erro inesperado: {str(e)}")
        return "Erro interno no servidor", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)