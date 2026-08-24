import smtplib
from email.message import EmailMessage
from datetime import datetime
from app.core.config import settings


def enviar_email_alerta(destinatarios: list[str], produto: str, local: str, estoque: int):
    msg = EmailMessage()
    msg["Subject"] = f"⚠️ Alerta de Estoque Acabando - {produto}"
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = ", ".join(destinatarios)

    msg.set_content(f"""
                    
Olá,

O estoque do produto está perto de acabar.

📦 Produto: {produto}
📍 Local: {local}
📉 Estoque Atual: {estoque}
⌚ Data/Hora: {datetime.now().strftime('%d/%m/%Y %H:%M')}

Por favor, providencie a reposição.

Stock Control App

"Esta é uma mensagem automática. Dúvidas entrar em contato com o setor de TI.🖥️"


""")

    try:
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.EMAIL_USER, settings.EMAIL_PASSWORD)
            server.send_message(msg)

        print("E-mail enviado com sucesso!")

    except Exception as e:
        print("Erro ao enviar e-mail:", e)
