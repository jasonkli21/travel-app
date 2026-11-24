"""Fixture-only loopback processes and network scope."""

import http.client
import json
import os
import ssl
import sys
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from socketserver import TCPServer
from threading import Thread


def restrict_network():
    blocked = []

    def guard(event, values):
        if event == "socket.getaddrinfo" and values[0] == "127.0.0.1":
            return
        if event in {"socket.bind", "socket.connect"}:
            address = values[1]
            if isinstance(address, tuple) and address[0] == "127.0.0.1":
                return
            if isinstance(address, str) and address.startswith("/tmp/step4-travel-db."):
                return
        if event in {
            "socket.bind", "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
            "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg",
        }:
            blocked.append(event)
            print(f"FIXTURE_NETWORK_EGRESS_BLOCKED {event}", file=sys.stderr, flush=True)
            raise PermissionError("external network forbidden by synthetic fixture")

    sys.addaudithook(guard)
    return blocked


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def exchange(port, verb, path, *, body=b"", headers=None, ca=None):
    if ca:
        connection = http.client.HTTPSConnection(
            "127.0.0.1", port, timeout=12, context=ssl.create_default_context(cafile=ca),
        )
    else:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
    with closing(connection):
        connection.request(verb, path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


def rpc(port, operation, **args):
    status, _, body = exchange(
        port, "POST", "/_fixture/" + operation, body=encode(args),
        headers={"content-type": "application/json"},
    )
    assert status == 200, (operation, status, body)
    return json.loads(body)


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address


def make_server(port, handler, *, cert=None, key=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = self.rfile.read(int(self.headers.get("content-length", "0")))
            self.respond(request)

        def do_GET(self):
            self.respond(b"")

        def respond(self, request):
            status, headers, body = handler(self.command, self.path, dict(self.headers), request)
            if status is None:
                self.close_connection = True
                return
            self.send_response(status)
            for name, value in headers.items():
                if name.lower() not in {"content-length", "connection", "server", "date"}:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = LocalServer(("127.0.0.1", port), Handler)
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


@contextmanager
def threaded(server):
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        worker.join(3)
        server.server_close()


def cert_pair(path, label):
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    signing = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, label)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(signing.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), False)
        .sign(signing, hashes.SHA256())
    )
    cert = path / (label + ".pem")
    key = path / (label + ".key")
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(signing.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    key.chmod(0o600)
    return str(cert), str(key)


def receiver():
    from hashlib import sha256
    from uuid import uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    denied = restrict_network()
    key = Ed25519PrivateKey.generate()
    arrivals = []
    outcome = "completed"

    def handle(method, path, headers, body):
        nonlocal outcome
        if path == "/_fixture/network-audit":
            return 200, {}, encode(denied)
        if path == "/_fixture/arrivals":
            return 200, {}, encode(arrivals)
        if path == "/_fixture/outcome":
            outcome = json.loads(body)["mode"]
            return 200, {}, b"null"
        assert (method, path) == ("POST", "/dispatch")
        headers = {k.lower(): v for k, v in headers.items()}
        digest = sha256(body).hexdigest()
        assert headers["x-pas-payload-sha256"] == digest
        arrivals.append({
            "attempt": headers["x-pas-attempt"], "sha256": digest,
            "body": body.decode(), "bytes": len(body), "method": method, "path": path,
        })
        if outcome == "disconnect":
            return None, {}, b""
        terminal = encode({
            "attempt": str(uuid4()) if outcome == "wrong-attempt" else headers["x-pas-attempt"],
            "payload_sha256": digest, "result": "Paired synthetic itinerary response.", "usage": 0,
        })
        return 200, {"x-fixture-signature": key.sign(terminal).hex()}, terminal

    server = make_server(0, handle)
    print(encode({
        "port": server.server_port, "pid": os.getpid(),
        "key": key.public_key().public_bytes_raw().hex(),
    }).decode(), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    assert sys.argv[1:] == ["receiver"]
    receiver()
