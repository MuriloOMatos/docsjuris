from flask import Flask, render_template, request, send_file, abort
from docx import Document
import io
import os
import requests
from datetime import datetime
from decimal import Decimal, InvalidOperation
import bleach
from calendar import monthrange

app = Flask(__name__)

# Configuração via variável de ambiente
SERIES_BACEN = {'pessoal_fisica': int(os.getenv('SERIE_BACEN', 4390))}

def get_bacen_taxa_historico(data_emprestimo):
    """Busca taxa histórica para o mês do empréstimo"""
    try:
        codigo_serie = SERIES_BACEN['pessoal_fisica']
        mes_ano = data_emprestimo.strftime("%m/%Y")
        _, last_day = monthrange(data_emprestimo.year, data_emprestimo.month)
        url = (
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_serie}/dados?"
            f"formato=json&dataInicial=01/{mes_ano}&dataFinal={last_day:02d}/{mes_ano}"
        )
        
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        dados = response.json()
        if not dados:
            raise ValueError("Nenhum dado histórico encontrado para o período")
        return float(dados[0]['valor'])
        
    except (requests.RequestException, ValueError) as e:
        app.logger.error(f"Erro ao buscar taxa histórica BACEN: {str(e)}")
        return None

def get_bacen_taxa_atual():
    """Obtém a última taxa disponível do BACEN (usada apenas no index)"""
    try:
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{SERIES_BACEN['pessoal_fisica']}/dados/ultimos/1?formato=json"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        dados = response.json()
        if not dados:
            raise ValueError("Nenhum dado atual encontrado")
        return float(dados[0]['valor'])
        
    except (requests.RequestException, ValueError) as e:
        app.logger.error(f"Erro ao buscar taxa atual BACEN: {str(e)}")
        return None

def calcular_diferenca(valor, taxa_contrato, taxa_media, parcelas):
    """Calcula a diferença financeira entre as taxas"""
    try:
        valor_dec = Decimal(str(valor))
        taxa_contrato_dec = Decimal(str(taxa_contrato))
        taxa_media_dec = Decimal(str(taxa_media))
        parcelas_dec = Decimal(str(parcelas))
        
        if any(x <= 0 for x in [valor_dec, taxa_contrato_dec, taxa_media_dec, parcelas_dec]):
            raise ValueError("Todos os valores devem ser positivos")
        
        diferenca = valor_dec * (taxa_contrato_dec - taxa_media_dec) * parcelas_dec / 100
        return float(diferenca)
    except (InvalidOperation, ValueError) as e:
        app.logger.error(f"Erro no cálculo de diferença: {str(e)}")
        raise ValueError(f"Erro ao calcular diferença: {str(e)}")

def validar_dados_entrada(form):
    """Valida os dados de entrada do formulário"""
    required_fields = ['renda_mensal', 'parcela_pessoal', 'modelo_peticao']
    for field in required_fields:
        if field not in form:
            raise ValueError(f"Campo obrigatório '{field}' ausente")
    
    renda_mensal = Decimal(form['renda_mensal'].replace(",", "."))
    parcela_pessoal = Decimal(form['parcela_pessoal'].replace(",", "."))
    if renda_mensal <= 0 or parcela_pessoal < 0:
        raise ValueError("Renda mensal deve ser positiva e parcela pessoal não pode ser negativa")
    
    num_emprestimos = int(form['modelo_peticao'])
    if num_emprestimos not in [1, 2, 3]:
        raise ValueError("Número de empréstimos inválido (deve ser 1, 2 ou 3)")
    
    return num_emprestimos

def processar_emprestimos(form, num_emprestimos):
    """Processa os dados dos empréstimos usando a taxa média mensal de cada um"""
    emprestimos = []
    total_consignado = Decimal('0')
    
    for i in range(num_emprestimos):
        prefix = f'emprestimos[{i}]'
        emp_data = form.get(f'{prefix}[data]')
        try:
            data_emprestimo = datetime.strptime(emp_data, '%Y-%m-%d')
            if data_emprestimo > datetime.now():
                raise ValueError(f"Data do empréstimo {i+1} não pode ser no futuro")
        except ValueError as e:
            raise ValueError(f"Data do empréstimo {i+1} inválida ou no futuro. Use AAAA-MM-DD")
        
        taxa_media = get_bacen_taxa_historico(data_emprestimo)
        if taxa_media is None:
            raise ValueError(f"Não foi possível obter a taxa média para o empréstimo {i+1} ({emp_data})")
        
        valor = form.get(f'{prefix}[valor]', '0').replace(",", ".")
        parcela = form.get(f'{prefix}[parcela_consignada]', '0').replace(",", ".")
        parcelas = form.get(f'{prefix}[parcelas]', '0')
        taxa_contrato = form.get(f'{prefix}[taxa]', '0').replace(",", ".")
        
        if not all(Decimal(x) > 0 for x in [valor, parcela, parcelas, taxa_contrato]):
            raise ValueError(f"Valores do empréstimo {i+1} devem ser positivos")
        
        emprestimo = {
            'data': emp_data,
            'valor': valor,
            'parcela': parcela,
            'parcelas': parcelas,
            'taxa': taxa_contrato,  # Renomeado para 'taxa' para corresponder ao placeholder {{emprestimos_X_taxa}}
            'taxa_media': f"{taxa_media:.2f}",
            'diferenca': f"{calcular_diferenca(valor, taxa_contrato, taxa_media, parcelas):.2f}"
        }
        
        emprestimos.append(emprestimo)
        total_consignado += Decimal(parcela)
    
    return emprestimos, total_consignado

def gerar_documento(dados, num_emprestimos):
    """Gera o documento Word a partir dos dados"""
    template_path = os.path.abspath(os.path.join("modelos", f"modelo_{num_emprestimos}.docx"))
    if not template_path.startswith(os.path.abspath("modelos")) or not os.path.exists(template_path):
        raise FileNotFoundError("Modelo de documento inválido ou não encontrado")
    
    doc = Document(template_path)
    replacements = {
        'renda_mensal': dados['renda_mensal'],
        'parcela_pessoal': dados['parcela_pessoal'],
        'valor_liquido': dados['valor_liquido'],
        'comprometimento': dados['comprometimento'],
        'emprestimos': dados['emprestimos']  # Inclui 'taxa' para {{emprestimos_X_taxa}}
    }
    
    for p in doc.paragraphs:
        for key, value in flatten_dict(replacements).items():
            p.text = p.text.replace(f'{{{{{key}}}}}', bleach.clean(str(value)))
    
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return output

@app.route('/')
def index():
    taxa_atual = get_bacen_taxa_atual()
    if taxa_atual is None:
        taxa_atual = "Indisponível"
    bacen_data = {'taxa': f"{taxa_atual:.2f}%", 'data_atualizacao': datetime.now().strftime('%d/%m/%Y')}
    return render_template('index.html', **bacen_data)

@app.route('/gerar-peticao', methods=['POST'])
def gerar_peticao():
    try:
        num_emprestimos = validar_dados_entrada(request.form)
        
        dados = {
            'renda_mensal': request.form['renda_mensal'].replace(",", "."),
            'parcela_pessoal': request.form['parcela_pessoal'].replace(",", ".")
        }
        
        emprestimos, total_consignado = processar_emprestimos(request.form, num_emprestimos)
        dados['emprestimos'] = emprestimos
        
        renda = Decimal(dados['renda_mensal'])
        parcela_pessoal = Decimal(dados['parcela_pessoal'])
        dados['valor_liquido'] = f"{(renda - parcela_pessoal - total_consignado):.2f}"
        dados['comprometimento'] = f"{((parcela_pessoal + total_consignado) / renda * 100):.2f}%"
        
        documento = gerar_documento(dados, num_emprestimos)
        
        return send_file(
            documento,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            download_name=f"peticao_{datetime.now().strftime('%Y%m%d')}.docx",
            as_attachment=True
        )
    
    except ValueError as e:
        app.logger.error(f"Erro de validação: {str(e)}")
        return f"Erro nos dados fornecidos: {str(e)}", 400
    except FileNotFoundError as e:
        app.logger.error(f"Erro de arquivo: {str(e)}")
        return str(e), 404
    except Exception as e:
        app.logger.error(f"Erro inesperado: {str(e)}")
        return abort(500, "Erro interno ao processar a solicitação")

def flatten_dict(d, parent_key='', sep='_'):
    """Flattens a nested dictionary iteratively"""
    stack = [(d, parent_key)]
    items = []
    while stack:
        current_dict, current_key = stack.pop()
        for k, v in current_dict.items():
            new_key = f"{current_key}{sep}{k}" if current_key else k
            if isinstance(v, dict):
                stack.append((v, new_key))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    stack.append((item, f"{new_key}{sep}{i}"))
            else:
                items.append((new_key, v))
    return dict(items)

if __name__ == '__main__':
    app.run(debug=True)