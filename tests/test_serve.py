import errno
from unittest.mock import MagicMock

import pytest

from backend import serve


def test_occupied_default_port_uses_next_and_hands_off_reserved_socket(monkeypatch):
    occupied = MagicMock()
    occupied.bind.side_effect = OSError(errno.EADDRINUSE, 'Address already in use')
    available = MagicMock()
    available.getsockname.return_value = ('127.0.0.1', 8001)
    available.__enter__.return_value = available
    connections = iter([occupied, available])
    monkeypatch.setattr(serve.socket, 'socket', lambda *args: next(connections))
    server = MagicMock()
    monkeypatch.setattr(serve.uvicorn, 'Server', lambda config: server)
    monkeypatch.setattr('sys.argv', ['makersim'])
    serve.main()
    occupied.close.assert_called_once()
    available.bind.assert_called_once_with(('127.0.0.1', 8001))
    server.run.assert_called_once_with(sockets=[available])


def test_occupied_explicit_port_reports_error(monkeypatch):
    connection = MagicMock()
    connection.bind.side_effect = OSError(errno.EADDRINUSE, 'Address already in use')
    monkeypatch.setattr(serve.socket, 'socket', lambda *args: connection)
    with pytest.raises(OSError) as raised:
        serve.reserve_socket(9000)
    assert raised.value.errno == errno.EADDRINUSE
    connection.bind.assert_called_once_with(('127.0.0.1', 9000))
    connection.close.assert_called_once()


def test_permission_errors_are_not_treated_as_occupied_ports(monkeypatch):
    connection = MagicMock()
    connection.bind.side_effect = OSError(errno.EPERM, 'Operation not permitted')
    monkeypatch.setattr(serve.socket, 'socket', lambda *args: connection)
    with pytest.raises(OSError) as raised:
        serve.reserve_socket()
    assert raised.value.errno == errno.EPERM
    connection.bind.assert_called_once()
