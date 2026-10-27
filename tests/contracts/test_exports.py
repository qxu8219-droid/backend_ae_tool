import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT=Path(__file__).resolve().parents[2]


def test_examples_also_pass_standard_json_schema_validation():
    from jsonschema import Draft202012Validator
    from pydantic import TypeAdapter
    from app.schemas import REST_MODELS,WS_SERVER_ADAPTER,WS_CLIENT_ADAPTER
    from app.schemas.examples import examples as service_examples
    from replay_protocol import PROTOCOL_MODELS,PROTOCOL_ADAPTERS
    from replay_protocol.examples import examples as protocol_examples
    validators={**PROTOCOL_ADAPTERS,'ws-server':WS_SERVER_ADAPTER,'ws-client':WS_CLIENT_ADAPTER,
                **{name:TypeAdapter(model) for name,model in {**PROTOCOL_MODELS,**REST_MODELS}.items()}}
    for collection in [protocol_examples(),service_examples()]:
        for name,example in collection.items():
            schema=validators[example['schema']].json_schema()
            Draft202012Validator.check_schema(schema)
            errors=list(Draft202012Validator(schema).iter_errors(example['payload']))
            assert not errors,(name,[error.message for error in errors])


def test_export_includes_rest_and_independent_wire_contracts(tmp_path):
    script=ROOT/'scripts/export_contracts.py'
    result=subprocess.run([sys.executable,str(script),'--output-dir',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    files={p.name for p in tmp_path.glob('*.json')}
    assert {'openapi.json','ws-server.schema.json','ws-client.schema.json',
            'ipc-request.schema.json','ipc-response.schema.json','worker-event.schema.json',
            'target.schema.json','frame-catalog.schema.json','presented-frame.schema.json',
            'replay-data.schema.json','examples.json'}<=files
    document=json.loads((tmp_path/'openapi.json').read_text())
    methods={'get','post','delete','put','patch'}
    assert sum(len(methods & set(operations)) for operations in document['paths'].values())==19
    assert document['openapi'].startswith('3.1.')
    assert json.loads((tmp_path/'ws-server.schema.json').read_text())['discriminator']['propertyName']=='type'
    examples=json.loads((tmp_path/'examples.json').read_text())
    assert len(examples)>=20
    previous={p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    check=subprocess.run([sys.executable,str(script),'--output-dir',str(tmp_path),'--check'],capture_output=True,text=True)
    assert check.returncode==0,check.stdout+check.stderr
    assert previous=={p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    (tmp_path/'openapi.json').write_text('{}')
    stale=subprocess.run([sys.executable,str(script),'--output-dir',str(tmp_path),'--check'],capture_output=True,text=True)
    assert stale.returncode==1
    assert 'openapi.json' in stale.stdout
    assert (tmp_path/'openapi.json').read_text()=='{}', '--check must never rewrite artifacts'
