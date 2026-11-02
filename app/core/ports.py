"""Reserve local ports until the process/socket that owns them is ready to bind."""
import socket


class PortLease:
    def __init__(self, sockets):
        self.ports = [sock.getsockname()[1] for sock in sockets]
        self._sockets = list(sockets)

    def release(self, index):
        sock = self._sockets[index]
        if sock is not None:
            sock.close()
            self._sockets[index] = None

    def close(self):
        for index in range(len(self._sockets)):
            self.release(index)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class PortPool:
    def __init__(self, first=6100, last=6199):
        if type(first) is not int or type(last) is not int or not 1024 <= first <= last <= 65535:
            raise ValueError('Port range must be unprivileged and ordered')
        self.first, self.last = first, last

    def lease(self, kinds):
        if not kinds or any(kind not in ('tcp', 'udp') for kind in kinds):
            raise ValueError('Expected one or more tcp/udp ports')
        held = []
        chosen = set()
        try:
            for kind in kinds:
                for port in range(self.first, self.last + 1):
                    if port in chosen:
                        continue
                    sock = socket.socket(type=socket.SOCK_STREAM if kind == 'tcp' else socket.SOCK_DGRAM)
                    try:
                        if kind == 'tcp':
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind(('127.0.0.1', port))
                        if kind == 'tcp':
                            sock.listen(1)
                    except OSError:
                        sock.close()
                        continue
                    held.append(sock)
                    chosen.add(port)
                    break
                else:
                    raise OSError('No free ports in the configured session range')
            return PortLease(held)
        except BaseException:
            for sock in held:
                sock.close()
            raise
