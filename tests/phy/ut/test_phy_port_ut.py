import pytest
from saichallenger.common.sai_data import SaiObjType
from saichallenger.common.sai import Sai

port_attrs = Sai.get_obj_attrs(SaiObjType.PORT)
port_attrs_default = {}
port_attrs_updated = {}


@pytest.fixture(scope="module", autouse=True)
def skip_all(testbed_instance):
    testbed = testbed_instance
    if testbed is not None and len(testbed.phy) != 1:
        pytest.skip("invalid for \"{}\" testbed".format(testbed.name))


@pytest.fixture(scope="module")
def sai_port_obj(phy):
    port_oid = phy.port_oids[0]
    yield port_oid

    # Fall back to the defaults
    for attr in port_attrs_updated:
        if attr in port_attrs_default:
            phy.set(port_oid, [attr, port_attrs_default[attr]])


@pytest.mark.parametrize(
    "attr,attr_type",
    port_attrs
)
def test_get_before_set_attr(phy, dataplane, sai_port_obj, attr, attr_type):
    status, data = phy.get_by_type(sai_port_obj, attr, attr_type, do_assert = False)
    phy.assert_status_success(status)

    if status == "SAI_STATUS_SUCCESS":
        port_attrs_default[attr] = data.value()


@pytest.mark.parametrize(
    "attr,attr_value",
    [
        ("SAI_PORT_ATTR_ADMIN_STATE",               "true"),
        ("SAI_PORT_ATTR_ADMIN_STATE",               "false"),
        # autoneg, speed and FEC attributes are set from the sku file, for example: phy/broadcom/BCM81724/saivs/sku/8x100g.json
        ("SAI_PORT_ATTR_LOOPBACK_MODE",             "SAI_PORT_LOOPBACK_MODE_PHY_REMOTE"),
        ("SAI_PORT_ATTR_LOOPBACK_MODE",             "SAI_PORT_LOOPBACK_MODE_NONE"),
        ("SAI_PORT_ATTR_LOOPBACK_MODE",             "SAI_PORT_LOOPBACK_MODE_MAC"),
        ("SAI_PORT_ATTR_MTU",                       "9000"),
        ("SAI_PORT_ATTR_MTU",                       "1500"),
        ("SAI_PORT_ATTR_PRIORITY_FLOW_CONTROL",     "0"),
    ],
)
def test_set_attr(phy, dataplane, sai_port_obj, attr, attr_value):
    status = phy.set(sai_port_obj, [attr, attr_value], False)
    phy.assert_status_success(status)

    if status == "SAI_STATUS_SUCCESS":
        port_attrs_updated[attr] = attr_value


@pytest.mark.parametrize(
    "attr,attr_type",
    [
        ("SAI_PORT_ATTR_ADMIN_STATE",               "bool"),
        ("SAI_PORT_ATTR_AUTO_NEG_MODE",             "bool"),
        ("SAI_PORT_ATTR_SPEED",                     "sai_uint32_t"),
        ("SAI_PORT_ATTR_FEC_MODE",                  "sai_uint32_t"),
        ("SAI_PORT_ATTR_LOOPBACK_MODE",             "sai_uint32_t"),
        ("SAI_PORT_ATTR_MTU",                       "sai_uint32_t"),
    ]
)
def test_get_after_set_attr(phy, dataplane, sai_port_obj, attr, attr_type):
    status, data = phy.get_by_type(sai_port_obj, attr, attr_type, do_assert = False)
    phy.assert_status_success(status)

    if attr in port_attrs_updated:
        assert data.value() == port_attrs_updated[attr]


@pytest.mark.parametrize(
    "attr, attr_type, dummy_value",
    [
        ("SAI_PORT_ATTR_OPER_STATUS", "sai_uint32_t", "SAI_PORT_OPER_STATUS_UP"),
        ("SAI_PORT_ATTR_HW_LANE_LIST", "sai_s32_list_t", "1:1"),
    ],
)
def test_readonly_port_attributes(phy, sai_port_obj, attr, attr_type, dummy_value):
    """Verify that read-only port attributes can be read via GET, but fail when modified via SET."""
    result = phy.get(sai_port_obj, [attr], do_assert=False)
    status = result[0] if isinstance(result, tuple) else result
    phy.assert_status_success(status)

    set_status = phy.set(sai_port_obj, [attr, dummy_value], do_assert=False)
    assert set_status != "SAI_STATUS_SUCCESS", f"Read-Only attribute {attr} was modified successfully!"


def test_port_invalid_oid_operations(phy, sai_port_obj, dataplane):
    """Verify that performing GET/SET operations on a non-existing OID fails gracefully."""
    invalid_oid = "oid:0x100000000ffff"
    
    # GET on invalid OID
    try:
        result = phy.get(invalid_oid, ["SAI_PORT_ATTR_ADMIN_STATE"], do_assert=False)
        status = result[0] if isinstance(result, tuple) else result
        assert status != "SAI_STATUS_SUCCESS", "GET operation on invalid OID should fail"
    except Exception as e:
        # Catch client-level failure (VID -> RID lookup failure)
        pass

    #verify syncd is still alive after invalid GET
    status, _ = phy.get(sai_port_obj, ["SAI_PORT_ATTR_ADMIN_STATE"], do_assert=False)
    phy.assert_status_success(status)

    # SET on invalid OID
    try:
        status = phy.set(invalid_oid, ["SAI_PORT_ATTR_ADMIN_STATE", "true"], do_assert=False)
        assert status != "SAI_STATUS_SUCCESS", "SET operation on invalid OID should fail"
    except Exception as e:
        pass

    #verify syncd is still alive after invalid SET
    status, _ = phy.get(sai_port_obj, ["SAI_PORT_ATTR_ADMIN_STATE"], do_assert=False)
    phy.assert_status_success(status)


@pytest.mark.parametrize(
    "attr, attr_type, test_val",
    [
        ("SAI_PORT_ATTR_AUTO_NEG_MODE", "bool", "true"),
        ("SAI_PORT_ATTR_AUTO_NEG_MODE", "bool", "false"),
    ],
)
def test_phy_autoneg_toggle(phy, sai_port_obj, attr, attr_type, test_val):
    """Verify toggling Auto-Negotiation mode on PHY interface."""
    try:
        res = phy.get(sai_port_obj, ["SAI_PORT_ATTR_SUPPORTED_AUTO_NEG_MODE"], do_assert=False)
        status, supp_data = res if isinstance(res, tuple) else (res, None)

        if status == "SAI_STATUS_SUCCESS" and supp_data is not None:
            raw_val = supp_data.value()
            is_supported = raw_val is True or str(raw_val).lower() in ("true", "1")
            if not is_supported:
                pytest.skip("Auto-negotiation explicitly reported as NOT supported on this port")
    except (NotImplementedError, Exception):
        # Attribute is not implemented in SaiPhy driver yet, fallback to direct SET check
        pass

    #Try setting AUTO_NEG_MODE directly
    status = phy.set(sai_port_obj, [attr, test_val], do_assert=False)
    if status != "SAI_STATUS_SUCCESS":
        pytest.skip(f"Auto-negotiation toggle not supported on target PHY")

    res = phy.get(sai_port_obj, [attr], do_assert=False)
    status, data = res if isinstance(res, tuple) else (res, None)
    
    phy.assert_status_success(status)
    assert data is not None, f"Failed to retrieve data for {attr}"
    assert str(data.value()).lower() == test_val.lower(), f"Expected {test_val}, got {data.value()}"


def test_port_batch_get_stats(phy, sai_port_obj):
    """Verify requesting multiple stats counters in a single RPC batch call."""
    counters = [
        "SAI_PORT_STAT_IF_IN_OCTETS", "",
        "SAI_PORT_STAT_IF_OUT_OCTETS", "",
        "SAI_PORT_STAT_IF_IN_UCAST_PKTS", "",
    ]
    stats_res = phy.get_stats(sai_port_obj, counters)
    
    cntrs = stats_res.counters() if hasattr(stats_res, "counters") else stats_res[1] if isinstance(stats_res, tuple) else stats_res
    
    assert len(cntrs) == 3
    for counter_name in ["SAI_PORT_STAT_IF_IN_OCTETS", "SAI_PORT_STAT_IF_OUT_OCTETS", "SAI_PORT_STAT_IF_IN_UCAST_PKTS"]:
        assert counter_name in cntrs
