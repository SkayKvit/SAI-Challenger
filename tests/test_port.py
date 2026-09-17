import os

import pytest


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

@pytest.mark.parametrize("port_index", [None]) # None - all ports
class TestPlatformPortBreakout:
    """Port create and attribute checks driven by platform.json breakout modes."""

    def test_platform_port_breakout(self, npu, port_index):
        breakout_cases_by_port = {}
        for case in npu.iter_breakout_ports(index=port_index):
            key = (case["index"], case["mode"])
            breakout_cases_by_port.setdefault(key, []).append(case)
        assert breakout_cases_by_port, f"no breakout cases for index {port_index}"

        for (idx, breakout_mode), port_cases in sorted(breakout_cases_by_port.items()):
            sku_speed = str(port_cases[0]["speed_mbps"])
            npu.set_sku_mode({
                "port": [{"lanes": c["lanes"], "speed": sku_speed} for c in port_cases],
                "speed": sku_speed,
                "autoneg": "off",
                "fec": "none",
            })

            for idx, case in enumerate(port_cases):
                port_oid = npu.port_oids[idx]
                expected_speeds = set(case["supported_speeds_mbps"])

                supported = npu.get(port_oid, ["SAI_PORT_ATTR_SUPPORTED_SPEED", npu.make_list(10, "0")]).to_list()
                actual_speeds = {int(s) for s in supported if int(s) != 0}
                assert expected_speeds <= actual_speeds, (
                    f"index {idx} mode {breakout_mode} alias {case['alias']}: "
                    f"platform {expected_speeds} not in supported {actual_speeds}"
                )

                for speed_mbps in case["supported_speeds_mbps"]:
                    npu.set(port_oid, ["SAI_PORT_ATTR_SPEED", str(speed_mbps)])
                    assert npu.get(port_oid, ["SAI_PORT_ATTR_SPEED"]).uint32() == speed_mbps

                    fec_modes = npu.get(port_oid, ["SAI_PORT_ATTR_SUPPORTED_FEC_MODE", npu.make_list(10, "0")]).to_list()
                    for fec in [m for m in fec_modes if m and m != "0"]:
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
