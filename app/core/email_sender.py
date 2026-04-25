"""
邮件发送工具（SMTP）。

默认从 Settings 读取 SMTP 配置，适用于通知类邮件发送。
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Iterable, Sequence

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailSender:
    """基于 SMTP 的轻量邮件发送器。"""

    def __init__(self) -> None:
        self.settings = get_settings()

    def send(
        self,
        *,
        to_emails: Sequence[str],
        subject: str,
        text_body: str | None = None,
        html_body: str | None = None,
    ) -> bool:
        """
        发送邮件（至少提供 text_body 或 html_body 之一）。

        返回 True 表示 SMTP 调用成功，False 表示发送失败或邮件功能未启用。
        """
        if not self.settings.EMAIL_ENABLED:
            logger.info("邮件发送未启用，跳过发送 subject=%s", subject)
            return False

        recipients = self._normalize_recipients(to_emails)
        if not recipients:
            raise ValueError("to_emails 不能为空")
        if not subject.strip():
            raise ValueError("subject 不能为空")
        if not text_body and not html_body:
            raise ValueError("text_body 与 html_body 不能同时为空")

        username = self.settings.EMAIL_USERNAME
        password = self.settings.EMAIL_PASSWORD
        if not username or not password:
            raise ValueError("未配置 EMAIL_USERNAME 或 EMAIL_PASSWORD")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((self.settings.EMAIL_FROM_NAME, username))
        msg["To"] = ", ".join(recipients)

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            if self.settings.EMAIL_USE_SSL:
                with smtplib.SMTP_SSL(
                    self.settings.EMAIL_SMTP_HOST,
                    self.settings.EMAIL_SMTP_PORT,
                    timeout=20,
                ) as server:
                    server.login(username, password)
                    server.sendmail(username, recipients, msg.as_string())
            else:
                with smtplib.SMTP(
                    self.settings.EMAIL_SMTP_HOST,
                    self.settings.EMAIL_SMTP_PORT,
                    timeout=20,
                ) as server:
                    server.starttls()
                    server.login(username, password)
                    server.sendmail(username, recipients, msg.as_string())
        except Exception:
            logger.exception("邮件发送失败 subject=%s to=%s", subject, recipients)
            return False

        logger.info("邮件发送成功 subject=%s to=%s", subject, recipients)
        return True

    @staticmethod
    def _normalize_recipients(to_emails: Iterable[str]) -> list[str]:
        recipients = [x.strip() for x in to_emails if x and x.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in recipients:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

