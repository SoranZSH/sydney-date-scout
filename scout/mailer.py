"""SMTP 发信（支持可选代理）。

为什么需要代理支持：国内网络到境外邮件服务器（Gmail 等）的 SMTP 端口
587/465 常被限制，表现为 TimeoutError / WinError 10060。而浏览器能打开
Gmail 不代表这里能连上 —— Python 的 smtplib 走原始 socket，不读
HTTP_PROXY 环境变量。配置 email.proxy 后，发信会走本地代理隧道。
"""
from __future__ import annotations

import contextlib
import logging
import smtplib
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterator, Optional

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _proxied_socket(proxy: Optional[dict]) -> Iterator[None]:
    """在 with 块内把全局 socket 换成走代理的版本，退出时还原。

    smtplib 内部用 socket.create_connection()，而后者会调用 socket.socket，
    所以替换 socket.socket 是让 smtplib 走代理的标准做法。
    """
    if not proxy:
        yield
        return

    try:
        import socks                                    # PySocks
    except ImportError as ex:
        raise RuntimeError(
            "配置了 email.proxy 但未安装 PySocks，请先运行：pip install PySocks"
        ) from ex

    kinds = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    kind = str(proxy.get("type", "socks5")).lower()
    if kind not in kinds:
        raise ValueError(f"email.proxy.type 只支持 {sorted(kinds)}，收到 {kind!r}")

    original = socket.socket
    socks.set_default_proxy(kinds[kind], proxy["host"], int(proxy["port"]))
    socket.socket = socks.socksocket
    log.info("发信走代理 %s://%s:%s", kind, proxy["host"], proxy["port"])
    try:
        yield
    finally:
        socket.socket = original
        socks.set_default_proxy()                       # 清掉全局默认，避免影响后续请求


def send_digest(cfg: dict, subject: str, html_body: str) -> None:
    email_cfg = cfg["email"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{email_cfg.get('from_name', '')} <{email_cfg['from_addr']}>"
    msg["To"] = ", ".join(email_cfg["to"])
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host = email_cfg["smtp_host"]
    port = int(email_cfg["smtp_port"])
    timeout = int(email_cfg.get("timeout_seconds", 30))

    with _proxied_socket(email_cfg.get("proxy")):
        server = (smtplib.SMTP_SSL(host, port, timeout=timeout) if port == 465
                  else smtplib.SMTP(host, port, timeout=timeout))
        try:
            if port != 465:
                server.starttls()
            server.login(email_cfg["smtp_user"], email_cfg["smtp_password"])
            server.sendmail(email_cfg["from_addr"], email_cfg["to"], msg.as_string())
            log.info("邮件已发送至 %s", email_cfg["to"])
        finally:
            with contextlib.suppress(Exception):
                server.quit()
