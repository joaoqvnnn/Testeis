from flask import Flask, request, jsonify
from config import Config
from database import get_session, Transaction, User
import requests

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Recebe notificações do Mercado Pago."""
    data = request.json
    print("Webhook recebido:", data)

    # O Mercado Pago envia notificações de diferentes tipos. Para Payouts, geralmente é 'transaction' ou 'payout'.
    if 'transaction' in data.get('type', '') or 'payout' in data.get('action', ''):
        external_ref = data.get('data', {}).get('external_reference')
        status = data.get('data', {}).get('status')

        if external_ref and status:
            session = get_session()
            tx = session.query(Transaction).filter_by(external_reference=external_ref).first()
            
            if tx:
                if status in ['approved', 'completed']:
                    tx.status = 'completed'
                    # Busca o usuário para notificar
                    user = session.query(User).filter_by(id=tx.user_id).first()
                    if user:
                        mensagem = (
                            f"✅ *Saque Realizado com Sucesso!*\n\n"
                            f"Valor: R$ {tx.amount:.2f}\n"
                            f"Status: Concluído\n"
                            f"ID da Transação: `{tx.external_reference}`\n\n"
                            f"O valor já está disponível na conta de destino."
                        )
                        # Envia a mensagem para o Telegram do usuário
                        url_telegram = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            "chat_id": user.telegram_id,
                            "text": mensagem,
                            "parse_mode": "Markdown"
                        }
                        requests.post(url_telegram, json=payload)

                elif status in ['rejected', 'failed', 'cancelled']:
                    tx.status = 'failed'
                    user = session.query(User).filter_by(id=tx.user_id).first()
                    if user:
                        mensagem = (
                            f"❌ *Falha no Saque*\n\n"
                            f"Valor: R$ {tx.amount:.2f}\n"
                            f"Motivo: Transação rejeitada pelo banco.\n"
                            f"ID: `{tx.external_reference}`\n\n"
                            f"Verifique os dados da chave PIX e tente novamente."
                        )
                        url_telegram = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
                        payload = {
                            "chat_id": user.telegram_id,
                            "text": mensagem,
                            "parse_mode": "Markdown"
                        }
                        requests.post(url_telegram, json=payload)

                session.commit()
            session.close()

    return jsonify({"status": "received"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
