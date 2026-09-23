import os
import pytest
from pytest_dependency import depends
from saichallenger.common.sai_npu import SaiNpu


def pytest_generate_tests(metafunc):
    if "port_name" not in metafunc.fixturenames or "breakout_mode" not in metafunc.fixturenames:
        return
    testbed = metafunc.config.getoption("--testbed")
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    ports = SaiNpu.get_port_breakout_modes(base_dir=base_dir, testbed=testbed)
    pairs = [(name, mode) for name in sorted(ports) for mode in sorted(ports[name]["modes"])]
    metafunc.parametrize("port_name,breakout_mode", pairs, ids=[f"{p}-{m}" for p, m in pairs])


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


class TestDynamicPortBreakout:
    """Port create and attribute checks driven by platform.json breakout modes."""

    @pytest.mark.dependency()
    def test_dynamic_port_breakout(self, npu, port_name, breakout_mode):
        cases = SaiNpu.get_port_breakout_modes(npu=npu, port_name=port_name, breakout_mode=breakout_mode)
        sku_speed = str(cases[0]["speed_mbps"])
        npu.set_sku_mode({
            "port": [{"lanes": case["lanes"], "speed": str(case["speed_mbps"])} for case in cases],
            "speed": sku_speed,
            "autoneg": "off",
            "fec": "none",
        })
        assert len(npu.port_oids) == len(cases), (f"expected {len(cases)} port OIDs, got {len(npu.port_oids)}")
        for port_oid, case in zip(npu.port_oids, cases):
            expected_speeds = set(case["supported_speeds_mbps"])
            supported = npu.get(port_oid, ["SAI_PORT_ATTR_SUPPORTED_SPEED", npu.make_list(10, "0")]).to_list()
            actual_speeds = {int(s) for s in supported if int(s) != 0}
            assert expected_speeds <= actual_speeds, (
                f"alias {case['alias']} mode {case['mode']}: "
                f"platform {expected_speeds} not in supported {actual_speeds}"
            )

    @pytest.mark.dependency()
    def test_port_admin_state(self, request, npu, port_name, breakout_mode):
        depends(request, [f"TestDynamicPortBreakout::test_dynamic_port_breakout[{port_name}-{breakout_mode}]"])
        cases = SaiNpu.get_port_breakout_modes(npu=npu, port_name=port_name, breakout_mode=breakout_mode)
        for port_oid, case in zip(npu.port_oids, cases):
            npu.set(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE", "true"])
            assert npu.get(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE"]).value() == "true"

            for speed_mbps in case["supported_speeds_mbps"]:
                npu.set(port_oid, ["SAI_PORT_ATTR_SPEED", str(speed_mbps)])
                assert npu.get(port_oid, ["SAI_PORT_ATTR_SPEED"]).uint32() == speed_mbps

                for value in ("false", "true"):
                    npu.set(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE", value])
                    assert npu.get(port_oid, ["SAI_PORT_ATTR_ADMIN_STATE"]).value() == value

    @pytest.mark.dependency()
    def test_speed_and_fec_change(self, request, npu, port_name, breakout_mode):
        depends(request, [f"TestDynamicPortBreakout::test_dynamic_port_breakout[{port_name}-{breakout_mode}]"])
        cases = SaiNpu.get_port_breakout_modes(npu=npu, port_name=port_name, breakout_mode=breakout_mode)
        for port_oid, case in zip(npu.port_oids, cases):
            npu.set(port_oid, ["SAI_PORT_ATTR_FEC_MODE", "SAI_PORT_FEC_MODE_NONE"])
            assert npu.get(port_oid, ["SAI_PORT_ATTR_FEC_MODE"]).value() == "SAI_PORT_FEC_MODE_NONE"

            for speed_mbps in case["supported_speeds_mbps"]:
                npu.set(port_oid, ["SAI_PORT_ATTR_SPEED", str(speed_mbps)])
                assert npu.get(port_oid, ["SAI_PORT_ATTR_SPEED"]).uint32() == speed_mbps

                fec_modes = npu.get(port_oid, ["SAI_PORT_ATTR_SUPPORTED_FEC_MODE", npu.make_list(10, "0")]).to_list()
                for fec in (m for m in fec_modes if m and m != "0"):
                    npu.set(port_oid, ["SAI_PORT_ATTR_FEC_MODE", fec])
                    assert npu.get(port_oid, ["SAI_PORT_ATTR_FEC_MODE"]).value() == fec

    @pytest.mark.dependency()
    def test_port_loopback_modes(self, request, npu, port_name, breakout_mode):
        depends(request, [f"TestDynamicPortBreakout::test_dynamic_port_breakout[{port_name}-{breakout_mode}]"])
        cases = SaiNpu.get_port_breakout_modes(npu=npu, port_name=port_name, breakout_mode=breakout_mode)
        for port_oid, case in zip(npu.port_oids, cases):
            npu.set(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE", "SAI_PORT_INTERNAL_LOOPBACK_MODE_NONE"])
            assert npu.get(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE"]).value() == "SAI_PORT_INTERNAL_LOOPBACK_MODE_NONE"

            for speed_mbps in case["supported_speeds_mbps"]:
                npu.set(port_oid, ["SAI_PORT_ATTR_SPEED", str(speed_mbps)])
                assert npu.get(port_oid, ["SAI_PORT_ATTR_SPEED"]).uint32() == speed_mbps

                for lb_mode in ("SAI_PORT_INTERNAL_LOOPBACK_MODE_NONE", "SAI_PORT_INTERNAL_LOOPBACK_MODE_PHY"):
                    npu.set(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE", lb_mode])
                    assert npu.get(port_oid, ["SAI_PORT_ATTR_INTERNAL_LOOPBACK_MODE"]).value() == lb_mode
