#!/usr/bin/env python3
"""Deterministically export REST plus independent WS/IPC/media JSON Schemas."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def documents():
    from app.main import app
    from app.schemas import REST_MODELS, WS_CLIENT_ADAPTER, WS_SERVER_ADAPTER
    from app.schemas.examples import examples as service_examples
    from replay_protocol import (RPC_ADAPTER, RPC_RESPONSE_ADAPTER, WORKER_EVENT_ADAPTER,
        TARGET_ADAPTER, SnapshotCatalog, PresentedFrame, ReplayData, PROTOCOL_MODELS, PROTOCOL_ADAPTERS)
    from replay_protocol.examples import examples as protocol_examples
    from pydantic import TypeAdapter

    adapters={
        'ws-server':WS_SERVER_ADAPTER,'ws-client':WS_CLIENT_ADAPTER,
        'ipc-request':RPC_ADAPTER,'ipc-response':RPC_RESPONSE_ADAPTER,
        'worker-event':WORKER_EVENT_ADAPTER,'target':TARGET_ADAPTER,
        'frame-catalog':TypeAdapter(SnapshotCatalog),'presented-frame':TypeAdapter(PresentedFrame),
        'replay-data':TypeAdapter(ReplayData),
    }
    result={'openapi.json':app.openapi()}
    for name,adapter in adapters.items():
        result[name+'.schema.json']={
            '$schema':'https://json-schema.org/draft/2020-12/schema',
            '$id':f'https://model-evaluation.local/contracts/1/{name}.schema.json',
            **adapter.json_schema(mode='validation')}
    validators={**adapters,**PROTOCOL_ADAPTERS,
        **{name:TypeAdapter(model) for name,model in {**PROTOCOL_MODELS,**REST_MODELS}.items()}}
    samples={}
    for prefix,collection in [('protocol',protocol_examples()),('service',service_examples())]:
        for name,example in collection.items():
            key=f'{prefix}/{name}'
            # Export only examples accepted by the exact same runtime validator.
            parsed=validators[example['schema']].validate_python(example['payload'])
            samples[key]={'schema':example['schema'],'payload':parsed.model_dump(mode='json')}
    result['examples.json']=samples
    result['manifest.json']={'schema_version':1,'purpose':'versioned API and wire contracts; runtime is configured explicitly',
        'rest':'openapi.json','wire':{name:name+'.schema.json' for name in adapters},
        'examples':'examples.json'}
    return {name:json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
            for name,value in result.items()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'contracts')
    parser.add_argument('--check',action='store_true',help='Fail on stale artifacts without modifying files')
    args=parser.parse_args()
    stale=[]
    for name,content in documents().items():
        path=args.output_dir/name
        if args.check:
            if not path.exists() or path.read_text()!=content:stale.append(name)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(content)
    if stale:
        print('Outdated contract artifacts: '+', '.join(stale))
        return 1
    print('Contracts verified' if args.check else 'Contracts exported to '+str(args.output_dir))
    return 0


if __name__=='__main__':raise SystemExit(main())
