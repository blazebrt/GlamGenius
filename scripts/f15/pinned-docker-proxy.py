"""Task-only Windows Docker bridge: immutable image and environment-only password.

Supabase 2.120.0 embeds its PGPASSWORD environment in the generated bash
command. Strip that single export before Docker receives the command and put
the secret in container Env instead. Never log HTTP bodies, errors or secrets.
The bridge is local, ephemeral, and cannot operate on pre-existing containers.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import socketserver
import sys
import threading
from contextlib import ExitStack
from http.client import HTTPResponse
from urllib.parse import unquote, urlsplit


def pinned_create(body: dict, *, tag: str, image: str, password: str, label: str) -> dict:
    if body.get("Image") not in {tag, image} or not password:
        raise ValueError("DUMP_IMAGE_OR_CREDENTIAL_REJECTED")
    cmd = (body.get("Entrypoint") or []) + (body.get("Cmd") or [])
    if not isinstance(cmd, list) or len(cmd) not in {3,4} or cmd[0] not in {"bash", "/bin/bash", "sh", "/bin/sh"} or cmd[1] != '-c' or (len(cmd)==4 and cmd[3]!='--'):
        raise ValueError("DUMP_COMMAND_REJECTED")
    script, removed = re.subn(r'^export PGPASSWORD=.*(?:\n|$)', ': "${PGPASSWORD:?}"\n', cmd[2], flags=re.M)
    if removed != 1 or password in script:
        raise ValueError("SECRET_COMMAND_REJECTED")
    body = dict(body)
    body["Image"] = image
    body["Entrypoint"] = ["/bin/bash"]
    body["Cmd"] = ["-c", script]
    env = [v for v in body.get("Env", []) if not v.startswith(("PGPASSWORD=", "PGSSLMODE=", "PGSSLROOTCERT=", "PGOPTIONS="))]
    body["Env"] = env + ["PGPASSWORD=" + password, "PGSSLMODE=verify-full",
                          "PGSSLROOTCERT=/etc/f15/supabase-prod-ca-2021.crt",
                          "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=120000"]
    body["Labels"] = dict(body.get("Labels") or {}, **{"glamgenius.f15.run": label})
    # Reversible password spellings must not remain in any command argument.
    from urllib.parse import quote
    if any(secret in json.dumps(body["Cmd"]) for secret in (password, quote(password, safe=""))):
        raise ValueError("SECRET_COMMAND_REJECTED")
    return body


class BufferSocket:
    def __init__(self, data: bytes):
        self.data = io.BytesIO(data)

    def makefile(self, *args):
        return self.data


class State:
    def __init__(self, pipe: str, tag: str, image: str, label: str):
        self.pipe, self.tag, self.image, self.label = pipe, tag, image, label
        self.password = ""
        self.owned: set[str] = set()
        self.lock = threading.Lock()

    def authorize(self, method: str, path: str) -> bool:
        path = re.sub(r'^/v[0-9.]+', '', unquote(urlsplit(path).path))
        if path in {"/_ping", "/version", "/info"} and method in {"GET", "HEAD"}:
            return True
        if method == "GET" and path.startswith("/images/") and path.endswith("/json"):
            return path[8:-5] in {self.tag, self.image}
        if method == "POST" and path == "/containers/create":
            return True
        match = re.fullmatch(r'/containers/([a-f0-9]{12,64})(?:/(json|attach|start|wait|logs|kill))?', path)
        return bool(match and match[1] in self.owned)


class Bridge(socketserver.StreamRequestHandler):
    def handle(self):
        pipe = None
        resources = ExitStack()
        state = self.server.state
        try:
            line = self.rfile.readline(8192).decode('ascii').strip()
            method, path, version = line.split(' ')
            headers = {}
            while True:
                line = self.rfile.readline(8192)
                if line in (b'\r\n', b'\n', b''):
                    break
                key, value = line.decode('ascii').split(':', 1)
                headers[key.lower()] = value.strip()
            size = int(headers.get('content-length', '0'))
            if (headers.get('x-f15-run') != state.label or not 0 <= size <= 4 * 1024 * 1024
                    or 'transfer-encoding' in headers or not state.authorize(method, path)):
                raise ValueError('DOCKER_REQUEST_REJECTED')
            body = self.rfile.read(size)
            creating = method == 'POST' and urlsplit(path).path.endswith('/containers/create')
            if creating:
                body = json.dumps(pinned_create(json.loads(body), tag=state.tag, image=state.image,
                                               password=state.password, label=state.label)).encode()
            upgrade = headers.get('upgrade', '').lower() == 'tcp'
            headers['connection'] = 'Upgrade' if upgrade else 'close'
            headers['content-length'] = str(len(body))
            request = f'{method} {path} {version}\r\n'.encode() + b''.join(
                f'{k}: {v}\r\n'.encode() for k, v in headers.items()) + b'\r\n' + body
            # Windows named-pipe file I/O; not a child command, shell or argv.
            pipe = resources.enter_context(io.FileIO(state.pipe, 'r+'))
            pipe.write(request)
            if upgrade:
                def input_stream():
                    try:
                        while chunk := self.connection.recv(65536):
                            pipe.write(chunk)
                    except (OSError, ValueError):
                        pass
                threading.Thread(target=input_stream, daemon=True).start()
            chunks = [] if creating else None
            while True:
                try:
                    chunk = pipe.read(65536)
                except OSError:
                    break
                if not chunk:
                    break
                if creating:
                    chunks.append(chunk)
                else:
                    self.connection.sendall(chunk)
            if creating:
                raw = b''.join(chunks)
                response = HTTPResponse(BufferSocket(raw))
                response.begin()
                data = response.read()
                if response.status == 201:
                    identity = json.loads(data)['Id']
                    if not re.fullmatch(r'[a-f0-9]{64}', identity):
                        raise ValueError('CONTAINER_ID_REJECTED')
                    with state.lock:
                        state.owned.add(identity)
                self.connection.sendall(raw)
        except Exception as error:
            known = {'DUMP_IMAGE_OR_CREDENTIAL_REJECTED','DUMP_COMMAND_REJECTED','SECRET_COMMAND_REJECTED','DOCKER_REQUEST_REJECTED','CONTAINER_ID_REJECTED'}
            code = str(error) if str(error) in known else type(error).__name__
            sys.stderr.write('F15_PROXY_REQUEST_REJECTED_' + code + '\n')
            sys.stderr.flush()
            try:
                body = b'{"message":"F15_DOCKER_FAIL_CLOSED"}'
                self.connection.sendall(f'HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body)
            except OSError:
                pass
        finally:
            resources.close()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipe', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    if args.pipe != r'\\.\pipe\dockerDesktopLinuxEngine' or not re.fullmatch(r'sha256:[a-f0-9]{64}', args.image):
        raise SystemExit('LOCAL_DOCKER_AUTHORITY_REQUIRED')
    from uuid import uuid4
    state = State(args.pipe, args.tag, args.image, uuid4().hex)
    with Server(('127.0.0.1', 0), Bridge) as server:
        server.state = state
        sys.stdout.write(json.dumps({'port': server.server_address[1], 'token': state.label})+'\n')
        sys.stdout.flush()
        def credential_input():
            try:
                line = sys.stdin.readline(8192)
                state.password = json.loads(line)['password']
                line = ''
                sys.stdout.write('CREDENTIAL_CHANNEL_READY\n')
                sys.stdout.flush()
                sys.stdin.read()
            finally:
                state.password = ''
                server.shutdown()
        threading.Thread(target=credential_input, daemon=True).start()
        server.serve_forever(poll_interval=0.1)


if __name__ == '__main__':
    main()
