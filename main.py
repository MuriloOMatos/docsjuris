from flask import Flask, render_template, request, send_file, abort, redirect, url_for, session
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
from werkzeug.security import generate_password_hash, check_password_hash
import logging
import zipfile
import tempfile
import psycopg2
from psycopg2.extras import RealDictCursor

# Lista de templates válidos
VALID_TEMPLATES = [
    'declaracao_hiposuficencia',
    'contratos_honorarios',
    'declaracao_contrato_digital',
    'declaracao_procuradores',
    'declaracao_ir',
    'procuracao'
]


# Configuração de logging
logging.basicConfig(level=logging.DEBUG)

# Inicialização do Flask
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24).hex())

# Usuários fictícios
USERS = {
    'GMadvogados': generate_password_hash('GM1252')
}

# Configuração via variável de ambiente
SERIES_BACEN = {'pessoal_fisica': int(os.getenv('SERIE_BACEN', 25464))}

# Sessão HTTP com retry
http_session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"])
adapter = HTTPAdapter(max_retries=retries)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            port=os.getenv("DB_PORT"),
            cursor_factory=RealDictCursor
        )
    except psycopg2.Error as e:
        app.logger.error(f"Erro ao conectar ao banco de dados: {str(e)}")
        raise

@app.route('/login', methods=['GET', 'POST'])
def login():
    app.logger.debug("Acessando rota /login")
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        app.logger.debug(f"Tentativa de login com usuário: {username}")
        
        if username in USERS and check_password_hash(USERS[username], password):
            session['logged_in'] = True
            session['username'] = username
            app.logger.debug("Login bem-sucedido")
            return redirect(url_for('index'))
        else:
            app.logger.debug("Falha no login")
            return render_template('login.html', error='Usuário ou senha inválidos')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    app.logger.debug("Executando logout")
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))

def login_required(f):
    def wrap(*args, **kwargs):
        if 'logged_in' not in session:
            app.logger.debug("Usuário não autenticado, redirecionando para login")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

def _obter_dados_api(url, params):
    headers = {'User-Agent': 'Python/AppRevisaoContratos'}
    try:
        response = http_session.get(url, params=params, timeout=10, headers=headers)
        response.raise_for_status()
        dados = response.json()
        if not dados or 'valor' not in dados[0]:
            raise ValueError("Nenhum dado válido retornado pela API")
        return dados
    except Exception as e:
        app.logger.error(f"Erro ao acessar API BACEN: {str(e)}")
        raise

@lru_cache(maxsize=128)
def get_bacen_taxa_historico(data_emprestimo):
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
        return 0.01

def get_bacen_taxa_atual():
    try:
        codigo_serie = SERIES_BACEN['pessoal_fisica']
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_serie}/dados/ultimos/1"
        params = {'formato': 'json'}
        dados = _obter_dados_api(url, params)
        return float(dados[0]['valor'])
    except Exception as e:
        app.logger.error(f"Erro ao buscar taxa atual BACEN: {str(e)}")
        return 0.01

@app.route('/bancos')
@login_required
def listar_bancos():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM bancos ORDER BY nome_banco ASC")
        bancos = cur.fetchall()
        cur.close()
        conn.close()
        return render_template('bancos.html', bancos=bancos)
    except psycopg2.Error as e:
        app.logger.error(f"Erro ao acessar bancos: {str(e)}")
        return render_template('bancos.html', bancos=[], error="Erro ao carregar bancos")

@app.route('/bancos/adicionar', methods=['POST'])
@login_required
def adicionar_banco():
    codigo = request.form.get('codigo_banco')
    nome = request.form.get('nome_banco')

    if not codigo or not nome:
        return "Código e Nome do banco são obrigatórios.", 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM bancos WHERE codigo_banco = %s", (codigo,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return "Código do banco já existe.", 400

        cur.execute(
            "INSERT INTO bancos (codigo_banco, nome_banco) VALUES (%s, %s)",
            (codigo, nome)
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('listar_bancos'))
    except psycopg2.Error as e:
        app.logger.error(f"Erro ao adicionar banco: {str(e)}")
        return "Erro ao adicionar banco.", 500

@app.route('/documentos')
@login_required
def documentos():
    app.logger.debug("Acessando rota /documentos com banco desativado temporariamente")

    bancos = [
    
    {"codigo_banco": "01", "nome_banco": "BANCO DO BRASIL.", "cnpj": "00.000.000/0001-00"},
    {"codigo_banco": "02", "nome_banco": "CAIXA ECONÔMICA.", "cnpj": "00.000.000/0001-91"},
    {"codigo_banco": "03", "nome_banco": "BANCO MERCANTIL DO BRASIL S.A.", "cnpj": "17.184.037/0001-10"},
    {"codigo_banco": "04", "nome_banco": "BANCO CREFISA S.A.", "cnpj": "61.033.106/0001-86"},
    {"codigo_banco": "05", "nome_banco": "BANCO BMG S.A.", "cnpj": "61.186.680/0001-74"},
    {"codigo_banco": "06", "nome_banco": "BANCO AGIBANK S.A.", "cnpj": "10.664.513/0001-50"}
]
    return render_template('documentos.html', bancos=bancos)


def calcular_diferenca(valor, taxa_contrato, taxa_media, parcelas):
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
    required_fields = ['renda_mensal', 'parcela_pessoal', 'modelo_peticao']
    for field in required_fields:
        if field not in form:
            raise ValueError(f"Campo obrigatório '{field}' ausente")
    
    try:
        renda_mensal = Decimal(form['renda_mensal'].replace(",", "."))
        parcela_pessoal = Decimal(form['parcela_pessoal'].replace(",", "."))
        if renda_mensal <= 0 or parcela_pessoal < 0:
            raise ValueError("Renda mensal deve ser positiva e parcela pessoal não pode ser negativa")
        
        num_emprestimos = int(form['modelo_peticao'])
        if num_emprestimos not in [1, 2, 3]:
            raise ValueError("Número de empréstimos inválido (deve ser 1, 2 ou 3)")
    except (ValueError, InvalidOperation) as e:
        raise ValueError(f"Erro na validação de dados: {str(e)}")
    
    return num_emprestimos

def calculos_emprestimo(form, num_emprestimos):
    emprestimos = []
    total_consignado = Decimal('0')
    total_emprestimo_geral = Decimal('0')
    total_dobro_geral = Decimal('0')
    renda_mensal = Decimal(form['renda_mensal'].replace(",", "."))
    parcela_pessoal = Decimal(form['parcela_pessoal'].replace(",", "."))
    
    for i in range(num_emprestimos):
        prefix = f'emprestimos[{i}]'
        emp_data = form.get(f'{prefix}[data]')
        try:
            data_emprestimo = datetime.strptime(emp_data, '%d/%m/%Y')
            if data_emprestimo > datetime.now():
                raise ValueError(f"Data do empréstimo {i+1} não pode ser no futuro")
        except ValueError as e:
            raise ValueError(f"Data do empréstimo {i+1} inválida ou no futuro. Use DD/MM/YYYY") from e
        
        taxa_media = get_bacen_taxa_historico(data_emprestimo)
        if taxa_media is None:
            raise ValueError(f"Não foi possível obter a taxa média para o empréstimo {i+1} ({emp_data})")
        
        valor_str = form.get(f'{prefix}[valor]', '0').replace(",", ".")
        parcela_str = form.get(f'{prefix}[parcela_consignada]', '0').replace(",", ".")
        parcelas_str = form.get(f'{prefix}[parcelas]', '0')
        taxa_contrato_str = form.get(f'{prefix}[taxa]', '0').replace(",", ".")
        
        try:
            valor = Decimal(valor_str)
            parcela = Decimal(parcela_str)
            parcelas = Decimal(parcelas_str)
            taxa_contrato = Decimal(taxa_contrato_str)
            
            if not all(x > 0 for x in [valor, parcela, parcelas, taxa_contrato]):
                raise ValueError(f"Valores do empréstimo {i+1} devem ser positivos")
            
            if Decimal(str(taxa_media)) <= 0:
                raise ValueError(f"Taxa média BACEN deve ser positiva para empréstimo {i+1}")
        except (ValueError, InvalidOperation) as e:
            raise ValueError(f"Erro nos valores do empréstimo {i+1}: {str(e)}")
        
        total_emprestimo = parcela * parcelas
        total_emprestimo_geral += total_emprestimo
        
        def_emprestimos = total_emprestimo / valor if valor != 0 else Decimal('0')
        
        taxa_media_dec = Decimal(str(taxa_media)) / 100
        parcela_pessoal_atual = valor * (taxa_media_dec / (1 - (1 + taxa_media_dec) ** -parcelas)) if (1 + taxa_media_dec) ** -parcelas != 1 else Decimal('0')
        
        total_emprestimo_bacen = parcela_pessoal_atual * parcelas
        
        dif_bacen = valor / parcela_pessoal_atual if parcela_pessoal_atual != 0 else Decimal('0')
        
        taxa_contrato_dec = taxa_contrato / 100
        vlr_total_emprestimo1 = valor * (1 + Decimal(str(taxa_media)) / 100) ** parcelas
        vlr_total_emprestimo2 = valor * (1 + taxa_contrato_dec) ** parcelas
        
        org_bacen = abs(total_emprestimo - total_emprestimo_bacen)
        
        org_div = (total_emprestimo_geral / vlr_total_emprestimo1) if vlr_total_emprestimo1 != 0 else Decimal('0')
        
        total_dobro = org_bacen * 2
        total_dobro_geral += total_dobro
        
        comprometimento_renda = parcela + parcela_pessoal
        comprometimento_porcentagem = (comprometimento_renda / renda_mensal * 100) if renda_mensal != 0 else Decimal('0')
        renda_atual = renda_mensal - parcela_pessoal - parcela
        
        emprestimo = {
            'data': emp_data,
            'valor': valor_str,
            'parcela': parcela_str,
            'parcelas': parcelas_str,
            'taxa': taxa_contrato_str,
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
            'valor_causa': f"{Decimal('5000') + total_dobro:.2f}",
            'dadovalorcausa': f"0.00",
            'comprometimento_renda': f"{comprometimento_renda:.2f}",
            'comprometimento_porcentagem': f"{comprometimento_porcentagem:.2f}",
            'renda_atual': f"{renda_atual:.2f}",
            'total_emprestimo_bacen': f"{total_emprestimo_bacen:.2f}",
        }
        
        emprestimos.append(emprestimo)
        total_consignado += parcela
        
    dadovalorcausa = total_dobro_geral + Decimal('5000')
    valor_causa = dadovalorcausa
    
    for emp in emprestimos:
        emp['dadovalorcausa'] = f"{dadovalorcausa:.2f}"
    
    return emprestimos, total_consignado, total_emprestimo_geral, def_emprestimos, parcela_pessoal_atual, dif_bacen, vlr_total_emprestimo1, vlr_total_emprestimo2, org_bacen, org_div, total_dobro, valor_causa, comprometimento_renda, renda_atual, comprometimento_porcentagem, total_emprestimo_bacen, total_dobro_geral, dadovalorcausa

def gerar_documento(dados, num_emprestimos):
    template_path = os.path.abspath(os.path.join("modelos", f"modelo_{num_emprestimos}.docx"))
    if not os.path.exists(template_path):
        app.logger.error(f"Template {template_path} não encontrado")
        raise FileNotFoundError(f"Modelo de documento modelo_{num_emprestimos}.docx não encontrado")
    
    doc = Document(template_path)
    replacements = {
        'renda_mensal': dados['renda_mensal'],
        'parcela_pessoal': dados['parcela_pessoal'],
        'valor_liquido': dados['valor_liquido'],
        'comprometimento': dados['comprometimento'],
        'emprestimos': dados['emprestimos'],
        'total_emprestimo': dados['total_emprestimo'],
        'diario': dados['diario']
    }
    
    for p in doc.paragraphs:
        for key, value in flatten_dict(replacements).items():
            p.text = p.text.replace(f'{{{{{key}}}}}', bleach.clean(str(value)))
    
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
@login_required
def index():
    app.logger.debug("Acessando rota /")
    try:
        taxa_atual = get_bacen_taxa_atual()
        taxa_atual_display = f"{taxa_atual:.2f}%" if taxa_atual is not None else "Indisponível"
        bacen_data = {
            'taxa': taxa_atual_display,
            'data_atualizacao': datetime.now().strftime('%d/%m/%Y')
        }
        return render_template('index.html', **bacen_data)
    except Exception as e:
        app.logger.error(f"Erro na rota index: {str(e)}")
        return abort(500, "Erro interno ao carregar a página inicial")

def format_brl(valor):
    try:
        valor_dec = Decimal(str(valor))
        s = f"{valor_dec:,.2f}"
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return str(valor)

@app.route('/gerar-peticao', methods=['POST'])
@login_required
def gerar_peticao():
    try:
        app.logger.debug("Iniciando geração de petição")
        num_emprestimos = validar_dados_entrada(request.form)
        
        dados = {
            'renda_mensal': request.form['renda_mensal'].replace(",", "."),
            'parcela_pessoal': request.form['parcela_pessoal'].replace(",", "."),
        }
        
        emprestimos, total_consignado, total_emprestimo_geral, def_emprestimos, parcela_pessoal_atual, dif_bacen, vlr_total_emprestimo1, vlr_total_emprestimo2, org_bacen, org_div, total_dobro, valor_causa, comprometimento_renda, renda_atual, comprometimento_porcentagem, total_emprestimo_bacen, total_dobro_geral, dadovalorcausa = calculos_emprestimo(request.form, num_emprestimos)
        
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
        renda = Decimal(dados['renda_mensal'])
        parcela_pessoal = Decimal(dados['parcela_pessoal'])

        dados['valor_liquido'] = format_brl(renda - parcela_pessoal - total_consignado)
        dados['diario'] = format_brl((renda - parcela_pessoal - total_consignado) / 30)
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
        dados['total_dobro_geral'] = format_brl(total_dobro_geral)
        dados['valor_causa'] = format_brl(valor_causa)
        dados['dadovalorcausa'] = format_brl(dadovalorcausa)
        dados['comprometimento_renda'] = format_brl(comprometimento_renda)
        dados['renda_atual'] = format_brl(renda_atual)
        dados['comprometimento_porcentagem'] = format_brl(comprometimento_porcentagem)
        dados['total_emprestimo_bacen'] = format_brl(total_emprestimo_bacen)
        
        documento = gerar_documento(dados, num_emprestimos)
        app.logger.debug("Documento gerado com sucesso")
        
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

@app.route('/documentos/gerar', methods=['POST'])
@login_required
def gerar_documentos():
    app.logger.debug("Acessando rota /documentos/gerar")
    selecionados = request.form.getlist('documentos')
    if not selecionados:
        app.logger.error("Nenhum documento selecionado")
        abort(400, 'Nenhum documento selecionado.')

    selecionados = [doc for doc in selecionados if doc in VALID_TEMPLATES]
    if not selecionados:
        app.logger.error("Nenhum documento válido selecionado")
        abort(400, 'Nenhum documento válido selecionado.')

    placeholders = {key: request.form.get(key, '') for key in request.form.keys() if key != 'documentos'}

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zipf:
        for doc_tipo in selecionados:
            docx_path = os.path.join('modelos', f"{doc_tipo}.docx")
            if not os.path.exists(docx_path):
                app.logger.warning(f"Template {doc_tipo}.docx não encontrado.")
                continue

            # Preencher DOCX
            doc = Document(docx_path)
            for p in doc.paragraphs:
                for chave, valor in placeholders.items():
                    p.text = p.text.replace(f'{{{{{chave}}}}}', bleach.clean(valor))
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for chave, valor in placeholders.items():
                            cell.text = cell.text.replace(f'{{{{{chave}}}}}', bleach.clean(valor))

            # Salvar DOCX em memória e adicionar no ZIP
            output = io.BytesIO()
            doc.save(output)
            output.seek(0)
            zipf.writestr(f"{doc_tipo}.docx", output.read())

    zip_buffer.seek(0)
    app.logger.debug("Arquivo ZIP gerado com sucesso")
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        download_name='documentos.zip',
        as_attachment=True
    )
                                            


    
    app.logger.debug("Arquivo ZIP gerado com sucesso")
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        download_name='documentos.zip',
        as_attachment=True
    )

if __name__ == '__main__':
    app.run(debug=True, port=5000)