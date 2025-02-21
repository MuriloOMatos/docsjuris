import requests
import pandas as pd
from datetime import datetime
from typing import Dict, Optional
from flask import current_app as app

# Configuração da série do BACEN (pode ser movida para config.py)
SERIES_BACEN = {
    'pessoal_fisica': 20783  # Código da série histórica para taxas de juros
}

class BacenServiceError(Exception):
    """Exceção personalizada para erros do serviço BACEN."""
    pass

def get_bacen_taxa_juros() -> Optional[Dict[str, str]]:
    """Obtém a última taxa média de juros do BACEN.

    Faz uma requisição à API do Banco Central para obter a taxa de juros mais recente
    da série especificada em SERIES_BACEN.

    Returns:
        Dict com 'taxa', 'data_consulta' e 'data_atualizacao', ou None em caso de erro.

    Raises:
        BacenServiceError: Se houver falha na obtenção ou processamento dos dados.
    """
    try:
        url = (
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs."
            f"{SERIES_BACEN['pessoal_fisica']}/dados/ultimos/1?formato=json"
        )
        
        # Configuração de timeout para evitar travamentos
        response = requests.get(url, timeout=15)
        response.raise_for_status()  # Levanta exceção para códigos de status 4xx/5xx
        
        dados = response.json()
        if not dados or len(dados) == 0:
            raise BacenServiceError("Nenhum dado retornado pela API do BACEN")
        
        # Processamento dos dados
        df = pd.DataFrame(dados)
        df['data'] = pd.to_datetime(df['data'], dayfirst=True, errors='coerce')
        df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
        
        # Verificação de valores inválidos
        if df['valor'].isna().any() or df['data'].isna().any():
            raise BacenServiceError("Dados retornados contém valores inválidos")
        
        taxa_data = {
            'taxa': float(df.iloc[0]['valor']),
            'data_consulta': datetime.now().strftime('%d/%m/%Y %H:%M'),
            'data_atualizacao': df.iloc[0]['data'].strftime('%d/%m/%Y')
        }
        
        app.logger.info(f"Taxa do BACEN obtida com sucesso: {taxa_data['taxa']}%")
        return taxa_data
        
    except requests.RequestException as e:
        app.logger.error(f"Erro de conexão com a API do BACEN: {str(e)}")
        raise BacenServiceError(f"Falha na conexão com o BACEN: {str(e)}")
    except (ValueError, KeyError, IndexError) as e:
        app.logger.error(f"Erro ao processar resposta do BACEN: {str(e)}")
        raise BacenServiceError(f"Erro no processamento dos dados: {str(e)}")
    except Exception as e:
        app.logger.error(f"Erro inesperado no serviço BACEN: {str(e)}")
        raise BacenServiceError(f"Erro inesperado: {str(e)}")

def validate_bacen_data(data: Optional[Dict[str, str]]) -> bool:
    """Valida se os dados retornados do BACEN estão completos e válidos.

    Args:
        data: Dicionário com os dados retornados por get_bacen_taxa_juros.

    Returns:
        True se os dados são válidos, False caso contrário.
    """
    if data is None:
        return False
    
    required_keys = {'taxa', 'data_consulta', 'data_atualizacao'}
    if not all(key in data for key in required_keys):
        app.logger.warning("Dados do BACEN incompletos")
        return False
    
    try:
        float(data['taxa'])  # Verifica se a taxa é numérica
        datetime.strptime(data['data_consulta'], '%d/%m/%Y %H:%M')
        datetime.strptime(data['data_atualizacao'], '%d/%m/%Y')
        return True
    except (ValueError, TypeError) as e:
        app.logger.error(f"Dados do BACEN inválidos: {str(e)}")
        return False

if __name__ == "__main__":
    # Exemplo de uso standalone para teste
    from flask import Flask
    app = Flask(__name__)
    with app.app_context():
        try:
            taxa_info = get_bacen_taxa_juros()
            if validate_bacen_data(taxa_info):
                print(f"Taxa: {taxa_info['taxa']}%")
                print(f"Data Consulta: {taxa_info['data_consulta']}")
                print(f"Data Atualização: {taxa_info['data_atualizacao']}")
            else:
                print("Dados inválidos retornados")
        except BacenServiceError as e:
            print(f"Erro: {e}")