from flask import Flask, request, send_file, render_template
from docx import Document
import io

app = Flask(__name__)

# Função para calcular a diferença entre os juros do contrato e a taxa média do BACEN
def calcular_diferenca(valor_financiado, taxa_contrato, taxa_media, parcelas):
    juros_contrato = (float(taxa_contrato) / 100) * float(valor_financiado) * int(parcelas)
    juros_media = (float(taxa_media) / 100) * float(valor_financiado) * int(parcelas)
    return juros_contrato - juros_media

# Rota para exibir o formulário
@app.route('/')
def index():
    return render_template('index.html')  # O index.html deve estar na pasta "templates"

# Rota para processar o formulário e gerar o documento Word
@app.route('/gerar-peticao', methods=['POST'])
def gerar_peticao():
    # Coleta dos dados do formulário
    modelo_selecionado = request.form['modelo_peticao']
    dados = {
        'renda_mensal': request.form['renda_mensal'],
        'parcela_consignado': request.form['parcela_consignado'],
        'parcela_pessoal': request.form['parcela_pessoal'],
        'data_contratacao': request.form['data_contratacao'],
        'valor_financiado': request.form['valor_financiado'],
        'quantidade_parcelas': request.form['quantidade_parcelas'],
        'taxa_juros_contrato': request.form['taxa_juros_contrato'],
        'taxa_media_bacen': request.form['taxa_media_bacen']
    }

    # Coleta os dados do formulário
    renda_mensal = float(request.form['renda_mensal'])
    parcela_consignado = float(request.form['parcela_consignado'])
    parcela_pessoal = float(request.form['parcela_pessoal'])

    # Cálculo do valor líquido disponível (Renda - Parcelas)
    valor_liquido = renda_mensal - (parcela_consignado + parcela_pessoal)
    dados['valor_liquido'] = f"R$ {valor_liquido:.2f}"
    
    # Cálculo do comprometimento da renda
    comprometimento = (float(dados['parcela_consignado']) + float(dados['parcela_pessoal'])) / float(dados['renda_mensal']) * 100
    dados['comprometimento_renda'] = f"{comprometimento:.2f}%"

     # Cálculo do Valor total parcela banco(Parcela * valor parcela)
    total_emprestimo = (float(dados['parcela_consignado']) * float(dados['quantidade_parcelas']))
    dados['total_emprestimo'] = f"{total_emprestimo:.2f}"

    # Cálculo do Valor acima do emprestado(valor total / valor financiado)
    acima_do_financiado = (float(dados['total_emprestimo']) / float(dados['valor_financiado']))
    dados['acima_do_financiado'] = f"{acima_do_financiado:.2f}"

    # Cálculo Valor com base no basen
    taxa_juros = float(dados['taxa_juros_contrato']) / 100  # Converter para decimal
    valor_financiado = float(dados['valor_financiado'])
    parcelas = int(dados['quantidade_parcelas'])

    acima_do_financiado = valor_financiado * ((1 + taxa_juros) ** parcelas)
    dados['acima_do_financiado'] = f"R$ {acima_do_financiado:.2f}"

    

    # Cálculo da diferença entre os juros do contrato e a taxa média do BACEN
    diferenca = calcular_diferenca(
        dados['valor_financiado'],
        dados['taxa_juros_contrato'],
        dados['taxa_media_bacen'],
        dados['quantidade_parcelas']
    )
    dados['diferenca_contrato1'] = f"R$ {diferenca:.2f}"

     # Abrir o modelo Word selecionado
    doc = Document(modelo_selecionado)

    # Percorre os parágrafos do documento para substituir os placeholders
    for paragraph in doc.paragraphs:
        for key, value in dados.items():
            placeholder = f'{{{{{key}}}}}'
            if placeholder in paragraph.text:
                paragraph.text = paragraph.text.replace(placeholder, str(value))
    
    # Se o documento possuir tabelas, cabeçalhos ou rodapés, percorra esses elementos conforme necessário.

    # Salva o documento gerado em memória
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)

    # Retorna o documento para download automático
    return send_file(output, download_name='acao_revisional.docx', as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
