"""mailer.py against a minimal in-process SMTP server."""

import socket
import socketserver
import threading
from email import message_from_bytes
from email.policy import default as default_policy

import pytest

from domain_cert_monitor.config import Settings
from domain_cert_monitor.mailer import Mailer


class _Handler(socketserver.StreamRequestHandler):
    """Just enough SMTP (RFC 5321) for smtplib: EHLO, MAIL, RCPT, DATA, QUIT."""

    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self) -> None:
        envelope = {"from": None, "to": [], "data": b""}
        self.reply("220 test ESMTP")
        while line := self.rfile.readline():
            cmd = line.decode().strip()
            verb = cmd.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.reply("250 test")
            elif verb == "MAIL":
                envelope["from"] = cmd.split(":", 1)[1].strip().strip("<>")
                self.reply("250 OK")
            elif verb == "RCPT":
                envelope["to"].append(cmd.split(":", 1)[1].strip().strip("<>"))
                self.reply("250 OK")
            elif verb == "DATA":
                self.reply("354 go ahead")
                data = b""
                while (chunk := self.rfile.readline()) != b".\r\n":
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                envelope["data"] = data
                self.server.messages.append(envelope)
                envelope = {"from": None, "to": [], "data": b""}
                self.reply("250 queued")
            elif verb == "RSET":
                self.reply("250 OK")
            elif verb == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("502 not implemented")


@pytest.fixture
def smtp_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.messages = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def smtp_settings(port: int) -> Settings:
    return Settings(
        _env_file=None,
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_security="none",
        smtp_from="monitor@example.com",
        alert_to="a@example.com, b@example.com",
    )


async def test_send_delivers_message(smtp_server):
    mailer = Mailer(smtp_settings(smtp_server.server_address[1]))
    subject = "[domain-cert-monitor] example.com: certificate expires in 7 days"
    await mailer.send(subject, "body text\n.leading dot line\n")
    assert len(smtp_server.messages) == 1
    env = smtp_server.messages[0]
    assert env["from"] == "monitor@example.com"
    assert env["to"] == ["a@example.com", "b@example.com"]
    msg = message_from_bytes(env["data"], policy=default_policy)
    assert msg["Subject"] == subject
    assert msg["From"] == "monitor@example.com"
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Date"] and msg["Message-ID"]
    assert ".leading dot line" in msg.get_content()


async def test_refused_connection_raises():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Nothing listens on the port any more.
    with pytest.raises(OSError):
        await Mailer(smtp_settings(port)).send("subject", "body")
