"""验收报告不能把未收集、未执行和跳过的参数用例算作通过。"""

from scripts.render_acceptance import classify, mappings, source_requirements


def test_acceptance_mapping_preserves_source_ids_and_references_real_tests():
    mapped = mappings()
    assert "USG-A09" not in source_requirements()
    assert "MEM-A07" not in source_requirements()
    assert "RUN-A10" not in source_requirements()
    assert "GLO-A13" not in source_requirements()
    assert "GLO-A09" in mapped


def test_missing_collection_and_skipped_parameters_cannot_pass():
    first, second = "tests/test_x.py::test_first", "tests/test_x.py::test_second"
    outcome = {first: {"state": "passed", "evidence": "a.xml"}}
    assert classify([first, second], outcome, [first])[0] == "缺少收集记录"
    outcome[first + "[a]"] = {"state": "passed", "evidence": "a.xml"}
    outcome[first + "[b]"] = {"state": "skipped", "evidence": "a.xml"}
    assert classify([first], outcome, [first + "[a]", first + "[b]"])[0] == "证据未齐"
    assert classify([first], {}, [first])[0] == "证据未齐"
    outcome[first] = {"state": "failed", "evidence": "a.xml"}
    assert classify([first], outcome, [first])[0] == "用例失败"
