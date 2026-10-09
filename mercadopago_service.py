import requests
from config import Config

def realizar_payout_pix(valor, chave_pix_tipo, chave_pix_valor, cpf_titular, external_reference, webhook_url):
    """
    Realiza um Payout PIX via Mercado Pago em PRODUÇÃO.
    """
    url = "https://api.mercadopago.com/v1/transaction-intents/process"
    
    headers = {
        "Authorization": f"Bearer {Config.MERCADOPAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": external_reference
    }

    payload = {
        "external_reference": external_reference,
        "seller_configuration": {
            "notification_info": {
                "notification_url": webhook_url
            }
        },
        "transaction": {
            "from": {
                "accounts": [
                    { "amount": valor }
                ]
            },
            "to": {
                "accounts": [
                    {
                        "type": "current",
                        "amount": valor,
                        "chave": {
                            "type": chave_pix_tipo,
                            "value": chave_pix_valor
                        },
                        "owner": {
                            "identification": {
                                "type": "CPF",
                                "number": cpf_titular
                            }
                        }
                    }
                ]
            },
            "total_amount": valor
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erro na requisição de payout: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print("Detalhes do erro:", e.response.text)
        return {"error": str(e)}
