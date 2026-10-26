#!/usr/bin/env python3
"""Generate/check portable metadata from the selected common file, without SDK."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analysis.metadata import CACHE, metadata_from_common


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, default=CACHE)
    args = parser.parse_args()
    config = json.loads((ROOT / 'configs/robot-asset.json').read_text())
    source_ref = (Path(config['asset_directory']) / config['common_config']).as_posix()
    artifact = metadata_from_common(ROOT / source_ref, source_ref=source_ref)
    if args.check:
        if not args.output.exists() or json.loads(args.output.read_text()) != artifact.payload():
            raise SystemExit('Joint metadata differs from the selected common file')
        print('Joint metadata and common-file SHA256 verified')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact.payload(), ensure_ascii=False, indent=2) + '\n')
        print(f'Joint metadata exported to {args.output}')


if __name__ == '__main__':
    main()
