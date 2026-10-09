import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from config import Config
from database import get_session, User, Transaction, init_db
from mercadopago_service import realizar_payout_pix
import hashlib
import uuid

# Configuração de logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Inicializa o banco de dados
init_db()

# --- Funções Auxiliares ---

def hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

def is_admin(user_id: int) -> bool:
    """Verifica se o ID do usuário é o admin configurado no .env"""
    return str(user_id) == str(Config.ADMIN_TELEGRAM_ID)

# --- Handlers do Bot ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start: registra o usuário e mostra o menu."""
    user = update.effective_user
    session = get_session()
    db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
    
    if not db_user:
        db_user = User(telegram_id=str(user.id), username=user.username or user.first_name)
        session.add(db_user)
        session.commit()
    
    session.close()

    teclado = [
        [InlineKeyboardButton("💰 Meu Saldo", callback_data="menu_saldo")],
        [InlineKeyboardButton("💸 Sacar PIX", callback_data="menu_sacar")],
        [InlineKeyboardButton("🔑 Definir Senha de Saque", callback_data="menu_senha")]
    ]
    
    if is_admin(user.id):
        teclado.append([InlineKeyboardButton("⚙️ Painel Admin", callback_data="admin_painel")])

    await update.message.reply_text(
        f"Olá, {user.first_name}! Bem-vindo ao seu painel.\nEscolha uma opção abaixo:",
        reply_markup=InlineKeyboardMarkup(teclado)
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa os cliques nos menus."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    session = get_session()
    db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()

    if data == "menu_saldo":
        await query.edit_message_text(f"💰 Seu saldo atual é: R$ {db_user.balance:.2f}")

    elif data == "menu_sacar":
        if db_user.balance <= 0:
            await query.edit_message_text("❌ Você não tem saldo suficiente para sacar.")
        elif not db_user.senha_hash:
            await query.edit_message_text("⚠️ Você precisa definir uma senha de saque primeiro. Use o menu 'Definir Senha'.")
        else:
            await query.edit_message_text("Qual o valor do saque? (Digite apenas números, ex: 10.50)")
            context.user_data['acao'] = 'aguardando_valor_saque'

    elif data == "menu_senha":
        await query db.edit_message_text("Digite a nova senha de saque (será salva de_user, forma session seg)
ura):")
        context.user_data['       acao'] = 'definir_s elseenha'

    elif data == "admin:
_painel" and is_admin(user.id           ):
        teclado_admin = [
            [InlineKeyboardButton("➕ Depositar Saldo", callback_data="admin await update_d.messageepositar")],
            [InlineKeyboardButton("👥 Listar Usuários", callback_data="admin_listar_users")],
            [InlineKeyboardButton("📜 Últimas Transações", callback_data="admin_transacoes")],
            [InlineKeyboardButton("🔙 Voltar ao Menu", callback_data="admin_voltar")]
        ]
        await query.edit_message_text("⚙️ Painel Administrativo:", reply_markup=InlineKeyboardMarkup(teclado_admin))

    elif data == "admin_voltar" and is_admin(user.id):
        teclado = [
            [InlineKeyboardButton("💰 Meu Saldo", callback_data="menu_saldo")],
            [InlineKeyboardButton("💸 Sacar PIX", callback_data="menu_sacar")],
            [InlineKeyboardButton("🔑 Definir Senha de Saque", callback_data="menu_senha")],
            [InlineKeyboardButton("⚙️ Painel Admin", callback_data="admin_painel")]
        ]
        await query.edit_message_text("Menu Principal:", reply_markup=InlineKeyboardMarkup(teclado))

    elif data == "admin_depositar" and is_admin(user.id):
        await query.edit_message_text("Digite o ID do Telegram do usuário que receberá o depósito:")
        context.user_data['acao'] = 'admin_aguardando_id_deposito'

    elif data == "admin_listar_users" and is_admin(user.id):
        users = session.query(User).all()
        texto = "👥 *Usuários Cadastrados:*\n\n"
        for u in users:
            texto += f"ID: `{u.telegram_id}` | Nome: {u.username} | Saldo: R$ {u.balance:.2f}\n"
        await query.edit_message_text(texto, parse_mode='Markdown')

    elif data == "admin_transacoes" and is_admin(user.id):
        txs = session.query(Transaction).order_by(Transaction.created_at.desc()).limit(10).all()
        texto = "📜 *Últimas 10 Transações:*\n\n"
        for tx in txs:
            texto += f"ID: {tx.id} | Tipo: {tx.type} | Valor: R$ {tx.amount:.2f} | Status: {tx.status}\n"
        await query.edit_message_text(texto, parse_mode='Markdown')

    session.close()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa mensagens de texto baseadas no estado atual."""
    user = update.effective_user
    text = update.message.text
    session = get_session()
    db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
    acao = context.user_data.get('acao')

    # --- Fluxo do Cliente ---
    if acao == 'definir_senha':
        db_user.senha_hash = hash_senha(text)
        session.commit()
        context.user_data.clear()
        await update.message.reply_text("✅ Senha de saque definida com sucesso!")

    elif acao == 'aguardando_valor_saque':
        try:
            valor = float(text.replace(',', '.'))
            if valor <= 0 or valor > db_user.balance:
                raise ValueError
            context.user_data['valor_saque'] = valor
            context.user_data['acao'] = 'aguardando_tipo_pix'
            
            teclado = [
                [InlineKeyboardButton("CPF", callback_data="pix_CPF"), InlineKeyboardButton("E-mail", callback_data="pix_email")],
                [InlineKeyboardButton("Telefone", callback_data="pix_phone"), InlineKeyboardButton("Aleatória", callback_data="pix_random")]
            ]
            await update.message.reply_text("Escolha o tipo de chave PIX:", reply_markup=InlineKeyboardMarkup(teclado))
        except ValueError:
            await update.message.reply_text("❌ Valor inválido ou saldo insuficiente. Tente novamente.")

    elif acao == 'aguardando_chave_pix':
        context.user_data['pix_valor'] = text
        await update.message.reply_text("Digite o CPF do titular da conta (apenas números):")
        context.user_data['acao'] = 'aguardando_cpf_titular'

    elif acao == 'aguardando_cpf_titular':
        context.user_data['cpf_titular'] = text
        resumo = (
            f"📋 *Confirme os dados do saque:*\n\n"
            f"Valor: R$ {context.user_data['valor_saque']:.2f}\n"
            f"Chave: {context.user_data['pix_tipo']} - {context.user_data['pix_valor']}\n"
            f"CPF: {text}\n\n"
            "Os dados estão corretos?"
        )
        teclado = [
            [InlineKeyboardButton("✅ Sim, confirmar", callback_data="confirmar_saque")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_saque")]
        ]
        await update.message.reply_text(resumo, reply_markup=InlineKeyboardMarkup(teclado), parse_mode='Markdown')

    elif acao == 'aguardando_senha':
        if db_user.senha_hash and hash_senha(text) == db_user.senha_hash:
            await update.message.reply_text("🔄 Senha correta! Processando saque...")
            await processar_saque(update, context,.reply_text("❌ Senha incorreta. Tente novamente ou use /start para cancelar.")

    # --- Fluxo do Administrador ---
    elif acao == 'admin_aguardando_id_deposito' and is_admin(user.id):
        context.user_data['admin_target_id'] = text
        await update.message.reply_text("Agora digite o valor a ser depositado (ex: 50.00):")
        context.user_data['acao'] = 'admin_aguardando_valor_deposito'

    elif acao == 'admin_aguardando_valor_deposito' and is_admin(user.id):
        try:
            valor = float(text.replace(',', '.'))
            target_id = context.user_data.get('admin_target_id')
            target_user = session.query(User).filter_by(telegram_id=target_id).first()
            if target_user:
                target_user.balance += valor
                tx = Transaction(user_id=target_user.id, type='deposit', amount=valor, status='completed')
                session.add(tx)
                session.commit()
                await update.message.reply_text(f"✅ Depósito de R$ {valor:.2f} realizado para o ID {target_id}.")
            else:
                await update.message.reply_text("❌ Usuário não encontrado.")
        except ValueError:
            await update.message.reply_text("❌ Valor inválido.")
        context.user_data.clear()

    else:
        await update.message.reply_text("Comando não reconhecido. Use /start para ver o menu.")

    session.close()

async def pix_tipo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recebe o tipo de chave PIX escolhido."""
    query = update.callback_query
    await query.answer()
    tipo = query.data.split("_")[1]
    context.user_data['pix_tipo'] = tipo
    await query.edit_message_text(f"Você escolheu {tipo.upper()}. Agora digite a chave PIX:")
    context.user_data['acao'] = 'aguardando_chave_pix'

async def confirmar_saque_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma ou cancela o saque."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "confirmar_saque":
        await query.edit_message_text("🔒 Para sua segurança, digite sua senha de saque:")
        context.user_data['acao'] = 'aguardando_senha'
    elif data == "cancelar_saque":
        await query.edit_message_text("❌ Saque cancelado.")
        context.user_data.clear()

async def processar_saque(update, context, db_user, session):
    """Chama a API do Mercado Pago para realizar o payout."""
    valor = context.user_data.get('valor_saque')
    tipo = context.user_data.get('pix_tipo')
    chave = context.user_data.get('pix_valor')
    cpf = context.user_data.get('cpf_titular')
    ext_ref = str(uuid.uuid4())[:20]

    tx = Transaction(user_id=db_user.id, type='withdraw', amount=valor, status='pending', external_reference=ext_ref)
    session.add(tx)
    session.commit()

    # Chama o serviço do Mercado Pago REAL
    resultado = realizar_payout_pix(valor, tipo.upper(), chave, cpf, ext_ref, Config.WEBHOOK_URL)

    if "error" in resultado:
        await update.message.reply_text("❌ Erro ao processar o saque. Verifique os dados e tente novamente.")
        tx.status = 'failed'
        session.commit()
    else:
        await update.message.reply_text("⏳ Saque solicitado! Você receberá a confirmação em instantes assim que o banco processar.")
        # O status final será atualizado pelo webhook

    context.user_data.clear()
    session.close()

def main():
    """Inicia o bot."""
    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("start", start))

    # Callbacks de botões
    application.add_handler(CallbackQueryHandler(menu_callback))
    application.add_handler(CallbackQueryHandler(pix_tipo_callback, pattern="^pix_"))
    application.add_handler(CallbackQueryHandler(confirmar_saque_callback, pattern="^(confirmar_saque|cancelar_saque)$"))

    # Mensagens de texto
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot iniciado... Pressione Ctrl+C para parar.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
