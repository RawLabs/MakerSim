"""Readiness checks inside the guest, before its connector is started."""
import http.client
import json
import math
from pathlib import Path
import socket
import time
import urllib.request


def call(path, body=None, cookie='', content_type='application/json', extra=None):
    connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=90)
    headers = {'Cookie': cookie, 'Content-Type': content_type, **(extra or {})}
    connection.request('POST' if body is not None else 'GET', path, body, headers)
    response = connection.getresponse()
    data = response.read()
    status, response_headers = response.status, {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    return status, data, response_headers


def check():
    for _ in range(30):
        try:
            status, data, _ = call('/api/health')
            if status == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    assert json.loads(data)['mode'] == 'hosted'
    data = Path('/opt/makersim/backend/data/backpack-bracket.stl').read_bytes()
    boundary = 'makersim-readiness'
    upload = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="bracket.stl"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
              + data + f'\r\n--{boundary}--\r\n'.encode())
    status, data, headers = call('/api/models', upload, content_type=f'multipart/form-data; boundary={boundary}')
    assert status == 200, (status, data[:200])
    cookie = headers['set-cookie'].split(';')[0]
    assert 'Secure' in headers['set-cookie'] and 'HttpOnly' in headers['set-cookie']
    payload = json.loads(Path('/opt/makersim/readiness-request.json').read_text())
    payload['model_id'] = json.loads(data)['model_id']
    status, _, _ = call('/api/simulate', json.dumps(payload), cookie='')
    assert status == 404, status
    payload['direction'] = [0, 0, -1e308]
    status, data, _ = call('/api/simulate', json.dumps(payload), cookie)
    assert status == 200, (status, data[:200])
    field = json.loads(data)
    assert field['checks']['relative_force_balance'] < 1e-5, field['checks']
    assert math.isfinite(field['max_displacement_mm']) and field['max_displacement_mm'] > 0
    body = json.dumps(payload).replace('-1e+308', 'NaN')
    assert call('/api/simulate', body, cookie)[0] == 422
    assert call('/api/models', b'x', cookie, extra={'Origin':'https://other.example'})[0] == 403
    assert call('/api/health', extra={'Host':'other.example'})[0] == 400
    status, _, headers = call('/api/health', extra={'Origin':'https://makersim.rawcastdigital.com'})
    assert status == 200 and headers['access-control-allow-origin'] == 'https://makersim.rawcastdigital.com'
    # Probe known reachable services. A refusal alone could just mean no listener.
    for address, port in [('192.168.130.1',22),('192.168.1.81',22),('192.168.1.254',80),('75.155.72.250',443)]:
        try:
            with socket.create_connection((address, port), timeout=2):
                raise AssertionError(f'VM can reach protected address {address}:{port}')
        except OSError:
            pass
    with urllib.request.urlopen('https://www.cloudflare.com/cdn-cgi/trace', timeout=20) as response:
        assert response.status == 200
    print('MAKERSIM_VM_CHECKS_PASSED', flush=True)


if __name__ == '__main__':
    check()
