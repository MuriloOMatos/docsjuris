from flask import Flask, request, send_file, render_template
from docx import Document
import io
import os

app = Flask(__name__)

# Função para calcular a diferença entre os juros do contrato e a taxa média do BACEN
def calcular_diferenca(valor_financiado, taxa_contrato, taxa_media, parcelas):
    try:
        juros_contrato = (float(taxa_contrato) / 100) * float(valor_financiado) * int(parcelas)
        juros_media = (float(taxa_media) / 100) * float(valor_financiado) * int(parcelas)
        return juros_contrato - juros_media
    except ValueError:
        return 0.0

# Rota para exibir o formulário (index.html deve estar na pasta "templates")
@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

# Rota para processar o formulário e gerar a petição
@app.route('/gerar-peticao', methods=['POST'])
def gerar_peticao():
    # Verifica se todos os campos obrigatórios estão presentes
    required_fields = ['renda_mensal', 'data_contratacao', 'parcela_pessoal', 'modelo_peticao']
    for field in required_fields:
        if field not in request.form:
            return f"Erro: Campo '{field}' é obrigatório.", 400

    # Coleta dos dados gerais
    dados = {
        'renda_mensal': request.form.get('renda_mensal', '0').replace(",", "."),
        'data_contratacao': request.form.get('data_contratacao', ''),
        'parcela_pessoal': request.form.get('parcela_pessoal', '0').replace(",", "."),
        'parcela_consignado': request.form.get('parcela_consignado', '0').replace(",", "."),
        'quantidade_parcelas': request.form.get('quantidade_parcelas', '0'),
        'valor_financiado': request.form.get('valor_financiado', '0').replace(",", "."),
        'taxa_media_bacen': request.form.get('taxa_media_bacen', '0').replace(",", "."),
        'taxa_juros_contrato': request.form.get('taxa_juros_contrato', '0').replace(",", "."),
        'modelo_peticao': request.form.get('modelo_peticao', '')  # Valor esperado: "1", "2" ou "3"
    }

    # Verifica se o modelo foi selecionado
    modelo_selecionado = dados.get('modelo_peticao')
    if not modelo_selecionado:
        return "Erro: Modelo de petição não selecionado.", 400

    # Conversão dos campos gerais para cálculos
    try:
        renda_mensal = float(dados['renda_mensal'])
        parcela_pessoal = float(dados['parcela_pessoal'])
        parcela_consignado = float(dados['parcela_consignado'])
        quantidade_parcelas = int(dados['quantidade_parcelas'])
        valor_financiado = float(dados['valor_financiado'])
        taxa_media_bacen = float(dados['taxa_media_bacen']) / 100
    except ValueError:
        return "Erro: Valores inválidos fornecidos.", 400

    # Cálculo do valor líquido disponível (Renda - Parcelas)
    valor_liquido = renda_mensal - (parcela_consignado + parcela_pessoal)
    dados['valor_liquido'] = f"R$ {valor_liquido:.2f}"

    # Cálculo do valor líquido por dia (valor / 30)
    Valor_diario = valor_liquido / 30
    dados['Valor_diario'] = f"R$ {Valor_diario:.2f}"

    # Cálculo do comprometimento da renda
    comprometimento = (parcela_consignado + parcela_pessoal) / renda_mensal * 100
    dados['comprometimento_renda'] = f"{comprometimento:.2f}%"

    # Cálculo do Valor total parcela banco (Parcela * número de parcelas)
    total_emprestimo = parcela_consignado * quantidade_parcelas
    dados['total_emprestimo'] = f"R$ {total_emprestimo:.2f}"

    # Cálculo do Valor acima do emprestado (valor total / valor financiado)
    if valor_financiado != 0:
        acima_do_financiado = total_emprestimo / valor_financiado
        dados['acima_do_financiado'] = f"{acima_do_financiado:.2f}"
    else:
        dados['acima_do_financiado'] = "Indefinido (valor financiado é zero)"
        print("Erro: valor_financiado é zero, divisão não realizada.")

    # Cálculo Valor com base no BACEN (substitui o valor anterior de 'acima_do_financiado')
    acima_do_financiado_bacen = valor_financiado * ((1 + taxa_media_bacen) ** quantidade_parcelas)
    dados['acima_do_financiado_bacen'] = f"R$ {acima_do_financiado_bacen:.2f}"

    # Cálculo da diferença entre os juros do contrato e a taxa média do BACEN
    diferenca = calcular_diferenca(
        valor_financiado,
        dados['taxa_juros_contrato'],
        dados['taxa_media_bacen'],
        quantidade_parcelas
    )
    dados['diferenca_contrato1'] = f"R$ {diferenca:.2f}"

    # Mapeia a opção selecionada para o nome do arquivo de modelo
    modelo_mapping = {
        '1': 'emprestimo1',
        '2': 'emprestimo2',
        '3': 'emprestimo3'
    }
    if modelo_selecionado not in modelo_mapping:
        return "Erro: Modelo de petição inválido.", 400

    modelo_nome = modelo_mapping[modelo_selecionado]

    # Usa o caminho absoluto para localizar o arquivo de modelo
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    template_filename = os.path.join(BASE_DIR, "modelos", f"{modelo_nome}.docx")
    print("Diretório atual:", os.getcwd())
    print("Procurando o arquivo:", template_filename)

    if not os.path.exists(template_filename):
        return "Erro: Modelo de petição não encontrado.", 400

    # Carrega o template do modelo selecionado
    doc = Document(template_filename)

    # Substitui os placeholders nos parágrafos e tabelas do documento
    for paragraph in doc.paragraphs:
        for key, value in dados.items():
            placeholder = f"{{{{{key}}}}}"
            paragraph.text = paragraph.text.replace(placeholder, str(value))

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, value in dados.items():
                    placeholder = f"{{{{{key}}}}}"
                    cell.text = cell.text.replace(placeholder, str(value))

    # Salva o documento gerado em memória e o envia para download
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name="acao_revisional.docx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

if __name__ == '__main__':
    app.run(debug=True)
