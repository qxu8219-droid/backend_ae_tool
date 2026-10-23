import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]

def call(tmp_path,*args):
    env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    return subprocess.run([sys.executable,str(ROOT/'scripts/import_log.py'),'--store',str(tmp_path/'store'),*args],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)


def test_offline_cli_import_inspect_and_cross_namespace_query(tmp_path):
    source=tmp_path/'source.csv';source.write_text('timestamp,torso_joint1\n0,0.1\n1,0.2\n')
    imported=call(tmp_path,'import',str(source));assert imported.returncode==0,imported.stderr
    result=json.loads(imported.stdout);lid=result['log_id'];assert result['stats']['sample_count']==2
    listed=call(tmp_path,'list');assert json.loads(listed.stdout)[0]['log_id']==lid
    loaded=call(tmp_path,'show',lid);assert json.loads(loaded.stdout)['channels']['torso_joint1']['values']==[0.1,0.2]
    channels=call(tmp_path,'channels',lid);assert len(json.loads(channels.stdout)['channels'])==28
    other=call(tmp_path,'--session','other','show',lid);assert other.returncode==1
    assert json.loads(other.stderr)['error']['code']=='LOG_NOT_FOUND'


def test_cli_parse_failure_is_structured_and_does_not_publish_partial_archive(tmp_path):
    source=tmp_path/'source.json';source.write_text('[{"timestamp":0,"torso_joint1":"bad"}]')
    failed=call(tmp_path,'import',str(source));assert failed.returncode==1
    assert json.loads(failed.stderr)['error']['code']=='FIELD_NOT_NUMERIC'
    assert json.loads(call(tmp_path,'list').stdout)==[]


def test_explicit_null_mapping_file_is_rejected_instead_of_silently_using_defaults(tmp_path):
    source=tmp_path/'source.csv';source.write_text('timestamp,torso_joint1\n0,0.1\n')
    mapping=tmp_path/'mapping.json';mapping.write_text('null')
    result=call(tmp_path,'import',str(source),'--mapping',str(mapping))
    assert result.returncode==1
    assert json.loads(result.stderr)['error']['code']=='INVALID_MAPPING'
    assert json.loads(call(tmp_path,'list').stdout)==[]
