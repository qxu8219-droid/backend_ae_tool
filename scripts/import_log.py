#!/usr/bin/env python3
"""Offline import/archive inspection. Does not create an IsaacSim session."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.base import AdapterError, MappingError, ParseLimits
from app.services.log_store import LogStore


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, default=Path('outputs/imports'))
    parser.add_argument('--session', default='offline-analysis', help='Archive namespace, not a running robot session')
    parser.add_argument('--max-file-bytes', type=int, default=64*1024*1024)
    parser.add_argument('--max-samples', type=int, default=20000)
    parser.add_argument('--max-points', type=int, default=600000)
    parser.add_argument('--memory-mib', type=int, default=768)
    parser.add_argument('--timeout-s', type=float, default=30.0)
    sub = parser.add_subparsers(dest='command', required=True)
    load = sub.add_parser('import', help='Keep original bytes, normalize and archive')
    load.add_argument('source', type=Path)
    load.add_argument('--mapping', type=Path)
    load.add_argument('--parser', default=None)
    sub.add_parser('list', help='List completed imports in this namespace')
    for name in ('show', 'channels'):
        child = sub.add_parser(name)
        child.add_argument('log_id')
    return parser.parse_args()


async def run(args):
    limits = ParseLimits(max_file_bytes=args.max_file_bytes,max_samples=args.max_samples,max_points=args.max_points,
                         memory_bytes=args.memory_mib*1024*1024,timeout_s=args.timeout_s)
    store = LogStore(args.store, limits)
    if args.command == 'import':
        mapping = None
        if args.mapping:
            with args.mapping.open('rb') as source:
                body = source.read(65537)
            if len(body)>65536: raise MappingError('Mapping JSON exceeds 65536 bytes')
            try:
                mapping = json.loads(body)
            except (ValueError, UnicodeError) as exc:
                raise MappingError('Mapping file must be a JSON object') from exc
            if not isinstance(mapping, dict):
                raise MappingError('An explicitly supplied mapping file must contain a JSON object')
        async def chunks():
            with args.source.open('rb') as source:
                while chunk := await asyncio.to_thread(source.read,65536):
                    yield chunk
        result = await store.import_file(args.session,chunks(),args.source.name,mapping_config=mapping,parser=args.parser)
    elif args.command == 'list':
        result = await store.list_logs(args.session)
    elif args.command == 'channels':
        result = await store.channels(args.session,args.log_id)
    else:
        result = await store.get(args.session,args.log_id)
    return [item.model_dump(mode='json') for item in result] if isinstance(result,list) else result.model_dump(mode='json')


def main():
    try:
        value = asyncio.run(run(arguments()))
    except AdapterError as exc:
        print(json.dumps({'error':{'code':exc.code,'message':exc.message,'status':exc.status_code,
                                  'issues':[issue.model_dump(mode='json') for issue in exc.issues]}},ensure_ascii=False,allow_nan=False),file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(json.dumps({'error':{'code':'IMPORT_INPUT_ERROR','message':str(exc)}},ensure_ascii=False),file=sys.stderr)
        return 1
    print(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
