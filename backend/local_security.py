"""Validate browser origins and local authorities without requiring CLI headers."""
import ipaddress
from urllib.parse import urlsplit

from starlette.datastructures import Headers


def _authority(value, scheme):
    if not value or any(character.isspace() for character in value):
        raise ValueError('Invalid authority')
    parsed = urlsplit('//' + value)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or value.endswith(':')):
        raise ValueError('Invalid authority')
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    hostname = parsed.hostname.lower()
    try:
        hostname = str(ipaddress.ip_address(hostname))
    except ValueError:
        pass
    return hostname, port if port is not None else (443 if scheme == 'https' else 80)


def request_origin_error(scope):
    headers = Headers(scope=scope)
    scheme = {'ws': 'http', 'wss': 'https'}.get(scope.get('scheme'), scope.get('scheme'))
    try:
        hosts = headers.getlist('host')
        if len(hosts) != 1:
            raise ValueError('Missing or duplicate Host')
        hostname, port = _authority(hosts[0], scheme)
        client = scope.get('client')
        # TestClient's synthetic host is never allowed for a real TCP client.
        test_host = hostname == 'testserver' and client and client[0] == 'testclient'
        if hostname != 'localhost' and not test_host:
            address = ipaddress.ip_address(hostname)
            mapped = address.ipv4_mapped if address.version == 6 else None
            if not address.is_loopback and not (mapped and mapped.is_loopback):
                raise ValueError('Non-local Host')
    except ValueError:
        return 'AI Video Note 仅允许本机访问'

    origins = headers.getlist('origin')
    if origins:
        try:
            if len(origins) != 1:
                raise ValueError('Duplicate Origin')
            origin = urlsplit(origins[0])
            if (origin.scheme not in {'http', 'https'} or origin.scheme != scheme
                    or origin.path or origin.query or origin.fragment
                    or _authority(origin.netloc, origin.scheme) != (hostname, port)):
                raise ValueError('Cross-origin request')
        except ValueError:
            return '不允许跨来源请求'
    if (scope.get('method', 'GET').upper() not in {'GET', 'HEAD', 'OPTIONS'}
            and headers.get('sec-fetch-site', '').lower() in {'cross-site', 'same-site'}):
        return '不允许跨来源请求'
    return None
