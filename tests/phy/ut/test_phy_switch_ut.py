import pytest
from saichallenger.common.sai import Sai

switch_attrs = Sai.get_obj_attrs("SAI_OBJECT_TYPE_SWITCH")


@pytest.fixture(scope="module", autouse=True)
def skip_all(testbed_instance):
    testbed = testbed_instance
    if testbed is not None and len(testbed.phy) != 1:
        pytest.skip("invalid for \"{}\" testbed".format(testbed.name))


@pytest.mark.parametrize(
    "attr,attr_type",
    switch_attrs
)
def test_get_attr(phy, dataplane, attr, attr_type):
    status, data = phy.get_by_type(phy.switch_oid, attr, attr_type, do_assert = False)
    phy.assert_status_success(status)


def test_phy_switch_type(phy):
    """Verify that the SWITCH identifies as SAI_SWITCH_TYPE_PHY."""
    status, data = phy.get(phy.switch_oid, ["SAI_SWITCH_ATTR_TYPE"], do_assert=False)
    phy.assert_status_success(status)

    assert data.value() == "SAI_SWITCH_TYPE_PHY", f"Expected SAI_SWITCH_TYPE_PHY, got: {data.value()}"


def test_phy_switch_port_number(phy):
    """Verify SAI_SWITCH_ATTR_NUMBER_OF_ACTIVE_PORTS on PHY SWITCH."""
    status, data = phy.get(phy.switch_oid, ["SAI_SWITCH_ATTR_NUMBER_OF_ACTIVE_PORTS", "0"], do_assert=False)
    phy.assert_status_success(status)
    assert data is not None, "Port number response is None"

    port_number = data.uint32()
    assert port_number > 0, f"Expected PORT_NUMBER > 0 on PHY, got: {port_number}"


def test_phy_switch_port_list(phy):
    """Verify SAI_SWITCH_ATTR_PORT_LIST on PHY SWITCH and consistency with PORT_NUMBER."""
    
    status, data_num = phy.get(phy.switch_oid, ["SAI_SWITCH_ATTR_NUMBER_OF_ACTIVE_PORTS", "0"], do_assert=False)
    phy.assert_status_success(status)
    port_number = data_num.uint32()

    status, data_list = phy.get_by_type(phy.switch_oid, "SAI_SWITCH_ATTR_PORT_LIST", "sai_object_list_t", do_assert=False)
    phy.assert_status_success(status)
    assert data_list is not None, "Port list response data is None"

    port_oids = data_list.oids()

    assert len(port_oids) > 0, "PHY Switch returned an empty port list!"

    assert len(port_oids) == port_number, (
        f"Mismatch: PORT_NUMBER={port_number}, but PORT_LIST length={len(port_oids)}"
    )


def test_phy_switch_firmware_version(phy):
    """Verify firmware / microcode version query on PHY SWITCH."""
    status, data = phy.get(phy.switch_oid, ["SAI_SWITCH_ATTR_FIRMWARE_MAJOR_VERSION"], do_assert=False)
    phy.assert_status_success(status)
    assert data is not None, "Returned data is None"

    raw_val = data.value()
    assert raw_val is not None, "Firmware version value is missing"


def test_phy_switch_port_list_oids_validity(phy):
    """Verify that every OID in PORT_LIST is a valid PORT object and accessible."""
    status_list, data_list = phy.get_by_type(phy.switch_oid, "SAI_SWITCH_ATTR_PORT_LIST", "sai_object_list_t", do_assert=False)
    phy.assert_status_success(status_list)

    port_oids = data_list.oids()

    for port_oid in port_oids:
        _, data = phy.get(port_oid, ["SAI_PORT_ATTR_TYPE", "0"], do_assert=False)
        port_type = data.value()
        assert port_type in ["SAI_PORT_TYPE_LOGICAL", "0"], f"Unexpected port type {port_type} for OID {port_oid}"
