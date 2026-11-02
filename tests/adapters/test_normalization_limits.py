from pathlib import Path
import pytest
from app.adapters.base import AdapterError, LimitError, ParseLimits, RawColumn, RawTable
from app.adapters.mapping import MappingError, parse_mapping
from app.adapters.normalize import normalize_table, upload_result


def data(tmp_path, columns, limits=ParseLimits(), mapping=None):
    path=tmp_path/'source.csv';path.write_text('synthetic fixture\n')
    return normalize_table(RawTable(columns,'test'),path,parse_mapping(mapping),limits=limits,parser='csv_v1')


def test_normalized_missing_defaults_also_count_against_point_budget(tmp_path):
    with pytest.raises(LimitError):
        data(tmp_path,{'timestamp':RawColumn([0,1]),'torso_joint1':RawColumn([0,1])},ParseLimits(max_points=55))


def test_pathological_input_issues_fail_explicitly_instead_of_growing_unbounded(tmp_path):
    with pytest.raises(LimitError) as e:
        data(tmp_path,{'timestamp':RawColumn([0,1]),'torso_joint1':RawColumn([0,1])},ParseLimits(max_issues=3))
    assert len(e.value.issues)<=3


def test_completely_invalid_timestamps_and_si_overflow_are_fatal(tmp_path):
    with pytest.raises(AdapterError) as e:
        data(tmp_path,{'timestamp':RawColumn(['NaN',None]),'torso_joint1':RawColumn([0,1])})
    assert e.value.code=='NO_TIMESTAMP'
    with pytest.raises(AdapterError) as e:
        data(tmp_path,{'timestamp':RawColumn([0]),'torso_joint1':RawColumn([1e308])},mapping={'joints':{'per_joint_overrides':{'torso_joint1':{'scale':1e308}}}})
    assert e.value.code=='NUMERIC_OVERFLOW'


def test_source_and_effective_mapping_hashes_change_independently_and_coverage_is_canonical(tmp_path):
    columns={'timestamp':RawColumn([0,1]),'torso_joint1':RawColumn([1,2])}
    a=data(tmp_path,columns)
    b=data(tmp_path,columns,mapping={'joints':{'unit':'deg'}})
    assert a.raw_file_sha256==b.raw_file_sha256
    assert a.mapping_snapshot_id!=b.mapping_snapshot_id
    assert a.log_id!=b.log_id
    result=upload_result(a)
    assert len(result.channels)==28
    assert result.stats.joints_mapped==['torso_joint1']
    assert len(result.stats.joints_missing)==27
    assert a.metadata['conversion_steps']['torso_joint1']['unit_factor']==1


@pytest.mark.parametrize('config',[{'schema_version':True},{'schema_version':1.0},{'options':{'unwrap_continuous':0}}])
def test_mapping_versions_and_boolean_options_reject_numeric_equivalents(config):
    with pytest.raises(MappingError):parse_mapping(config)
