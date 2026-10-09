import os
import threading
import asyncio
import logging
import hashlib
import uuid
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests
from dotenv import load_dotenv

load_dotenv()

# --- Configurações ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- Banco de Dados (SQLite) ---
Base = declarative_base()
engine = create_engine('sqlite:///database.db', echo=False, connect_args={'check_same_thread': False})
Session = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(50), unique=True, nullable=False)
    username = Column(String(100))
    balance = Column(Float, default=0.0)
    senha_hash = Column(String(128))

class Transaction(Base):
    __tablename__ = 'transactions'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    type = Column(String(20))
    amount = Column(Float, nullable=False)
    status = Column(String(20), default='pending')
    external_reference = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)
def get_session(): return Session()

# --- Funções Auxiliares ---
def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()
def is_admin(uid): return str(uid) == str(ADMIN_TELEGRAM_ID)

# --- Bot do Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = get_session()
    db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
    if not db_user:
        db_user = User(telegram_id=str(user.id), username=user.username or user.first_name)
        session.add(db_user); session.commit()
    session.close()

    teclado = [
        [InlineKeyboardButton("💰 Meu Saldo", callback_data="menu_saldo")],
        [InlineKeyboardButton("💸 Sacar PIX", callback_data="menu_sacar")],
        [InlineKeyboardButton("🔑 Definir Senha de Saque", callback_data="menu_senha")]
    ]
    if is_admin(user.id):
        teclado.append([InlineKeyboardButton("⚙️ Painel Admin", callback_data="admin_painel")])
    await update.message.reply_text(f"Olá, {user.first_name}! Escolha uma opção:", reply_markup=InlineKeyboardMarkup(teclado))

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data; user = query.from_user
    session = get_session(); db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()

    if data == "menu_saldo":
        await query.edit_message_text(f"💰 Seu saldo atual é: R$ {db_user.balance:.2f}")
    elif data == "menu_sacar":
        if db_user.balance <= 0: await query.edit_message_text("❌ Saldo insuficiente.")
        elif not db_user.senha_hash: await query.edit_message_text("⚠️ Defina uma senha de saque primeiro.")
        else:
            await query.edit_message_text("Qual o valor do saque? (Ex: 10.50)")
            context.user_data['acao'] = 'aguardando_valor_saque'
    elif data == "menu_senha":
        await query.edit_message_text("Digite a nova senha de saque:")
        context.user_data['acao'] = 'definir_senha'
    elif data == "admin_painel" and is_admin(user.id):
        teclado_admin = [
            [InlineKeyboardButton("➕ Depositar Saldo", callback_data="admin_depositar")],
            [InlineKeyboardButton("👥 Listar Usuários", callback_data="admin_listar_users")],
            [InlineKeyboardButton("📜 Últimas Transações", callback_data="admin_transacoes")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="admin_voltar")]
        ]
        await query.edit_message_text("⚙️ Painel Administrativo:", reply_markup=InlineKeyboardMarkup(teclado_admin))
    elif data == "admin_voltar" and is_admin(user.id):
        await query.edit_message_text("Use /start para voltar ao menu principal.")
    elif data == "admin_depositar" and is_admin(user.id):
        await query.edit_message_text("Digite o ID do Telegram do usuário:")
        context.user_data['acao'] = 'admin_aguardando_id_deposito'
    elif data == "admin_listar_users" and is_admin(user.id):
        users = session.query(User).all()
        texto = "👥 *Usuários:*\n\n" + "\n".join([f"ID: `{u.telegram_id}` | Saldo: R$ {u.balance:.2f}" for u in users])
        await query.edit_message_text(texto, parse_mode='Markdown')
    elif data == "admin_transacoes" and is_admin(user.id):
        txs = session.query(Transaction).order_by(Transaction.created_at.desc()).limit(10).all()
        texto = "📜 *Últimas Transações:*\n\n" + "\n".join([f"ID: {tx.id} | {tx.type} | R$ {tx.amount:.2f} | {tx.status}" for tx in txs])
        await query.edit_message_text(texto, parse_mode='Markdown')
    session.close()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; text = update.message.text
    session = get_session(); db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
    acao = context.user_data.get('acao')

    if acao == 'definir_senha':
        db_user.senha_hash = hash_senha(text); session.commit()
        context.user_data.clear(); await update.message.reply_text("✅ Senha definida!")
    elif acao == 'aguardando_valor_saque':
        try:
            valor = float(text.replace(',', '.'))
            if valor <= 0 or valor > db_user.balance: raise ValueError
            context.user_data['valor_saque'] = valor; context.user_data['acao'] = 'aguardando_tipo_pix'
            teclado = [[InlineKeyboardButton("CPF", callback_data="pix_CPF"), InlineKeyboardButton("E-mail", callback_data="pix_email")]]
            await update.message.reply_text("Escolha o tipo de chave PIX:", reply_markup=InlineKeyboardMarkup(teclado))
        except: await update.message.reply_text("❌ Valor inválido.")
    elif acao == 'aguardando_chave_pix':
        context.user_data['pix_valor'] = text; context.user_data['acao'] = 'aguardando_cpf_titular'
        await update.message.reply_text("Digite o CPF do titular:")
    elif acao == 'aguardando_cpf_titular':
        context.user_data['cpf_titular'] = text
        resumo = f"📋 *Confirme:*\nValor: R$ {context.user_data['valor_saque']:.2f}\nChave: {context.user_data['pix_tipo']} - {context.user_data['pix_valor']}\nCPF: {text}"
        teclado = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_saque"), InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")]]
        await update.message.reply_text(resumo, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    elif acao == 'aguardando_senha':
        if db_user.senha_hash and hash_senha(text) == db_user.senha_hash:
            await update.message.reply_text("🔄 Processando saque...")
            await processar_saque(update, context, db_user, session)
        else: await update.message.reply_text("❌ Senha incorreta.")
    elif acao == 'admin_aguardando_id_deposito' and is_admin(user.id):
        context.user_data['admin_target_id'] = text; context.user_data['acao'] = 'admin_aguardando_valor_deposito'
        await update.message.reply_text("Digite o valor do depósito:")
    elif acao == 'admin_aguardando_valor_deposito' and is_admin(user.id):
        try:
            valor = float(text.replace(',', '.')); target_id = context.user_data.get('admin_target_id')
            target_user = session.query(User).filter_by(telegram_id=target_id).first()
            if target_user:
                target_user.balance += valor; session.add(Transaction(user_id=target_user.id, type='deposit', amount=valor, status='completed')); session.commit()
                await update.message.reply_text(f"✅ Depósito de R$ {valor:.2f} realizado.")
            else: await update.message.reply_text("❌ Usuário não encontrado.")
        except: await update.message.reply_text("❌ Valor inválido.")
        context.user_data.clear()
    else: await update.message.reply_text("Use /start para ver o menu.")
    session.close()

async def pix_tipo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data['pix_tipo'] = query.data.split("_")[1]; context.user_data['acao'] = 'aguardando_chave_pix'
    await query.edit_message_text("Digite a chave PIX:")

async def confirmar_saque_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == "confirmar_saque":
        await query.edit_message_text("🔒 Digite sua senha de saque:")
        context.user_data['acao'] = 'aguardando_senha'
    else:
        await query.edit_message_text("❌ Saque cancelado."); context.user_data.clear()

async def processar_saque(update, context, db_user, session):
    valor = context.user_data.get('valor_saque'); tipo = context.user_data.get('pix_tipo')
    chave = context.user_data.get('pix_valor'); cpf = context.user_data.get('cpf_titular')
    ext_ref = str(uuid.uuid4())[:20]
    
    tx = Transaction(user_id=db_user.id, type='withdraw', amount=valor, status='pending', external_reference=ext_ref)
    session.add(tx); session.commit()

    url = "https://api.mercadopago.com/v1/transaction-intents/process"
    headers = {"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": ext_ref}
    payload = {
        "external_reference": ext_ref,
        "seller_configuration": {"notification_info": {"notification_url": WEBHOOK_URL}},
        "transaction": {
            "from": {"accounts": [{"amount": valor}]},
            "to": {"accounts": [{"type": "current", "amount": valor, "chave": {"type": tipo.upper(), "value": chave}, "owner": {"identification": {"type": "CPF", "number": cpf}}}]},
            "total_amount": valor
        }
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        await update.message.reply_text("⏳ Saque solicitado! Aguardando confirmação do banco...")
    except Exception as e:
        print(f"Erro Payout: {e}")
        await update.message.reply_text("❌ Erro ao processar o saque. Verifique os dados.")
        tx.status = 'failed'; session.commit()
    context.user_data.clear(); session.close()

# --- Flask App (Webhook) ---
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook recebido:", data)
    if 'transaction' in data.get('type', '') or 'payout' in data.get('action', ''):
        ext_ref = data.get('data', {}).get('external_reference')
        status = data.get('data', {}).get('status')
        if ext_ref and status:
            session = get_session()
            tx = session.query(Transaction).filter_by(external_reference=ext_ref).first()
            if tx:
                user = session.query(User).filter_by(id=tx.user_id).first()
                if status in ['approved', 'completed']:
                    tx.status = 'completed'; session.commit()
                    if user:
                        msg = f"✅ *Saque Realizado!*\nValor: R$ {tx.amount:.2f}\nStatus: Concluído"
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": user.telegram_id, "text": msg, "parse_mode": "Markdown"})
                elif status in ['rejected', 'failed', 'cancelled']:
                    tx.status = 'failed'; session.commit()
                    if user:
                        msg = f"❌ *Falha no Saque*\nValor: R$ {tx.amount:.2f}\nMotivo: Rejeitado pelo banco."
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": user.telegram_id, "text": msg, "parse_mode": "Markdown"})
            session.close()
    return jsonify({"status": "received"}), 200

# --- Inicialização do Bot (versão assíncrona sem dependência de main thread) ---
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_handler(CallbackQueryHandler(pix_tipo_callback, pattern="^pix_"))
    application.add_handler(CallbackQueryHandler(confirmar_saque_callback, pattern="^(confirmar_saque|cancelar_saque)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def _start():
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        print("Bot rodando...")
        stop_event = asyncio.Event()
        await stop_event.wait()

    loop.run_until_complete(_start())

# Inicia o bot automaticamente quando o Gunicorn importar o módulo
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
