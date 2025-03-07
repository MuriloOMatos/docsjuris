import requests
from datetime import datetime
import calendar
import logging
from functools import lru_cache

# Configuração de logging
logger = logging.getLogger('BACEN_API')

class TaxaNaoEncontradaError(Exception):
    pass

class BacenError(Exception):
    pass

def _obter_dados_api(params: dict) -> list:
    """
    Função para obter dados da API de séries temporais do Banco Central (BACEN).
    
    Args:
        params (dict): Parâmetros com a data inicial e final, entre outros.
    
    Returns:
        list: Lista de dados com as taxas diárias de juros.
    
    Raises:
        BacenError: Se ocorrer um erro na consulta à API.
    """
    serie_bacen = 25464  # Código da série histórica da taxa de juros para empréstimos pessoais
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie_bacen}/dados"

    # Adicionar parâmetros de data ao formato da URL
    data_inicial = params.get('dataInicial', '')
    data_final = params.get('dataFinal', '')
    formato = params.get('formato', 'json')  # Formato padrão é json
    
    # Construir a URL com os parâmetros necessários
    query_params = {
        'formato': formato,
        'dataInicial': data_inicial,
        'dataFinal': data_final
    }

    try:
        # Enviar a requisição GET
        response = requests.get(url, params=query_params)
        response.raise_for_status()  # Verifica se ocorreu algum erro na requisição
        
        # Retornar os dados JSON
        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na requisição à API: {str(e)}", exc_info=True)
        raise BacenError(f"Erro na consulta à API Bacen: {str(e)}") from e

@lru_cache(maxsize=128)
def get_taxa_mensal(mes: int, ano: int) -> float:
    """
    Obtém a taxa média mensal de juros para empréstimos pessoais.

    Args:
        mes (int): Mês (1-12)
        ano (int): Ano com 4 dígitos

    Returns:
        float: Média mensal de juros (% ao mês)

    Raises:
        TaxaNaoEncontradaError: Se não houver dados suficientes
        BacenError: Para erros na API
    """
    # Validação dos parâmetros
    if not (1 <= mes <= 12):
        raise ValueError(f"Mês inválido: {mes}. Deve estar entre 1 e 12.")
    if ano < 2000 or ano > datetime.now().year:
        raise ValueError(f"Ano inválido: {ano}. Deve ser entre 2000 e o ano atual.")

    # Definir intervalo do mês
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    params = {
        'formato': 'json',
        'dataInicial': f"01/{mes:02d}/{ano}",
        'dataFinal': f"{ultimo_dia:02d}/{mes:02d}/{ano}"
    }

    try:
        # Obter dados da API
        dados = _obter_dados_api(params)

        # Processar valores
        valores_validos = []
        for registro in dados:
            # Verificar estrutura do registro
            if not isinstance(registro, dict) or 'data' not in registro or 'valor' not in registro:
                logger.debug(f"Registro mal formatado ignorado: {registro}")
                continue

            try:
                valor = float(registro['valor'])
                data_registro = datetime.strptime(registro['data'], '%d/%m/%Y')

                # Verificar consistência da data
                if data_registro.month != mes or data_registro.year != ano:
                    logger.debug(f"Data fora do mês ignorada: {registro['data']}")
                    continue

                # Ignorar valores inválidos
                if valor <= 0:
                    logger.debug(f"Valor inválido ignorado: {valor} em {registro['data']}")
                    continue

                valores_validos.append(valor)

            except (ValueError, TypeError) as e:
                logger.debug(f"Erro ao processar registro: {str(e)}")
                continue

        # Verificar se há dados suficientes
        if not valores_validos:
            raise TaxaNaoEncontradaError(f"Nenhum dado válido para {mes:02d}/{ano}")

        # Estimativa simples de dias úteis (5/7 dos dias totais como aproximação)
        dias_uteis_esperados = (ultimo_dia * 5) // 7
        if len(valores_validos) < dias_uteis_esperados * 0.5:  # Menos de 50% dos dias úteis
            logger.warning(f"Apenas {len(valores_validos)} dias válidos para {mes:02d}/{ano}, menos que 50% dos esperados ({dias_uteis_esperados}).")

        # Calcular média
        media = round(sum(valores_validos) / len(valores_validos), 4)
        logger.info(f"Taxa média para {mes:02d}/{ano}: {media}% (Base: {len(valores_validos)} dias)")
        return media

    except BacenError:
        raise
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}", exc_info=True)
        raise BacenError("Erro no processamento dos dados") from e

# Exemplo de uso
if __name__ == "__main__":
    try:
        taxa = get_taxa_mensal(3, 2023)
        print(f"Taxa média de Março/2023: {taxa}%")
    except Exception as e:
        print(f"Erro: {str(e)}")
