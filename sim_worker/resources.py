"""Cleanup each owned resource even when an earlier release or diagnostic fails."""
import ipaddress,socket,time
from pathlib import Path


def release_all(actions):
    errors=[]
    for label,action in actions:
        try:action()
        except BaseException as exc:errors.append(f'{label}: {type(exc).__name__}: {exc}')
    return errors


def start_checked(server,config):
    try:
        if server.start(config) is not True:raise RuntimeError('RTSP server refused to start/bind')
    except BaseException:
        release_all([('partial-server-stop',server.stop),('partial-server-close',server.close)])
        raise
    return server


def attach_checked(writer,product,width,height):
    try:
        writer.attach([product]);writer._start_server(width,height)
    except BaseException:
        release_all([('partial-writer-detach',writer.detach),('partial-writer-stop',writer._stop_server)])
        raise
    return writer


def listening_addresses(port):
    result=[]
    for name,family in (('/proc/net/tcp',socket.AF_INET),('/proc/net/tcp6',socket.AF_INET6)):
        path=Path(name)
        if not path.exists():continue
        for line in path.read_text().splitlines()[1:]:
            fields=line.split();address,raw_port=fields[1].split(':')
            if fields[3]!='0A' or int(raw_port,16)!=port:continue
            raw=bytes.fromhex(address)
            packed=raw[::-1] if family==socket.AF_INET else b''.join(raw[i:i+4][::-1] for i in range(0,16,4))
            result.append(socket.inet_ntop(family,packed))
    return sorted(set(result))


def verify_rtsp_binding(port,*,allow_native_wildcard=False):
    deadline=time.monotonic()+2
    while True:
        addresses=listening_addresses(port)
        if addresses:break
        if time.monotonic()>=deadline:raise RuntimeError('RTSP start returned without an observable listener')
        time.sleep(.01)
    if not allow_native_wildcard and any(not ipaddress.ip_address(address).is_loopback for address in addresses):
        raise RuntimeError(f'Native RTSP binds non-loopback {addresses}; use isolated deployment or explicit synthetic-validation override')
    return addresses
