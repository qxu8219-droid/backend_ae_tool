"""Concrete integration defects found by the independent T08 review."""
import asyncio
import math
import threading

import pytest
from app.adapters.base import AdapterError, ParseLimits, RawColumn, RawTable
from app.adapters.mapping import parse_mapping
from app.adapters.normalize import normalize_table


def normalized(tmp_path,columns,mapping):
    source=tmp_path/'raw.csv';source.write_text('trace source')
    return normalize_table(RawTable(columns,'test'),source,parse_mapping(mapping),limits=ParseLimits(),parser='csv_v1')


@pytest.mark.parametrize('unit,value',[('mm',1000),('cm',123),('deg',180)])
def test_custom_channel_retains_its_declared_value_and_unit(tmp_path,unit,value):
    data=normalized(tmp_path,{'timestamp':RawColumn([0]),'torso_joint1':RawColumn([0]),'extra':RawColumn([value])},
                    {'custom_channels':{'custom':{'field':'extra','unit':unit}}})
    assert data.channels['custom'].unit==unit
    assert data.channels['custom'].values==[float(value)]


def test_rotated_x_inherits_filled_y_provenance_and_all_raw_source_fields(tmp_path):
    data=normalized(tmp_path,{'timestamp':RawColumn([0,.1]),'torso_joint1':RawColumn([0,0]),'x':RawColumn([1,2]),'y':RawColumn([3,None]),'yaw':RawColumn([0,0])},
                    {'chassis':{'available':True,'transform':{'rotation_rad':math.pi/2}}})
    assert data.channels['chassis.x'].values==pytest.approx([-3,-3])
    assert 'forward_filled' in data.channels['chassis.x'].quality[1].processing
    assert data.metadata['composite_sources']['chassis.x'][1]=={'x':[1],'y':[0]}


def test_cancelling_during_failed_import_cleanup_releases_the_single_import_slot(tmp_path,monkeypatch):
    from app.services import log_store
    entered=threading.Event();release=threading.Event();real=log_store.shutil.rmtree
    def gated(path,*args,**kwargs):
        if '.staging' in str(path):
            entered.set();assert release.wait(3)
        return real(path,*args,**kwargs)
    monkeypatch.setattr(log_store.shutil,'rmtree',gated)
    async def broken():
        if False: yield b''
        raise AdapterError('SOURCE_FAILURE','broken upload source')
    async def run():
        store=log_store.LogStore(tmp_path)
        task=asyncio.create_task(store.import_file('s',broken(),'x.csv'))
        assert await asyncio.to_thread(entered.wait,3)
        task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
        with pytest.raises(AdapterError) as error:await store.import_file('s',broken(),'x.csv')
        assert error.value.code=='SOURCE_FAILURE'
        assert not list((tmp_path/'.staging').iterdir())
    asyncio.run(run())


def test_plugin_output_is_validated_at_registry_boundary(tmp_path):
    from app.adapters.registry import AdapterRegistry
    class Invalid:
        def probe(self,path):return .9
        def load(self,path,config):return ('not','ReplayData')
    registry=AdapterRegistry();registry.register('invalid',Invalid())
    with pytest.raises(AdapterError) as error:registry.load(tmp_path/'x',parser='invalid')
    assert error.value.code=='INVALID_ADAPTER_OUTPUT'


def test_plugin_runtime_errors_use_structured_adapter_failure(tmp_path):
    from app.adapters.registry import AdapterRegistry
    class Broken:
        def probe(self,path):return .9
        def load(self,path,config):raise RuntimeError('plugin implementation failed')
    registry=AdapterRegistry();registry.register('broken',Broken())
    with pytest.raises(AdapterError) as error:registry.load(tmp_path/'x',parser='broken')
    assert error.value.code=='ADAPTER_LOAD_FAILED'


def test_plugin_missing_import_metadata_is_a_structured_error(tmp_path):
    from app.adapters.normalize import upload_result
    data=normalized(tmp_path,{'timestamp':RawColumn([0]),'torso_joint1':RawColumn([0])},{})
    incomplete=data.model_copy(update={'metadata':{}})
    with pytest.raises(AdapterError) as error:upload_result(incomplete)
    assert error.value.code=='INVALID_ADAPTER_OUTPUT'


def test_repeated_cancellation_waits_for_filesystem_mutation_to_finish():
    from app.services.import_worker import _finish_io
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    def slow():
        entered.set();assert release.wait(3);finished.set()
    async def run():
        task=asyncio.create_task(_finish_io(slow))
        assert await asyncio.to_thread(entered.wait,3)
        try:
            task.cancel();await asyncio.sleep(0);await asyncio.sleep(0)
            task.cancel();await asyncio.sleep(0);await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):await task
        assert finished.is_set()
    asyncio.run(run())
