import glob
import json
import os

import pytest


def _platform_json_path(testbed):
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    with open(os.path.join(base_dir, "testbeds", f"{testbed}.json")) as f:
        npu_cfg = json.load(f)["npu"][0]
    asic_dir = glob.glob(f"{base_dir}/npu/**/{npu_cfg['asic']}", recursive=True)[0]
    return os.path.join(asic_dir, npu_cfg["target"], "platform.json")


def _breakout_pairs(platform_json):
    if not os.path.isfile(platform_json):
        return [(None, None)]
    with open(platform_json) as f:
        interfaces = json.load(f).get("interfaces", {})
    pairs = sorted((int(port["index"].split(",")[0]), mode) for port in interfaces.values() for mode in port.get("breakout_modes", {}))
    return pairs or [(None, None)]


def pytest_generate_tests(metafunc):
    if "port_index" not in metafunc.fixturenames or "breakout_mode" not in metafunc.fixturenames:
        return
    pairs = _breakout_pairs(_platform_json_path(metafunc.config.getoption("--testbed")))
    metafunc.parametrize("port_index,breakout_mode", pairs, ids=[f"{port_index}-{mode}" for port_index, mode in pairs])


@pytest.fixture(scope="module", autouse=True)
def skip_all(testbed_instance):
    testbed = testbed_instance
    if testbed is not None and len(testbed.npu) != 1:
        pytest.skip('invalid for "{}" testbed'.format(testbed.name))


@pytest.fixture(scope="module", autouse=True)
def skip_saivs(npu):
    if npu is not None and npu.target == "saivs":
        pytest.skip('not supported for "{}" target'.format(npu.target))


@pytest.fixture(scope="module", autouse=True)
def restore_ports(npu):
    yield
    npu.reset()


@pytest.fixture(scope="module", autouse=True)
def check_platform_json(npu):
    if not os.path.isfile(npu._platform_json_path()):
        pytest.skip("platform.json not found for this testbed")


@pytest.fixture
def breakout_groups(npu, port_index, breakout_mode):
    groups = {}
    for case in npu.iter_breakout_ports(index=port_index, mode=breakout_mode):
        groups.setdefault((case["index"], case["mode"]), []).append(case)
    assert groups, f"no breakout cases for port index {port_index} mode {breakout_mode}"
    return groups


def _configure_breakout_group(npu, port_cases):
    sku_speed = str(port_cases[0]["speed_mbps"])
    npu.set_sku_mode({
        "port": [{"lanes": c["lanes"], "speed": sku_speed} for c in port_cases],
        "speed": sku_speed,
        "autoneg": "off",
        "fec": "none",
    })
    return list(zip(npu.port_oids, port_cases))


def _iter_configured_ports(npu, breakout_groups):
    for _, port_cases in sorted(breakout_groups.items()):
        for port_oid, case in _configure_breakout_group(npu, port_cases):
            yield port_oid, case


def _assert_supported_speeds(npu, port_oid, case):
    expected_speeds = set(case["supported_speeds_mbps"])
    supported = npu.get(port_oid, ["SAI_PORT_ATTR_SUPPORTED_SPEED", npu.make_list(10, "0")]).to_list()
    actual_speeds = {int(s) for s in supported if int(s) != 0}
    assert expected_speeds <= actual_speeds, (
        f"alias {case['alias']} mode {case['mode']}: "
        f"platform {expected_speeds} not in supported {actual_speeds}"
    )


def _assert_port_at_speed(npu, port_oid, speed_mbps):
    npu.set(port_oid, ["SAI_PORT_ATTR_SPEED", str(speed_mbps)])
    assert npu.get(port_oid, ["SAI_PORT_ATTR_SPEED"]).uint32() == speed_mbps

    fec_modes = npu.get(port_oid, ["SAI_PORT_ATTR_SUPPORTED_FEC_MODE", npu.make_list(10, "0")]).to_list()
    for fec in (m for m in fec_modes if m and m != "0"):
        npu.set(port_oid, ["SAI_PORT_ATTR_FEC_MODE", fec])
        assert npu.get(port_oid, ["SAI_PORT_ATTR_FEC_MODE"]).value() == fec

    orig_loopback = npu.get(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE"]).value()
    for lb_mode in ("SAI_PORT_INTERNAL_LOOPBACK_MODE_NONE", "SAI_PORT_INTERNAL_LOOPBACK_MODE_PHY"):
        npu.set(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE", lb_mode])
        assert npu.get(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE"]).value() == lb_mode
    npu.set(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE", orig_loopback])

    npu.set(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE", "false"])
    assert npu.get(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE"]).value() == "false"
    npu.set(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE", "true"])
    assert npu.get(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE"]).value() == "true"


class TestPlatformPortBreakout:
    """Port create and attribute checks driven by platform.json breakout modes."""

    def test_platform_port_breakout(self, npu, breakout_groups):
        for port_oid, case in _iter_configured_ports(npu, breakout_groups):
            _assert_supported_speeds(npu, port_oid, case)
            for speed_mbps in case["supported_speeds_mbps"]:
                _assert_port_at_speed(npu, port_oid, speed_mbps)
