import os
import threading
import asyncio
import hashlib
import uuid
import base64
import io
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# --- Configurações ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# --- Banco de Dados ---
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
    external_reference = Column(String(100), unique=True)
    mp_payment_id = Column(String(50))
    nome_titular = Column(String(150))
    cpf_titular = Column(String(20))
    pix_tipo = Column(String(20))
    pix_chave = Column(String(150))
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)
def get_session(): return Session()

# --- Auxiliares ---
def hash_senha(s): return hashlib.sha256(s.encode()).hexdigest()
def is_admin(uid): return str(uid) == str(ADMIN_TELEGRAM_ID)
def mask_cpf(cpf): 
    if not cpf or len(cpf) < 11: return "***.***.***-**"
    return f"***.{cpf[3:6]}.{cpf[6:9]}-**"
def mask_chave(chave):
    if not chave: return "***"
    if len(chave) <= 4: return "*" * len(chave)
    return chave[:3] + "*" * (len(chave) - 5) + chave[-2:]

# --- Gera imagem de comprovante ---
def gerar_comprovante(nome, cpf, chave, tipo, valor, data, status, ext_ref):
    W, H = 800, 900
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except:
        font_big = font_med = font = ImageFont.load_default()

    # Cabeçalho
    d.rectangle([0, 0, W, 120], fill="#0d47a1")
    d.text((30, 40), "COMPROVANTE DE TRANSFERÊNCIA", font=font_big, fill="white")

    # Status verde
    d.rectangle([0, 120, W, 180], fill="#e8f5e9")
    d.text((30, 135), f"✔ {status}", font=font_med, fill="#2e7d32")

    # Valor destacado
    d.text((30, 210), "Valor", font=font_med, fill="#666")
    d.text((30, 240), f"R$ {valor:.2f}", font=font_big, fill="#0d47a1")

    # Linhas de dados
    y = 320
    dados = [
        ("Tipo de chave", tipo.upper()),
        ("Chave PIX", mask_chave(chave)),
        ("Nome do titular", nome),
        ("CPF do titular", mask_cpf(cpf)),
        ("Data", data),
        ("ID da transação", ext_ref),
        ("Instituição", "Mercado Pago"),
    ]
    for label, valor_txt in dados:
        d.text((30, y), label, font=font, fill="#888")
        d.text((280, y), str(valor_txt), font=font_med, fill="#222")
        d.line([(30, y + 35), (W - 30, y + 35)], fill="#eee", width=1)
        y += 55

    d.text((30, H - 60), "Comprovante gerado automaticamente pelo bot.", font=font, fill="#999")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ============================================================
# ======================= BOT TELEGRAM =======================
# ============================================================

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
        [InlineKeyboardButton("🔑 Definir Senha de Saque", callback_data="menu_senha")],
        [InlineKeyboardButton("➕ Depositar via PIX", callback_data="menu_depositar")]
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
    elif data == "menu_depositar":
        await query.edit_message_text("Digite o valor que deseja depositar via PIX (ex: 50.00):")
        context.user_data['acao'] = 'user_aguardando_valor_deposito'
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
            [InlineKeyboardButton("➕ Depositar Saldo (PIX real)", callback_data="admin_depositar")],
            [InlineKeyboardButton("👥 Listar Usuários", callback_data="admin_listar_users")],
            [InlineKeyboardButton("📜 Últimas Transações", callback_data="admin_transacoes")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="admin_voltar")]
        ]
        await query.edit_message_text("⚙️ Painel Administrativo:", reply_markup=InlineKeyboardMarkup(teclado_admin))
    elif data == "admin_voltar" and is_admin(user.id):
        await query.edit_message_text("Use /start para voltar ao menu principal.")
    elif data == "admin_depositar" and is_admin(user.id):
        await query.edit_message_text("Digite o ID do Telegram do usuário que vai receber o saldo:")
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

async def pix_tipo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    tipo = query.data.split("_")[1]
    context.user_data['pix_tipo'] = tipo
    context.user_data['acao'] = 'aguardando_chave_pix'
    await query.edit_message_text(f"Você escolheu *{tipo.upper()}*. Agora digite a chave PIX (só números, sem pontos ou traços):", parse_mode='Markdown')

async def confirmar_saque_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == "confirmar_saque":
        await query.edit_message_text("🔒 Digite sua senha de saque:")
        context.user_data['acao'] = 'aguardando_senha'
    else:
        await query.edit_message_text("❌ Saque cancelado.")
        context.user_data.clear()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; text = update.message.text
    session = get_session(); db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
    acao = context.user_data.get('acao')

    if acao == 'definir_senha':
        db_user.senha_hash = hash_senha(text); session.commit()
        context.user_data.clear(); await update.message.reply_text("✅ Senha definida!")
    elif acao == 'user_aguardando_valor_deposito':
        try:
            valor = float(text.replace(',', '.'))
            if valor <= 0: raise ValueError
            await gerar_pix_deposito(update, context, db_user, valor)
        except: await update.message.reply_text("❌ Valor inválido.")
        context.user_data.clear()
    elif acao == 'aguardando_valor_saque':
        try:
            valor = float(text.replace(',', '.'))
            if valor <= 0 or valor > db_user.balance: raise ValueError
            context.user_data['valor_saque'] = valor; context.user_data['acao'] = 'aguardando_tipo_pix'
            teclado = [
                [InlineKeyboardButton("CPF", callback_data="pix_CPF"), InlineKeyboardButton("E-mail", callback_data="pix_email")],
                [InlineKeyboardButton("Telefone", callback_data="pix_phone"), InlineKeyboardButton("Aleatória", callback_data="pix_random")]
            ]
            await update.message.reply_text("Escolha o tipo de chave PIX:", reply_markup=InlineKeyboardMarkup(teclado))
        except: await update.message.reply_text("❌ Valor inválido.")
    elif acao == 'aguardando_chave_pix':
        context.user_data['pix_valor'] = text
        context.user_data['acao'] = 'aguardando_nome_titular'
        await update.message.reply_text("Digite o *nome completo do titular* da conta:", parse_mode='Markdown')
    elif acao == 'aguardando_nome_titular':
        context.user_data['nome_titular'] = text
        context.user_data['acao'] = 'aguardando_cpf_titular'
        await update.message.reply_text("Digite o *CPF do titular* (só números):", parse_mode='Markdown')
    elif acao == 'aguardando_cpf_titular':
        context.user_data['cpf_titular'] = text
        tipo = context.user_data['pix_tipo']
        chave = context.user_data['pix_valor']
        nome = context.user_data['nome_titular']
        valor = context.user_data['valor_saque']
        resumo = (
            f"📋 *Confira os dados da transferência:*\n\n"
            f"💰 *Valor:* R$ {valor:.2f}\n"
            f"🏦 *Instituição:* Mercado Pago\n"
            f"🔑 *Tipo de chave:* {tipo.upper()}\n"
            f"🔑 *Chave:* `{chave}`\n"
            f"👤 *Titular:* {nome}\n"
            f"🪪 *CPF:* `{mask_cpf(text)}`\n\n"
            f"Os dados estão corretos?"
        )
        teclado = [[InlineKeyboardButton("✅ Confirmar", callback_data="confirmar_saque"), InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")]]
        await update.message.reply_text(resumo, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')
    elif acao == 'aguardando_senha':
        if db_user.senha_hash and hash_senha(text) == db_user.senha_hash:
            await update.message.reply_text("🔄 Processando saque...")
            await processar_saque(update, context, db_user, session)
        else:
            await update.message.reply_text("❌ Senha incorreta.")
    elif acao == 'admin_aguardando_id_deposito' and is_admin(user.id):
        context.user_data['admin_target_id'] = text
        context.user_data['acao'] = 'admin_aguardando_valor_deposito'
        await update.message.reply_text("Digite o valor do depósito PIX (ex: 50.00):")
    elif acao == 'admin_aguardando_valor_deposito' and is_admin(user.id):
        try:
            valor = float(text.replace(',', '.'))
            if valor <= 0: raise ValueError
            target_id = context.user_data.get('admin_target_id')
            target_user = session.query(User).filter_by(telegram_id=target_id).first()
            if not target_user:
                await update.message.reply_text("❌ Usuário não encontrado.")
            else:
                await gerar_pix_deposito(update, context, target_user, valor, admin=True)
        except: await update.message.reply_text("❌ Valor inválido.")
        context.user_data.clear()
    else:
        await update.message.reply_text("Use /start para ver o menu.")
    session.close()

# ============================================================
# ================== DEPÓSITO VIA PIX REAL ===================
# ============================================================
async def gerar_pix_deposito(update, context, target_user, valor, admin=False):
    ext_ref = f"DEP-{uuid.uuid4().hex[:12]}"
    url = "https://api.mercadopago.com/v1/payments"
    headers = {"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": ext_ref}
    payload = {
        "transaction_amount": float(valor),
        "description": f"Depósito no bot - User {target_user.telegram_id}",
        "payment_method_id": "pix",
        "external_reference": ext_ref,
        "notification_url": WEBHOOK_URL,
        "payer": {"email": f"user{target_user.telegram_id}@teste.com", "first_name": target_user.username or "Cliente"}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        qr_code = data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code")
        qr_code_base64 = data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code_base64")
        payment_id = str(data.get("id"))
        session = get_session()
        tx = Transaction(user_id=target_user.id, type='deposit', amount=valor, status='pending', external_reference=ext_ref, mp_payment_id=payment_id)
        session.add(tx); session.commit(); session.close()
        texto = f"💳 *Depósito PIX gerado!*\n\nUsuário: `{target_user.telegram_id}`\nValor: *R$ {valor:.2f}*\nID: `{payment_id}`\n\n👉 Pague o QR Code abaixo."
        await update.message.reply_text(texto, parse_mode='Markdown')
        if qr_code_base64:
            await update.message.reply_photo(photo=base64.b64decode(qr_code_base64), caption="📷 QR Code PIX")
        if qr_code:
            await update.message.reply_text(f"🔗 *PIX Copia e Cola:*\n\n`{qr_code}`", parse_mode='Markdown')
    except Exception as e:
        print(f"Erro PIX: {e}")
        if hasattr(e, "response") and e.response is not None: print("Detalhes:", e.response.text)
        await update.message.reply_text("❌ Erro ao gerar PIX.")

# ============================================================
# ======================= SAQUE PIX ==========================
# ============================================================
async def processar_saque(update, context, db_user, session):
    valor = context.user_data.get('valor_saque'); tipo = context.user_data.get('pix_tipo')
    chave = context.user_data.get('pix_valor'); cpf = context.user_data.get('cpf_titular')
    nome = context.user_data.get('nome_titular')
    ext_ref = f"SAQ-{uuid.uuid4().hex[:12]}"

    tx = Transaction(user_id=db_user.id, type='withdraw', amount=valor, status='pending',
                     external_reference=ext_ref, nome_titular=nome, cpf_titular=cpf,
                     pix_tipo=tipo, pix_chave=chave)
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
        # Salva dados para o comprovante
        context.user_data['comp_nome'] = nome
        context.user_data['comp_cpf'] = cpf
        context.user_data['comp_chave'] = chave
        context.user_data['comp_tipo'] = tipo
        context.user_data['comp_valor'] = valor
        context.user_data['comp_ref'] = ext_ref
    except Exception as e:
        print(f"Erro Payout: {e}")
        if hasattr(e, "response") and e.response is not None: print("Detalhes:", e.response.text)
        await update.message.reply_text("❌ Erro ao processar o saque. Verifique os dados.")
        tx.status = 'failed'; session.commit()
    session.close()

# ============================================================
# ======================= FLASK WEBHOOK ======================
# ============================================================
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("Webhook recebido:", data)

    # Depósito PIX
    if data.get("type") == "payment" or "payment" in data.get("action", ""):
        payment_id = str(data.get("data", {}).get("id"))
        if payment_id:
            r = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}",
                             headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"})
            if r.status_code == 200:
                pay = r.json(); status = pay.get("status"); ext_ref = pay.get("external_reference")
                if ext_ref and status == "approved":
                    session = get_session()
                    tx = session.query(Transaction).filter_by(external_reference=ext_ref).first()
                    if tx and tx.status != 'completed':
                        tx.status = 'completed'
                        user = session.query(User).filter_by(id=tx.user_id).first()
                        if user:
                            user.balance += tx.amount; session.commit()
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                          json={"chat_id": user.telegram_id, "text": f"✅ *Depósito confirmado!*\nValor: R$ {tx.amount:.2f}\nNovo saldo: R$ {user.balance:.2f}", "parse_mode": "Markdown"})
                    session.close()

    # Saque
    if 'transaction' in data.get('type', '') or 'payout' in data.get('action', ''):
        ext_ref = data.get('data', {}).get('external_reference'); status = data.get('data', {}).get('status')
        if ext_ref and status:
            session = get_session()
            tx = session.query(Transaction).filter_by(external_reference=ext_ref).first()
            if tx:
                user = session.query(User).filter_by(id=tx.user_id).first()
                if status in ['approved', 'completed']:
                    tx.status = 'completed'
                    # Desconta saldo
                    if user and user.balance >= tx.amount:
                        user.balance -= tx.amount
                    session.commit()
                    if user:
                        # Gera comprovante e envia
                        data_str = datetime.now().strftime("%d/%m/%Y %H:%M")
                        buf = gerar_comprovante(tx.nome_titular or "—", tx.cpf_titular or "", tx.pix_chave or "", tx.pix_tipo or "", tx.amount, data_str, "Saque realizado com sucesso", tx.external_reference)
                        files = {"photo": ("comprovante.png", buf, "image/png")}
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                                      data={"chat_id": user.telegram_id, "caption": "🧾 *Comprovante de transferência*", "parse_mode": "Markdown"},
                                      files=files)
                elif status in ['rejected', 'failed', 'cancelled']:
                    tx.status = 'failed'; session.commit()
                    if user:
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                      json={"chat_id": user.telegram_id, "text": f"❌ *Falha no Saque*\nValor: R$ {tx.amount:.2f}", "parse_mode": "Markdown"})
            session.close()

    return jsonify({"status": "received"}), 200

# ============================================================
# ======================= INICIALIZAÇÃO ======================
# ============================================================
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(pix_tipo_callback, pattern="^pix_"))
    application.add_handler(CallbackQueryHandler(confirmar_saque_callback, pattern="^(confirmar_saque|cancelar_saque)$"))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern="^(menu_|admin_)"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    async def _start():
        await application.initialize()
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        print("Bot rodando...")
        stop_event = asyncio.Event()
        await stop_event.wait()
    loop.run_until_complete(_start())

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
