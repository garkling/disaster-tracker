import base64
from email.mime.text import MIMEText

import jinja2
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.settings import config
from app.pipeline import as_result
from app.credentials import OAuthCredentials
from app.disaster_events import DisasterAlert


class AlertNotifier:

    _EMAIL_TEMPLATE_FILE = "app/email_template.html"

    def __init__(self, credentials: OAuthCredentials):
        self._gmail_api = build('gmail', 'v1', credentials=credentials)

    @as_result(HttpError)
    def notify(self, alert: DisasterAlert) -> str:
        creds, _ = google.auth.default()

        rendered = self._render_content(alert)
        message = MIMEText(rendered, "html")

        message["To"] = alert.recipient
        message["From"] = config().default_email_sender
        message["Subject"] = "Disaster alert"

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        return (
            self._gmail_api.users()
            .messages()
            .send(userId="me", body={"raw": encoded_message})
            .execute()
        )

    def _render_content(self, alert: DisasterAlert):
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(".."))
        template = env.get_template(self._EMAIL_TEMPLATE_FILE)

        return template.render(alert=alert)
