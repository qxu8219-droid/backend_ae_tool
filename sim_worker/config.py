"""Loopback ZMQ endpoints and the pinned asset/runtime contract."""
from dataclasses import dataclass
from pathlib import Path
import json
import socket
from pydantic import TypeAdapter
from replay_protocol import Identifier


@dataclass(frozen=True)
class WorkerConfig:
    session_id:str
    rpc_port:int=19601
    target_port:int=19602
    event_port:int=19603
    rtsp_port:int=19554
    width:int=1280
    height:int=720
    render_hz:float=20.
    allow_native_rtsp_wildcard:bool=False
    def __post_init__(self):
        TypeAdapter(Identifier).validate_python(self.session_id)
        ports=(self.rpc_port,self.target_port,self.event_port,self.rtsp_port)
        if any(type(x)is not int or not 1024<=x<=65535 for x in ports) or len(set(ports))!=4:raise ValueError('Four distinct unprivileged ports are required')
        if type(self.width)is not int or type(self.height)is not int or not 640<=self.width<=1920 or not 360<=self.height<=1080:raise ValueError('Unsupported capture dimensions')
        if type(self.render_hz) not in (int,float) or not 1<=self.render_hz<=60:raise ValueError('render_hz must be 1..60')
        if type(self.allow_native_rtsp_wildcard)is not bool:raise ValueError('RTSP override must be explicit boolean')
    def endpoint(self,channel):return f'tcp://127.0.0.1:{getattr(self,channel+"_port")}'
    def topic(self,channel):return (channel+':'+self.session_id+'\0').encode()


def load_asset_profile(root:Path):
    profile=json.loads((root/'configs/robot-asset.json').read_text())
    if profile['isaacsim_version']!='6.2.0' or profile['physics_backend'].lower()!='physx':raise ValueError('Only IsaacSim 6.2.0/PhysX is supported')
    profile['asset_path']=(root/profile['asset_directory']).resolve()
    conversion=json.loads((profile['asset_path']/'conversion.json').read_text())
    if conversion['isaac_version'][0]!='6.2.0':raise ValueError('USD conversion version mismatch')
    return profile


def ensure_ports_available(config):
    for name in ('rpc','event','rtsp'):
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                probe.bind(('127.0.0.1',getattr(config,name+'_port')))
        except OSError as exc:
            raise OSError(f'{name} listener port is already occupied: {getattr(config,name+"_port")}') from exc
