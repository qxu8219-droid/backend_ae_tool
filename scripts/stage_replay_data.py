#!/usr/bin/env python3
"""Stage inspected local originals into a reproducible, git-ignored data catalog.

This command never connects to a remote host, parses observations, or calibrates
unknown quantities. Source copies are byte-preserved and hashed.
"""
import argparse,hashlib,json,shutil
from pathlib import Path


def stage(inspection,output):
    inspection=inspection.resolve();output=output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    catalog={'schema':'replay.local-source-catalog.v1','storage':'raw originals; adapters not yet activated','sources':[]}
    def copy(source,relative):
        source=source.resolve()
        if not source.is_relative_to(inspection) or not source.is_file():raise ValueError('Source is outside the inspected snapshot')
        target=output/'raw'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise RuntimeError('Copy hash mismatch')
        target.chmod(0o444)
        return {'path':target.relative_to(output).as_posix(),'bytes':target.stat().st_size,'sha256':digest}
    for run in sorted((inspection/'robot/samples').iterdir()):
        if not run.is_dir():continue
        files=[copy(path,Path('runtime')/run.name/path.relative_to(run)) for path in sorted(run.rglob('*')) if path.is_file()]
        catalog['sources'].append({'source_id':run.name,'format':'b041-runtime-directory','ingest_status':'raw_only','clock':'per-run monotonic; per-group source timing where recorded','files':files,'mapping_status':{'arm_torso':'18 rad joints confirmed','gripper':'EE2 candidate metric mapping; zero/calibration pending','base':'world pose and raw slots not confirmed'}})
    manifest=json.loads((inspection/'server/parquet-manifest.json').read_text())
    for item in manifest:
        source=inspection/'server/parquet'/Path(item['local_path']).name
        catalog['sources'].append({'source_id':item['source_id'],'format':'b041-full23-parquet','ingest_status':'raw_only','remote_source_path':item['remote_path'],'clock':'source_log_time_ns; preserve group valid/source_ns/age','files':[copy(source,Path('parquet')/item['source_id']/'episode.parquet')],'mapping_status':{'arm_torso':'18 rad joints confirmed','gripper':'calibration pending','base':'velocity only; not world pose'}})
    for name in ['annotations.reviewed.json','source-links.json','dataset-metadata.json','boundary-status-summary.json']:
        catalog['sources'].append({'source_id':name,'format':'annotation-or-provenance-sidecar','ingest_status':'raw_only','files':[copy(inspection/'server'/name,Path('metadata')/name)]})
    catalog['sources'].append({'source_id':'runtime-download-manifest.json','format':'source-provenance-manifest','ingest_status':'raw_only','files':[copy(inspection/'robot/download-manifest.json',Path('metadata/runtime-download-manifest.json'))]})
    catalog['sources'].append({'source_id':'runtime-adapter-provenance','format':'source-profile-and-adapter','ingest_status':'reference_only','files':[
        copy(inspection/'robot/context'/name,Path('provenance')/name)
        for name in ['profile_R1_native_sdk_20261020.json','full23_adapter.py']
    ]})
    (output/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'sources':len(catalog['sources']),'files':sum(len(x['files']) for x in catalog['sources']),'root':str(output)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspection-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();stage(args.inspection_dir,args.output_dir)
