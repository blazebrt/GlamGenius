"""Task-only Windows Docker bridge: immutable image and environment-only password.

Accept only byte-for-byte Supabase 2.120.0 roles/schema/COPY-data templates.
Build Docker commands from trusted templates, never from incoming shell text.
The password is supplied through Env only. Never log bodies or secrets.
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
from pathlib import Path
from urllib.parse import unquote, urlsplit


TEMPLATES = Path(__file__).with_name('cli-2.120.0-templates')
TARGET_KEYS = ('PGHOST', 'PGPORT', 'PGUSER', 'PGDATABASE')


def approved_modes() -> dict:
    return json.loads((TEMPLATES / 'modes.json').read_text(encoding='utf-8'))


def canonical_script(mode: str) -> str:
    script = (TEMPLATES / (mode + '.sh')).read_text(encoding='utf-8')
    export = 'export PGPASSWORD="$PGPASSWORD"\n'
    if script.count(export) != 1:
        raise ValueError('DUMP_COMMAND_REJECTED')
    return script.replace(export, ': "${PGPASSWORD:?}"\n')


def classify_request(body: dict, *, password: str, target: dict) -> str:
    if (body.get('Entrypoint') not in (None, []) or not isinstance(body.get('Cmd'), list)
            or len(body['Cmd']) != 4 or body['Cmd'][:2] != ['bash', '-c'] or body['Cmd'][3] != '--'):
        raise ValueError('DUMP_COMMAND_REJECTED')
    mode = next((m for m in approved_modes() if body['Cmd'][2] ==
                 (TEMPLATES / (m + '.sh')).read_text(encoding='utf-8')), None)
    if mode is None:
        raise ValueError('DUMP_COMMAND_REJECTED')
    expected = dict(approved_modes()[mode], **target, PGPASSWORD=password)
    env = body.get('Env')
    if (not isinstance(env, list) or any(not isinstance(v, str) or '=' not in v for v in env)
            or len(env) != len(expected) or set(env) != {k+'='+v for k, v in expected.items()}):
        raise ValueError('DUMP_ENVIRONMENT_REJECTED')
    return mode


def pinned_create(body: dict, *, tag: str, image: str, password: str, label: str, target: dict) -> dict:
    if body.get("Image") not in {tag, image} or not password:
        raise ValueError("DUMP_IMAGE_OR_CREDENTIAL_REJECTED")
    mode = classify_request(body, password=password, target=target)
    env = dict(approved_modes()[mode], **target, PGPASSWORD=password, PGSSLMODE='verify-full',
               PGSSLROOTCERT='/etc/f15/supabase-prod-ca-2021.crt',
               PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=120000')
    return {'Image': image, 'Entrypoint': ['/bin/bash'], 'Cmd': ['-c', canonical_script(mode)],
            'Env': [k+'='+v for k, v in env.items()], 'Labels': {'glamgenius.f15.run': label},
            'AttachStdout': True, 'AttachStderr': True, 'Tty': False,
            'HostConfig': {'NetworkMode': 'host', 'AutoRemove': True, 'RestartPolicy': {'Name': 'no'}}}


class BufferSocket:
    def __init__(self, data: bytes):
        self.data = io.BytesIO(data)

    def makefile(self, *args):
        return self.data


class State:
    def __init__(self, pipe: str, tag: str, image: str, label: str, target: dict):
        self.pipe, self.tag, self.image, self.label = pipe, tag, image, label
        self.password = ""
        self.owned: set[str] = set()
        self.lock = threading.Lock()
        self.target = target

    def inspect_created(self, identity: str, expected: dict, mode: str) -> None:
        # Only an ID returned by this run's successful create is inspected.
        with io.FileIO(self.pipe, 'r+') as pipe:
            pipe.write(f'GET /containers/{identity}/json HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'.encode())
            chunks = []
            while True:
                try:
                    chunk = pipe.read(65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        response = HTTPResponse(BufferSocket(b''.join(chunks)))
        response.begin()
        if response.status != 200:
            raise ValueError('CREATED_CONTAINER_AUTHORITY_REJECTED')
        actual = json.loads(response.read())
        config = actual['Config']
        if (actual['Image'] != self.image or config['Cmd'] != expected['Cmd']
                or config['Entrypoint'] != expected['Entrypoint']
                or config['Labels'].get('glamgenius.f15.run') != self.label
                or any(v not in config['Env'] for v in expected['Env'])
                or config['Env'].count('PGPASSWORD='+self.password) != 1
                or any(self.password in argument for argument in config['Cmd'])):
            raise ValueError('CREATED_CONTAINER_AUTHORITY_REJECTED')
        # No password or command text is emitted; exact trusted-command equality
        # establishes independence from password-derived shell escaping.
        with self.lock:
            sys.stdout.write(json.dumps({'mode': mode, 'canonical_dump_command_verified': True,
                                         'credential_present_in_container_cmd': False,
                                         'password_environment_only': True, 'immutable_image_verified': True})+'\n')
            sys.stdout.flush()

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
                incoming = json.loads(body)
                mode = classify_request(incoming, password=state.password, target=state.target)
                expected = pinned_create(incoming, tag=state.tag, image=state.image,
                                         password=state.password, label=state.label, target=state.target)
                body = json.dumps(expected).encode()
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
                    state.inspect_created(identity, expected, mode)
                self.connection.sendall(raw)
        except Exception as error:
            known = {'DUMP_IMAGE_OR_CREDENTIAL_REJECTED','DUMP_COMMAND_REJECTED','DUMP_ENVIRONMENT_REJECTED','DOCKER_REQUEST_REJECTED','CONTAINER_ID_REJECTED','CREATED_CONTAINER_AUTHORITY_REJECTED'}
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
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--user', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    if args.pipe != r'\\.\pipe\dockerDesktopLinuxEngine' or not re.fullmatch(r'sha256:[a-f0-9]{64}', args.image):
        raise SystemExit('LOCAL_DOCKER_AUTHORITY_REQUIRED')
    from uuid import uuid4
    if (not 1 <= args.port <= 65535 or not re.fullmatch(r'[a-zA-Z0-9.:-]+', args.host)
            or not re.fullmatch(r'[a-zA-Z0-9_.-]+', args.user) or not re.fullmatch(r'[a-zA-Z0-9_-]+', args.database)):
        raise SystemExit('DUMP_TARGET_AUTHORITY_REQUIRED')
    state = State(args.pipe, args.tag, args.image, uuid4().hex,
                  dict(PGHOST=args.host, PGPORT=str(args.port), PGUSER=args.user, PGDATABASE=args.database))
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
