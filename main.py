from flask import Flask, request, send_file, render_template
from docx import Document
import io

app = Flask(__name__)

# Função para calcular a diferença entre os juros do contrato e a taxa média do BACEN
def calcular_diferenca(valor_financiado, taxa_contrato, taxa_media, parcelas):
    juros_contrato = (float(taxa_contrato) / 100) * float(valor_financiado) * int(parcelas)
    juros_media = (float(taxa_media) / 100) * float(valor_financiado) * int(parcelas)
    return juros_contrato - juros_media

@app.route('/')
def index():
    return render_template('index.html')  # O arquivo index.html deve estar na pasta "templates"

@app.route('/gerar-peticao', methods=['POST'])
def gerar_peticao():
    # Coleta dos dados gerais (não duplicados)
    dados = {
        'renda_mensal': request.form['renda_mensal'],
        'data_contratacao': request.form['data_contratacao'],
        'parcela_pessoal': request.form['parcela_pessoal']
    }
    
    # Conversão dos campos gerais para cálculos
    try:
        renda_mensal = float(request.form['renda_mensal'])
    except:
        renda_mensal = 0.0
    try:
        parcela_pessoal = float(request.form['parcela_pessoal'])
    except:
        parcela_pessoal = 0.0

    # Cálculo do valor líquido disponível (Renda - Parcela Pessoal)
    valor_liquido = renda_mensal - parcela_pessoal
    dados['valor_liquido'] = f"R$ {valor_liquido:.2f}"
    
    # Cálculo do comprometimento da renda (global)
    comprometimento = (parcela_pessoal / renda_mensal * 100) if renda_mensal != 0 else 0
    dados['comprometimento_renda'] = f"{comprometimento:.2f}%"
    
    # Coleta do número de empréstimos (informado no campo "modelo_peticao")
    try:
        num_loans = int(request.form['modelo_peticao'])
    except (TypeError, ValueError):
        num_loans = 0

    # Para cada empréstimo, coleta os dados e realiza os cálculos individuais
    for i in range(num_loans):
        valor = request.form.get(f"emprestimos[{i}][valor]", "")
        parcela_consignada = request.form.get(f"emprestimos[{i}][parcela_consignada]", "")
        data = request.form.get(f"emprestimos[{i}][data]", "")
        parcelas = request.form.get(f"emprestimos[{i}][parcelas]", "")
        taxa = request.form.get(f"emprestimos[{i}][taxa]", "")
        
        # Armazena os dados do empréstimo no dicionário com chaves únicas para cada empréstimo
        dados[f"emprestimos_{i}_valor"] = valor
        dados[f"emprestimos_{i}_parcela_consignada"] = parcela_consignada
        dados[f"emprestimos_{i}_data"] = data
        dados[f"emprestimos_{i}_parcelas"] = parcelas
        dados[f"emprestimos_{i}_taxa"] = taxa
        
        # Cálculo do montante acumulado para o empréstimo i (juros compostos)
        try:
            loan_valor = float(valor)
            loan_taxa = float(taxa) / 100
            loan_parcelas = int(parcelas)
            montante = loan_valor * ((1 + loan_taxa) ** loan_parcelas)
        except:
            montante = 0.0
        dados[f"emprestimos_{i}_montante"] = f"R$ {montante:.2f}"
        
        # Cálculo da diferença entre os juros do contrato e a taxa média do BACEN para o empréstimo i
        try:
            diferenca_loan = calcular_diferenca(valor, taxa, request.form.get('taxa_media_bacen', 0), parcelas)
        except:
            diferenca_loan = 0.0
        dados[f"emprestimos_{i}_diferenca"] = f"R$ {diferenca_loan:.2f}"
    
    # Seleciona o modelo Word com base no número de empréstimos (arquivos: modelo_1.docx, modelo_2.docx, etc.)
    template_filename = f"modelo_{num_loans}.docx"
    doc = Document(template_filename)
    
    # Substitui os placeholders nos parágrafos do documento
    for paragraph in doc.paragraphs:
        for key, value in dados.items():
            placeholder = f"{{{{{key}}}}}"  # Exemplo: {{renda_mensal}}, {{emprestimos_0_valor}}, etc.
            if placeholder in paragraph.text:
                paragraph.text = paragraph.text.replace(placeholder, str(value))
                
    # Substitui os placeholders em tabelas, se houver
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, value in dados.items():
                    placeholder = f"{{{{{key}}}}}"
                    if placeholder in cell.text:
                        cell.text = cell.text.replace(placeholder, str(value))
                        
    # Salva o documento gerado em memória e o envia para download
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)

    return send_file(output, download_name="acao_revisional.docx", as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
