"""A small in-process IMAP server standing in for Proton Mail Bridge in tests (loopback, STARTTLS).

It speaks enough IMAP4rev1 for ``imaplib``: CAPABILITY, STARTTLS, LOGIN,
SELECT/EXAMINE, UID SEARCH, UID FETCH, LOGOUT, NOOP. Every command line it
receives is recorded, and write commands (STORE, COPY, MOVE, EXPUNGE, APPEND)
are applied to the in-memory messages, so tests can prove they never happen.
"""

import datetime
import re
import socketserver
import ssl
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class FakeMessage:
    raw: bytes
    flags: Set[str] = field(default_factory=set)


@dataclass
class FakeFolder:
    uidvalidity: int = 1
    messages: Dict[int, FakeMessage] = field(default_factory=dict)

    def add(self, raw: bytes, uid: Optional[int] = None) -> int:
        uid = uid or (max(self.messages, default=0) + 1)
        self.messages[uid] = FakeMessage(raw)
        return uid


@dataclass
class FakeAccount:
    password: str
    folders: Dict[str, FakeFolder] = field(default_factory=lambda: {"INBOX": FakeFolder()})


def make_certificate(directory: Path) -> tuple:
    """Self-signed certificate for 127.0.0.1, like Bridge's."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "bridge-cert.pem", directory / "bridge-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class FakeImapServer:
    def __init__(self, cert_dir: Path, accounts: Dict[str, FakeAccount]):
        self.accounts = accounts
        self.commands: List[str] = []          # every command line received (passwords masked)
        self.cert_path, key_path = make_certificate(cert_dir)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(self.cert_path, key_path)
        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                _Session(server, self).run()

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def verbs(self) -> List[str]:
        """Command names sent, e.g. ['CAPABILITY', 'STARTTLS', 'LOGIN', 'EXAMINE', 'UID SEARCH', ...]."""
        out = []
        for line in self.commands:
            parts = line.split()
            verb = parts[1].upper() if len(parts) > 1 else ""
            out.append(f"UID {parts[2].upper()}" if verb == "UID" and len(parts) > 2 else verb)
        return out


class _Session:
    def __init__(self, server: FakeImapServer, handler):
        self.server, self.handler = server, handler
        self.rfile, self.wfile = handler.rfile, handler.wfile
        self.account: Optional[FakeAccount] = None
        self.folder: Optional[FakeFolder] = None
        self.readonly = True

    def send(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def run(self) -> None:
        self.send("* OK [CAPABILITY IMAP4rev1 STARTTLS] Fake Bridge ready")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode().rstrip("\r\n")
            tag, _, rest = text.partition(" ")
            verb, _, args = rest.partition(" ")
            verb = verb.upper()
            masked = re.sub(r'^(LOGIN \S+) .*$', r'\1 ***', rest, flags=re.I)
            self.server.commands.append(f"{tag} {masked}")
            handler = getattr(self, f"cmd_{verb.lower()}", None)
            if handler is None:
                self.send(f"{tag} BAD unknown command")
                continue
            if handler(tag, args) == "close":
                return

    def cmd_capability(self, tag, args):
        caps = "IMAP4rev1 AUTH=PLAIN" + ("" if isinstance(self.handler.connection, ssl.SSLSocket) else " STARTTLS")
        self.send(f"* CAPABILITY {caps}")
        self.send(f"{tag} OK CAPABILITY completed")

    def cmd_noop(self, tag, args):
        self.send(f"{tag} OK NOOP completed")

    def cmd_starttls(self, tag, args):
        self.send(f"{tag} OK Begin TLS negotiation now")
        tls = self.server.context.wrap_socket(self.handler.connection, server_side=True)
        self.handler.connection = tls
        self.rfile, self.wfile = tls.makefile("rb"), tls.makefile("wb")

    def cmd_login(self, tag, args):
        user, password = [a.strip('"') for a in re.findall(r'"(?:[^"\\]|\\.)*"|\S+', args)][:2]
        account = self.server.accounts.get(user)
        if not account or account.password != password:
            self.send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials")
            return
        self.account = account
        self.send(f"{tag} OK LOGIN completed")

    def _open(self, tag, args, readonly):
        name = args.strip().strip('"')
        folder = self.account.folders.get(name) if self.account else None
        if folder is None:
            self.send(f"{tag} NO no such folder")
            return
        self.folder, self.readonly = folder, readonly
        self.send(f"* {len(folder.messages)} EXISTS")
        self.send(f"* OK [UIDVALIDITY {folder.uidvalidity}] UIDs valid")
        self.send(f"{tag} OK [{'READ-ONLY' if readonly else 'READ-WRITE'}] completed")

    def cmd_examine(self, tag, args):
        self._open(tag, args, readonly=True)

    def cmd_select(self, tag, args):
        self._open(tag, args, readonly=False)

    def cmd_uid(self, tag, args):
        sub, _, rest = args.partition(" ")
        sub = sub.upper()
        uids = sorted(self.folder.messages)
        if sub == "SEARCH":
            match = re.search(r"UID (\d+):\*", rest)
            low = int(match.group(1)) if match else 1
            found = [u for u in uids if u >= low] or uids[-1:]   # like real servers: n:* includes the highest UID
            self.send("* SEARCH " + " ".join(map(str, found)))
            self.send(f"{tag} OK SEARCH completed")
        elif sub == "FETCH":
            uid_text, _, items = rest.partition(" ")
            uid = int(uid_text)
            message = self.folder.messages.get(uid)
            if message is not None:
                if "PEEK" not in items.upper() and not self.readonly:
                    message.flags.add("\\Seen")
                seq = uids.index(uid) + 1
                self.wfile.write(f"* {seq} FETCH (UID {uid} BODY[] {{{len(message.raw)}}}\r\n".encode())
                self.wfile.write(message.raw + b")\r\n")
            self.send(f"{tag} OK FETCH completed")
        elif sub in ("STORE", "COPY", "MOVE"):
            for message in self.folder.messages.values():
                message.flags.add("\\Flagged" if sub == "STORE" else f"\\{sub}")
            self.send(f"{tag} OK {sub} completed")
        else:
            self.send(f"{tag} BAD unsupported")

    def cmd_logout(self, tag, args):
        self.send("* BYE logging out")
        self.send(f"{tag} OK LOGOUT completed")
        return "close"
