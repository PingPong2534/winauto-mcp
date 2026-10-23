"""Latest-only local app/site guides. No browser control or secret detection."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit


def identity_key(kind, identity):
    if kind == 'app':
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', identity):
            raise ValueError('App identity must be a lowercase product slug')
        return identity
    if kind != 'web':
        raise ValueError('Unknown guide kind')
    url = urlsplit(identity)
    if (url.scheme not in ('https', 'http') or not url.hostname or url.username
            or url.password or url.query or url.fragment or url.path not in ('', '/')
            or any(c.isspace() for c in identity)):
        raise ValueError('Website identity must be an origin without private URL components')
    host = url.hostname.encode('idna').decode('ascii').lower()
    if ':' in host:
        host = f'[{host}]'
    port = url.port
    suffix = f':{port}' if port and port != {'https': 443, 'http': 80}[url.scheme] else ''
    return f'{url.scheme}://{host}{suffix}'


@contextmanager
def locked(path):
    """OS releases the lock after interruption; no persistent lock history."""
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class GuideStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def directory(self, kind):
        path = self.root / kind
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Linked guide directories are refused')
        return path

    @staticmethod
    def reject_link(path):
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('Linked guide records or lock files are refused')

    def path(self, kind, identity):
        identity = identity_key(kind, identity)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        path = self.directory(kind) / f'{digest}.json'
        self.reject_link(path)
        self.reject_link(path.parent / '.write.lock')
        return path, identity

    def get(self, kind, identity):
        path, _ = self.path(kind, identity)
        if not path.exists():
            return {'revision': 'missing', 'guide': None}
        raw = path.read_bytes()
        return {'revision': hashlib.sha256(raw).hexdigest(), 'guide': json.loads(raw)}

    def put(self, kind, identity, name, body, expected, status, reviewed=False):
        path, canonical = self.path(kind, identity)
        if not reviewed:
            raise ValueError('Review exact guide contents for sensitive data before saving')
        if status not in ('current', 'needs_recheck'):
            raise ValueError('Unsupported verification status')
        if not body.strip() or len(body.encode('utf-8')) > 12288:
            raise ValueError('Guide body must contain 1..12288 UTF-8 bytes')
        if not name.strip() or len(name) > 120 or '\n' in name or '\r' in name:
            raise ValueError('Guide name must be a short single line')
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked(path.parent / '.write.lock'):
            # Reserved helper staging files can remain after a killed writer.
            for abandoned in path.parent.glob('.write-*'):
                if abandoned.is_file() and not abandoned.is_symlink():
                    abandoned.unlink()
            if self.get(kind, canonical)['revision'] != expected:
                raise ValueError('Revision conflict; reread current guide before merging')
            data = {'schema': 1, 'kind': kind, 'identity': canonical, 'name': name,
                    'status': status, 'verified_or_checked_at': datetime.now(timezone.utc).isoformat(),
                    'body': body.strip()}
            encoded = (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
            fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {'path': str(path), 'revision': hashlib.sha256(encoded).hexdigest()}

    def list(self):
        result = []
        for kind in ('app', 'web'):
            for path in sorted(self.directory(kind).glob('*.json')):
                self.reject_link(path)
                data = json.loads(path.read_bytes())
                result.append({k: data[k] for k in ('kind', 'identity', 'name', 'status')})
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('get', 'put', 'list'))
    parser.add_argument('--kind', choices=('app', 'web'))
    parser.add_argument('--identity')
    parser.add_argument('--name')
    parser.add_argument('--expected')
    parser.add_argument('--status', choices=('current', 'needs_recheck'), default='current')
    parser.add_argument('--reviewed', action='store_true')
    args = parser.parse_args()
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        parser.error('LOCALAPPDATA unavailable; guide storage is not configured')
    store = GuideStore(Path(local) / 'winauto-mcp/guides/v1')
    try:
        if args.action == 'list':
            result = store.list()
        else:
            if not args.kind or not args.identity:
                parser.error('--kind and --identity required')
            if args.action == 'get':
                result = store.get(args.kind, args.identity)
            else:
                if not args.name or not args.expected:
                    parser.error('--name and --expected required')
                result = store.put(args.kind, args.identity, args.name, sys.stdin.read(),
                                   args.expected, args.status, args.reviewed)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        print(f'Guide operation refused: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
