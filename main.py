from flask import Flask, render_template, request, send_file, abort
from docx import Document
import io
import os
import requests
from datetime import datetime
from decimal import Decimal, InvalidOperation
import bleach
from calendar import monthrange
from functools import lru_cache
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from decimal import Decimal

# Inicialização do Flask
app = Flask(__name__)

# Configuração via variável de ambiente
SERIES_BACEN = {'pessoal_fisica': int(os.getenv('SERIE_BACEN', 25464))}

# Criação de uma sessão HTTP com retry para robustez nas requisições
http_session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retries)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

def _obter_dados_api(url, params):
    """
    Função auxiliar para acessar a API do BACEN.

    Args:
        url (str): URL da API.
        params (dict): Parâmetros da requisição.

    Returns:
        list: Dados retornados pela API.

    Raises:
        Exception: Se ocorrer algum erro na requisição ou processamento dos dados.
    """
    headers = {'User-Agent': 'Python/AppRevisaoContratos'}
    try:
        response = http_session.get(url, params=params, timeout=15, headers=headers)
        response.raise_for_status()
        dados = response.json()
        if not dados:
            raise ValueError("Nenhum dado encontrado com os parâmetros fornecidos")
        return dados
    except Exception as e:
        app.logger.error(f"Erro ao acessar API BACEN: {str(e)}")
        raise

@lru_cache(maxsize=128)
def get_bacen_taxa_historico(data_emprestimo):
    """
    Busca a taxa histórica para o mês do empréstimo, usando cache para
    evitar requisições repetidas para o mesmo período.
    """
    try:
        codigo_serie = SERIES_BACEN['pessoal_fisica']
        mes_ano = data_emprestimo.strftime("%m/%Y")
        _, last_day = monthrange(data_emprestimo.year, data_emprestimo.month)
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_serie}/dados"
        params = {
            'formato': 'json',
            'dataInicial': f"01/{mes_ano}",
            'dataFinal': f"{last_day:02d}/{mes_ano}"
        }
        dados = _obter_dados_api(url, params)
        return float(dados[0]['valor'])
    except Exception as e:
        app.logger.error(f"Erro ao buscar taxa histórica BACEN: {str(e)}")
        return None

def get_bacen_taxa_atual():
    """Obtém a última taxa disponível do BACEN."""
    try:
        codigo_serie = SERIES_BACEN['pessoal_fisica']
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_serie}/dados/ultimos/1"
        params = {'formato': 'json'}
        dados = _obter_dados_api(url, params)
        return float(dados[0]['valor'])
    except Exception as e:
        app.logger.error(f"Erro ao buscar taxa atual BACEN: {str(e)}")
        return None

def calcular_diferenca(valor, taxa_contrato, taxa_media, parcelas):
    """Calcula a diferença financeira entre as taxas."""
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
    """Valida os dados de entrada do formulário."""
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

def calculos_emprestimo(form, num_emprestimos):
    emprestimos = []
    total_consignado = Decimal('0')
    total_emprestimo_geral = Decimal('0')
    def_emprestimos = Decimal('0')
    parcela_pessoal_atual = Decimal('0')
    dif_bacen = Decimal('0')
    vlr_total_emprestimo1 = Decimal('0')
    vlr_total_emprestimo2 = Decimal('0')
    org_bacen = Decimal('0')
    org_div = Decimal('0')
    total_dobro = Decimal('0')
    dadovalorcausa = Decimal('0')
    valor_causa = Decimal('0')
    comprometimento_renda = Decimal('0')
    renda_mensal = form['renda_mensal'].replace(",", ".")
    parcela_pessoal = form['parcela_pessoal'].replace(",", ".")
    renda_mensal = Decimal(renda_mensal)
    
    

    for i in range(num_emprestimos):
        prefix = f'emprestimos[{i}]'
        emp_data = form.get(f'{prefix}[data]')
        try:
            #formato DD/MM/YYYY
            data_emprestimo = datetime.strptime(emp_data, '%d/%m/%Y')
            if data_emprestimo > datetime.now():
                raise ValueError(f"Data do empréstimo {i+1} não pode ser no futuro")
        except ValueError as e:
            raise ValueError(f"Data do empréstimo {i+1} inválida ou no futuro. Use DD/MM/YYYY") from e
        
        taxa_media = get_bacen_taxa_historico(data_emprestimo)
        if taxa_media is None:
            raise ValueError(f"Não foi possível obter a taxa média para o empréstimo {i+1} ({emp_data})")
        
        valor = form.get(f'{prefix}[valor]', '0').replace(",", ".")
        parcela = form.get(f'{prefix}[parcela_consignada]', '0').replace(",", ".")
        parcelas = form.get(f'{prefix}[parcelas]', '0')
        taxa_contrato = form.get(f'{prefix}[taxa]', '0').replace(",", ".")
        total_emprestimo = Decimal(parcela) * Decimal(parcelas)
        def_emprestimos = total_emprestimo / Decimal(valor)
        parcela_pessoal_atual = Decimal(valor) * ((Decimal(taxa_media) / 100) / (1 - (1 + Decimal(taxa_media) / 100) ** -Decimal(parcelas)))
        total_emprestimo_bacen = Decimal(parcela_pessoal_atual) * Decimal(parcelas)
        dif_bacen = Decimal(valor) / Decimal(parcela_pessoal_atual)
        vlr_total_emprestimo1 = Decimal(valor) * (1 + Decimal(taxa_media) / 100) ** Decimal(parcelas)
        vlr_total_emprestimo2 = Decimal(valor) * (1 + Decimal(taxa_contrato) / 100) ** Decimal(parcelas)
        org_bacen = (total_emprestimo - total_emprestimo_bacen)
        org_div = (total_emprestimo_geral / vlr_total_emprestimo1)
        total_dobro = org_bacen * 2
        dadovalorcausa = total_dobro + Decimal(10000)
        valor_causa += Decimal(10000) + Decimal(total_dobro)
        comprometimento_renda = Decimal(parcela) + Decimal(parcela_pessoal)
        comprometimento_porcentagem = Decimal(comprometimento_renda) / Decimal(renda_mensal) * 100
        renda_atual = Decimal(renda_mensal) - Decimal(parcela_pessoal) - Decimal(parcela)
        
        
        

        if not all(Decimal(x) > 0 for x in [valor, parcela, parcelas, taxa_contrato]):
            raise ValueError(f"Valores do empréstimo {i+1} devem ser positivos")
        
        emprestimo = {
            'data': emp_data,
            'valor': valor,
            'parcela': parcela,
            'parcelas': parcelas,
            'taxa': taxa_contrato,
            'taxa_media': f"{taxa_media:.2f}",
            'diferenca': f"{calcular_diferenca(valor, taxa_contrato, taxa_media, parcelas):.2f}",
            'total_emprestimo': f"{total_emprestimo:.2f}",
            'def_emprestimos': f"{def_emprestimos:.2f}",
            'parcela_pessoal_atual': f"{parcela_pessoal_atual:.2f}",
            'dif_bacen': f"{dif_bacen:.2f}",
            'vlr_total_emprestimo1': f"{vlr_total_emprestimo1:.2f}",
            'vlr_total_emprestimo2': f"{vlr_total_emprestimo2:.2f}",
            'org_bacen': f"{org_bacen:.2f}",
            'org_div': f"{org_div:.2f}",
            'total_dobro': f"{total_dobro:.2f}",
            'valor_causa': f"{valor_causa:.2f}",
            'dadovalorcausa': f"{dadovalorcausa:.2f}",
            'comprometimento_renda': f"{comprometimento_renda:.2f}",
            'comprometimento_porcentagem': f"{comprometimento_porcentagem:.2f}",
            'renda_atual': f"{renda_atual:.2f}",
            'total_emprestimo_bacen': f"{total_emprestimo_bacen:.2f}",
            

        }
        
        emprestimos.append(emprestimo)
        total_consignado += Decimal(parcela)
    
    return emprestimos, total_consignado, total_emprestimo_geral, def_emprestimos, parcela_pessoal_atual,dif_bacen, vlr_total_emprestimo1, vlr_total_emprestimo2, org_bacen,org_div,total_dobro,valor_causa,comprometimento_renda,renda_atual,comprometimento_porcentagem,total_emprestimo_bacen,dadovalorcausa,

def gerar_documento(dados, num_emprestimos):
    """Gera o documento Word a partir dos dados."""
    template_path = os.path.abspath(os.path.join("modelos", f"modelo_{num_emprestimos}.docx"))
    if not template_path.startswith(os.path.abspath("modelos")) or not os.path.exists(template_path):
        raise FileNotFoundError("Modelo de documento inválido ou não encontrado")
    
    doc = Document(template_path)
    replacements = {
        'renda_mensal': dados['renda_mensal'],
        'parcela_pessoal': dados['parcela_pessoal'],
        'valor_liquido': dados['valor_liquido'],
        'comprometimento': dados['comprometimento'],
        'emprestimos': dados['emprestimos'],
        'total_emprestimo' : dados['total_emprestimo'],
        'diario': dados['diario']
        
    }
    
    # Substituir placeholders nos parágrafos
    for p in doc.paragraphs:
        for key, value in flatten_dict(replacements).items():
            p.text = p.text.replace(f'{{{{{key}}}}}', bleach.clean(str(value)))
    
    # Substituir placeholders nas tabelas
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, value in flatten_dict(replacements).items():
                    cell.text = cell.text.replace(f'{{{{{key}}}}}', bleach.clean(str(value)))
    
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return output

@app.route('/')
def index():
    taxa_atual = get_bacen_taxa_atual()
    if taxa_atual is None:
        taxa_atual_display = "Indisponível"
    else:
        taxa_atual_display = f"{taxa_atual:.2f}%"
    bacen_data = {
        'taxa': taxa_atual_display,
        'data_atualizacao': datetime.now().strftime('%d/%m/%Y')
    }
    return render_template('index.html', **bacen_data)

def format_brl(valor):
    try:
        valor_dec = Decimal(valor)
        s = f"{valor_dec:,.2f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return valor


@app.route('/gerar-peticao', methods=['POST'])
def gerar_peticao():
    try:
        num_emprestimos = validar_dados_entrada(request.form)
        
        dados = {
            'renda_mensal': request.form['renda_mensal'].replace(",", "."),
            'parcela_pessoal': request.form['parcela_pessoal'].replace(",", "."),
        }
        
        emprestimos, total_consignado, total_emprestimo_geral, def_emprestimos, parcela_pessoal_atual, dif_bacen, vlr_total_emprestimo1, vlr_total_emprestimo2, org_bacen, org_div, total_dobro, valor_causa, comprometimento_renda, renda_atual, comprometimento_porcentagem,total_emprestimo_bacen,dadovalorcausa, = calculos_emprestimo(request.form, num_emprestimos)
        
        # Aplicar formatação BRL aos valores monetários dentro da lista emprestimos
        for emp in emprestimos:
            emp['valor'] = format_brl(emp['valor'])
            emp['parcela'] = format_brl(emp['parcela'])
            emp['diferenca'] = format_brl(emp['diferenca'])
            emp['total_emprestimo'] = format_brl(emp['total_emprestimo'])
            emp['parcela_pessoal_atual'] = format_brl(emp['parcela_pessoal_atual'])
            emp['dif_bacen'] = format_brl(emp['dif_bacen'])
            emp['vlr_total_emprestimo1'] = format_brl(emp['vlr_total_emprestimo1'])
            emp['vlr_total_emprestimo2'] = format_brl(emp['vlr_total_emprestimo2'])
            emp['org_bacen'] = format_brl(emp['org_bacen'])
            emp['total_dobro'] = format_brl(emp['total_dobro'])
            emp['valor_causa'] = format_brl(emp['valor_causa'])
            emp['renda_atual'] = format_brl(emp['renda_atual'])
            emp['total_emprestimo_bacen'] = format_brl(emp['total_emprestimo_bacen'])
            emp['dadovalorcausa'] = format_brl(emp['dadovalorcausa'])
            

        dados['emprestimos'] = emprestimos
        renda = Decimal(request.form['renda_mensal'].replace(",", "."))
        parcela_pessoal = Decimal(request.form['parcela_pessoal'].replace(",", "."))

        # Aplicar formatação BRL aos valores principais
        dados['valor_liquido'] = format_brl(renda - parcela_pessoal - total_consignado)
        dados['diario'] = format_brl(renda - parcela_pessoal - total_consignado / 30)
        dados['comprometimento'] = format_brl(parcela_pessoal + total_consignado)
        dados['total_emprestimo'] = format_brl(total_emprestimo_geral)
        dados['def_emprestimos'] = format_brl(def_emprestimos)
        dados['parcela_pessoal_atual'] = format_brl(parcela_pessoal_atual)
        dados['dif_bacen'] = format_brl(dif_bacen)
        dados['vlr_total_emprestimo1'] = format_brl(vlr_total_emprestimo1)
        dados['vlr_total_emprestimo2'] = format_brl(vlr_total_emprestimo2)
        dados['org_bacen'] = format_brl(org_bacen)
        dados['org_div'] = format_brl(org_div)
        dados['total_dobro'] = format_brl(total_dobro)
        dados['valor_causa'] = format_brl(valor_causa)
        dados['dadovalorcausa'] = format_brl(dadovalorcausa) 
        dados['comprometimento_renda'] = format_brl(comprometimento_renda)
        dados['renda_atual'] = format_brl(renda_atual)
        dados['comprometimento_porcentagem'] = format_brl(comprometimento_porcentagem)
        dados['total_emprestimo_bacen'] = format_brl(total_emprestimo_bacen)
        

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
    """Flatten a nested dictionary iteratively."""
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
