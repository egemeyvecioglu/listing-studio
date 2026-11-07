"""Local-only HTTP(S) app. No Etsy write happens outside an approved batch."""
import argparse
import base64
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
import threading
import urllib.parse
import webbrowser

from engine import ReviewError, fingerprint
from service import Service, TaskBusy
from storage import Store, data_directory

WEB = Path(__file__).parent / "web"


def make_certificate(directory):
    from datetime import datetime, timedelta
    import ipaddress
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    cert_path, key_path = directory / "localhost.crt", directory / "localhost.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Etsy Listing Studio localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.utcnow() - timedelta(minutes=5)).not_valid_after(datetime.utcnow() + timedelta(days=365)).add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False).add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_for(service, port, secure=False):
    sessions = set()
    csrf = secrets.token_urlsafe(32)
    origin = ("https" if secure else "http") + "://localhost:" + str(port)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # OAuth query strings and setup requests must never enter logs.
            pass

        def send(self, status, body, content_type="application/json", cookie=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/json" else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.etsystatic.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", "studio_session=" + cookie + "; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else ""))
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            return self.headers.get("Host") == "localhost:" + str(port)

        def authorized(self):
            try:
                jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
                return jar.get("studio_session") and jar["studio_session"].value in sessions
            except cookies.CookieError:
                return False

        def do_GET(self):
            if not self.trusted_host():
                return self.send(403, {"error": "Open this app using its localhost address."})
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/":
                session = secrets.token_urlsafe(32)
                sessions.add(session)
                return self.send(200, (WEB / "index.html").read_bytes(), "text/html", session)
            if not self.authorized():
                return self.send(401, {"error": "Open the application again to continue."})
            try:
                if path == "/oauth/callback":
                    service.begin_task("Connecting your Etsy shop…")
                    try:
                        service.auth_finish(urllib.parse.parse_qs(parsed.query))
                    finally:
                        service.end_task()
                    return self.send(200, "<!doctype html><html lang=en><meta charset=utf-8><title>Shop connected</title><link rel=stylesheet href=/style.css><main class=callback><h1>Your shop is connected.</h1><p>You can return to the studio. Your products will sync automatically.</p><a class=button href=/>Open Listing Studio</a></main></html>", "text/html")
                if path == "/api/state":
                    return self.send(200, {**service.state(), "csrf": csrf})
                if path == "/api/status":
                    return self.send(200, service.status)
                if path == "/api/image-file":
                    key = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
                    raw, content_type = service.images.file(key)
                    return self.send(200, raw, content_type)
                if path == "/api/batch":
                    batch = service.batch(urllib.parse.parse_qs(parsed.query).get("id", [""])[0])
                    return self.send(200, {"batch": batch, "approval": fingerprint(batch)})
                target = (WEB / path.lstrip("/")).resolve()
                if not target.is_relative_to(WEB.resolve()) or not target.is_file():
                    return self.send(404, {"error": "This page could not be found."})
                ext = target.suffix
                if ext not in (".js", ".css", ".json", ".svg"):
                    return self.send(404, {"error": "This file is not public."})
                return self.send(200, target.read_bytes(), {".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml"}[ext])
            except TaskBusy as exc:
                return self.send(409, {"error": str(exc), "code": "TASK_BUSY", "task": exc.task})
            except (ReviewError, ValueError, TypeError, KeyError) as exc:
                if path == "/oauth/callback":
                    # Static error text avoids reflecting query content as HTML.
                    return self.send(400, "<!doctype html><html lang=en><meta charset=utf-8><title>Connection incomplete</title><link rel=stylesheet href=/style.css><main class=callback><h1>The shop connection did not finish.</h1><p>Return to the studio and try Connect with Etsy again. Check the registered callback and approved app access if this repeats.</p><a class=button href=/>Return to studio</a></main></html>", "text/html")
                return self.send(400, {"error": str(exc)})
            except Exception:
                return self.send(500, {"error": "The request could not be completed. Your saved work is preserved."})

        def do_POST(self):
            if not self.trusted_host() or not self.authorized() or self.headers.get("Origin") != origin or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), csrf):
                return self.send(403, {"error": "Open the app again before making changes."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                upload = urllib.parse.urlsplit(self.path).path == "/api/image-upload"
                limit = 28 * 1024 * 1024 if upload else 2 * 1024 * 1024
                if not 0 < length <= limit or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.send(400, {"error": "This request could not be read."})
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ReviewError("This request could not be read.")
                path = urllib.parse.urlsplit(self.path).path
                if path == "/api/image-gallery":
                    service.begin_task("Loading listing images from Etsy…")
                    try:
                        result = {"galleries": service.images.gallery(str(data["shop_id"]), data["listing_ids"])}
                        service.touch()
                    finally:
                        service.end_task()
                elif path == "/api/image-upload":
                    try:
                        raw = base64.b64decode(data["content"], validate=True)
                    except (ValueError, TypeError):
                        raise ReviewError("This image could not be read. Choose the file again.")
                    result = service.images.upload(str(data["shop_id"]), str(data["name"]), raw)
                    service.touch()
                elif path == "/api/image-review":
                    with service.store.lock:
                        batch = service.images.review(data)
                        result = {"batch": batch, "approval": fingerprint(batch)}
                elif path == "/api/sample":
                    service.sample()
                    result = {"ok": True}
                elif path == "/api/setup":
                    result = service.save_credentials(data)
                elif path == "/api/connect":
                    result = service.auth_start(data.get("connection_id", ""))
                elif path == "/api/sync":
                    service.start_sync(str(data["shop_id"]) if data.get("shop_id") else None)
                    result = {"ok": True}
                elif path == "/api/preview":
                    # Local calculation only. Sync replaces its cache atomically;
                    # keep a consistent snapshot without blocking on network work.
                    with service.store.lock:
                        batch = service.build_review(data)
                        result = {"batch": batch, "approval": fingerprint(batch)}
                elif path == "/api/apply":
                    service.apply(str(data["id"]), data.get("approval"), data.get("dependency_approvals"))
                    result = {"ok": True}
                elif path == "/api/restore":
                    batch = service.restore_review(str(data["id"]))
                    result = {"batch": batch, "approval": fingerprint(batch)}
                elif path == "/api/discard":
                    if service.status["busy"]:
                        raise ReviewError("Wait for the current task to finish.")
                    batch = service.batch(str(data["id"]))
                    batch["status"] = "discarded"
                    service.store.put("batch", batch["id"], batch)
                    service.touch()
                    result = {"ok": True}
                elif path == "/api/preset":
                    result = service.preset(data)
                elif path == "/api/delete-preset":
                    value = service.store.get("preset", str(data["id"]))
                    if not value:
                        raise ReviewError("This saved shortcut could not be found.")
                    service.store.put("deleted_preset", str(data["id"]), value)
                    service.store.delete("preset", str(data["id"]))
                    service.touch()
                    result = {"ok": True}
                elif path == "/api/match":
                    shop_id = str(data["shop_id"])
                    service.shop(shop_id)
                    name = str(data.get("name", "")).strip()
                    values = data.get("values")
                    if not name or not isinstance(values, dict):
                        raise ReviewError("Name this group and choose its matching variations.")
                    value = {"id": str(data.get("id") or secrets.token_hex(8)), "shop_id": shop_id, "name": name[:80], "values": values}
                    service.store.put("match", value["id"], value)
                    service.touch()
                    result = value
                elif path == "/api/settings":
                    minutes = int(data["sync_minutes"])
                    if not 5 <= minutes <= 1440:
                        raise ReviewError("Choose a sync interval between 5 minutes and one day.")
                    service.store.put("setting", "sync_minutes", minutes)
                    service.touch()
                    result = {"ok": True}
                else:
                    return self.send(404, {"error": "This action could not be found."})
                return self.send(200, result)
            except TaskBusy as exc:
                return self.send(409, {"error": str(exc), "code": "TASK_BUSY", "task": exc.task})
            except (ReviewError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                return self.send(400, {"error": str(exc)})
            except Exception:
                return self.send(500, {"error": "The request could not be completed. Your saved work is preserved."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--https", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--make-certificate", action="store_true")
    args = parser.parse_args()
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    if args.make_certificate:
        cert, _ = make_certificate(directory)
        print(cert)
        return
    url = ("https" if args.https else "http") + "://localhost:" + str(args.port)
    service = Service(Store(directory), url + "/oauth/callback", live_enabled=args.https and (os.name == "nt" or sys.platform == "darwin"))
    server = server_for(service, args.port, args.https)
    if args.https:
        cert, key = make_certificate(directory)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=service.scheduler, daemon=True).start()
    print("Listing Studio is ready: " + url, flush=True)
    print("Keep this window open while the studio is running. Press Ctrl+C to close.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
