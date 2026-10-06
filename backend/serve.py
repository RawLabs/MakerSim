"""Reserve an available local port before starting the MakerSim server."""
import argparse
import errno
import socket

import uvicorn


def reserve_socket(port: int | None = None) -> socket.socket:
    # Reserve the selected socket for Uvicorn so another process cannot take
    # the port between an availability check and server startup.
    candidates = [port] if port is not None else [*range(8000, 8011), 0]
    for candidate in candidates:
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            connection.bind(('127.0.0.1', candidate))
        except OSError as error:
            connection.close()
            if error.errno == errno.EADDRINUSE and port is None:
                continue
            raise
        return connection
    raise RuntimeError('No local port could be reserved.')


def main():
    parser = argparse.ArgumentParser(description='Start MakerSim on an available local port.')
    parser.add_argument('port', nargs='?', type=int, help='Use this specific port instead of selecting one automatically.')
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('The port must be between 1 and 65535.')
    try:
        connection = reserve_socket(args.port)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:
            parser.error(f'Port {args.port} is already in use. Omit the port to choose an available one automatically.')
        raise
    with connection:
        selected = connection.getsockname()[1]
        print(f'\nOpen MakerSim at http://127.0.0.1:{selected}\nPress Ctrl+C to stop.\n', flush=True)
        server = uvicorn.Server(uvicorn.Config('backend.app:app', host='127.0.0.1', port=selected))
        server.run(sockets=[connection])


if __name__ == '__main__':
    main()
