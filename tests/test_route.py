import json
import time

import pytest

from saichallenger.topologies.sai_ptf_topology import topology
from saichallenger.common.sai_data import SaiObjType
from ptf.testutils import (
    send_packet,
    simple_tcp_packet,
    simple_udp_packet,
    verify_no_other_packets,
    verify_packet,
    verify_packet_any_port,
    verify_packets,
)

@pytest.fixture(scope="module", autouse=True)
def skip_all(testbed_instance):
    testbed = testbed_instance
    if testbed is not None and len(testbed.npu) != 1:
        pytest.skip('invalid for "{}" testbed'.format(testbed.name))

@pytest.fixture(scope="module", autouse=True)
def register_topology(npu, topology):
    npu._topo = topology
    npu._topo_initialized = False
    npu._topo.setup()
    yield
    npu._topo.teardown()
 
@pytest.fixture(autouse=True)
def on_prev_test_failure(prev_test_failed, npu):
    if prev_test_failed:
        npu.reset()
        npu._topo_initialized = False
        npu._topo.setup()

def _neighbor_entry_key(npu, rif_oid, ip):
    return "SAI_OBJECT_TYPE_NEIGHBOR_ENTRY:" + json.dumps(
        {
            "ip": ip,
            "rif": rif_oid,
            "switch_id": npu.switch_oid,
        }
    )

def _route_entry_key(npu, vr_oid, prefix):
    return "SAI_OBJECT_TYPE_ROUTE_ENTRY:" + json.dumps(
        {
            "dest": prefix,
            "switch_id": npu.switch_oid,
            "vr": vr_oid,
        }
    )
        
class TestMultipleRoutes:
    """
    Verify forwarding with multiple route to the same nhop.
    """

    def test_multiple_routes_forward(self, npu, dataplane, topology):
        """
        Verifies that multiple routes pointing to the same next hop correctly
        forward packets when traffic generation is enabled.
        """
        topo = topology
        router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        src_mac = "00:22:22:22:22:22"
        dst_mac = "00:11:22:33:44:55"
        dev_port10 = 10
        dev_port11 = 11
        nhop_ip = "10.10.10.2"
        route1_ip = "10.10.10.1/32"
        route2_ip = "10.10.10.2/32"
        vrf_oid = topo.default_vrf
        rif_oid = topo.port10_rif
        neighbor_key = _neighbor_entry_key(npu, rif_oid, nhop_ip)

        npu.create(neighbor_key, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", dst_mac])
        nhop = npu.create(
            SaiObjType.NEXT_HOP,
            [
                "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                "SAI_NEXT_HOP_ATTR_IP", nhop_ip,
                "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", rif_oid,
            ],
        )
        npu.create_route(route1_ip, vrf_oid, nhop)
        npu.create_route(route2_ip, vrf_oid, nhop)

        try:
            if npu.run_traffic:
                for route_ip in (route1_ip, route2_ip):
                    pkt = simple_tcp_packet(
                        eth_dst=router_mac,
                        eth_src=src_mac,
                        ip_dst=route_ip.split("/")[0],
                        ip_id=105,
                    )
                    exp_pkt = simple_tcp_packet(
                        eth_dst=dst_mac,
                        eth_src=router_mac,
                        ip_dst=route_ip.split("/")[0],
                        ip_id=105,
                        ip_ttl=63,
                    )

                    send_packet(dataplane, dev_port11, pkt)
                    verify_packet(dataplane, exp_pkt, dev_port10)
        finally:
            npu.remove_route(route1_ip, vrf_oid)
            npu.remove_route(route2_ip, vrf_oid)
            npu.remove(nhop)
            npu.remove(neighbor_key)


class TestDropRoute:
    """
    Topology & setup for TestDropRoute:
    Sets up L3 Router Interface (RIF), Neighbor (ARP), Next Hop,
    and configures a trapped route to verify CPU queue packet increment.
    """

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dst_mac = "00:11:22:33:44:55"
        request.cls.dev_port11 = 11
        request.cls.nhop_ip = "10.10.10.2"
        request.cls.route_ip = "10.10.10.1/32"

        request.cls.vrf_oid = topo.default_vrf
        request.cls.rif_oid = topo.port10_rif
        request.cls.neighbor_key = _neighbor_entry_key(npu, request.cls.rif_oid, request.cls.nhop_ip)
        request.cls.route_key = _route_entry_key(npu, request.cls.vrf_oid, request.cls.route_ip)

        npu.create(
            request.cls.neighbor_key,
            [
                "SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS",
                request.cls.dst_mac,
            ],
        )
        request.cls.nhop = npu.create(
            SaiObjType.NEXT_HOP,
            [
                "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                "SAI_NEXT_HOP_ATTR_IP", request.cls.nhop_ip,
                "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", request.cls.rif_oid,
            ],
        )
        npu.create_route(
            request.cls.route_ip,
            request.cls.vrf_oid,
            request.cls.nhop,
            [
                "SAI_ROUTE_ENTRY_ATTR_PACKET_ACTION",
                "SAI_PACKET_ACTION_TRAP",
            ],
        )
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove_route(request.cls.route_ip, request.cls.vrf_oid)
        npu.remove(request.cls.nhop)
        npu.remove(request.cls.neighbor_key)
        npu._topo_initialized = False

    def _queue_stat(self, npu, queue_oid):
        return npu.get_stats(queue_oid, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()[
            "SAI_QUEUE_STAT_PACKETS"
        ]

    def _cpu_queue0(self, npu):
        cpu_port = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_CPU_PORT", "oid:0x0"], False)[1].oid()
        return npu.get_list(cpu_port, "SAI_PORT_ATTR_QOS_QUEUE_LIST", "oid:0x0")[0]

    def test_drop_route(self, npu, dataplane):
        """
        Description:
        Verifies trapped route behavior by checking CPU queue packet counter increment.
        """
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )

        cpu_queue0 = self._cpu_queue0(npu)
        pre_stats = self._queue_stat(npu, cpu_queue0)

        status, action = npu.get(
            self.route_key,
            ["SAI_ROUTE_ENTRY_ATTR_PACKET_ACTION", ""],
            False,
        )
        assert status == "SAI_STATUS_SUCCESS"
        assert action.value() == "SAI_PACKET_ACTION_TRAP"

        send_packet(dataplane, self.dev_port11, pkt)
        verify_no_other_packets(dataplane)
        time.sleep(4)

        post_stats = self._queue_stat(npu, cpu_queue0)
        assert post_stats == pre_stats + 1, (
            "CPU queue0 packet counters did not increment for route trap: "
            f"pre={pre_stats}, post={post_stats}"
        )


class TestRouteUpdate:
    """
    Topology & setup for TestRouteUpdate:
    Sets up L3 Router Interface (RIF), Neighbor (ARP), Next Hop,
    and a route so the test can update next hops and packet actions.
    """
    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.dst_mac_1 = "00:11:22:33:44:55"
        request.cls.dst_mac_2 = "00:11:22:33:44:66"
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dev_port10 = 10
        request.cls.dev_port11 = 11
        request.cls.nhop_ip_1 = "10.10.10.2"
        request.cls.nhop_ip_2 = "10.10.10.3"
        request.cls.route_ip = "10.10.10.1/32"
        request.cls.vrf_oid = topo.default_vrf
        request.cls.rif_oid = topo.port10_rif
        request.cls.neighbor_key_1 = _neighbor_entry_key(npu, request.cls.rif_oid, request.cls.nhop_ip_1)
        request.cls.route_key = _route_entry_key(npu, request.cls.vrf_oid, request.cls.route_ip)

        npu.create(
            request.cls.neighbor_key_1,
            [
                "SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS",
                request.cls.dst_mac_1,
            ],
        )
        request.cls.nhop_1 = npu.create(
            SaiObjType.NEXT_HOP,
            [
                "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                "SAI_NEXT_HOP_ATTR_IP", request.cls.nhop_ip_1,
                "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", request.cls.rif_oid,
            ],
        )
        npu.create_route(
            request.cls.route_ip,
            request.cls.vrf_oid,
            request.cls.nhop_1,
        )
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove(_route_entry_key(npu, request.cls.vrf_oid, request.cls.route_ip), False)
        npu.remove(request.cls.nhop_1, False)
        npu.remove(request.cls.neighbor_key_1, False)
        npu._topo_initialized = False

    def _cpu_queue0(self, npu):
        cpu_port = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_CPU_PORT", "oid:0x0"], False)[1].oid()
        return npu.get_list(cpu_port, "SAI_PORT_ATTR_QOS_QUEUE_LIST", "oid:0x0")[0]

    def test_route_update(self, npu, dataplane):
        """
        Description:
        Verifies that updating a route's next hop correctly forwards packets to the new destination.
        """
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt_1 = simple_tcp_packet(
            eth_dst=self.dst_mac_1,
            eth_src=self.router_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_id=105,
            ip_ttl=63,
        )
        exp_pkt_2 = simple_tcp_packet(
            eth_dst=self.dst_mac_2,
            eth_src=self.router_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_id=105,
            ip_ttl=63,
        )

        neighbor_entry_2 = None
        nhop_2 = None
        try:
            send_packet(dataplane, self.dev_port11, pkt)
            verify_packet(dataplane, exp_pkt_1, self.dev_port10)

            neighbor_entry_2 = _neighbor_entry_key(npu, self.rif_oid, self.nhop_ip_2)
            npu.create(
                neighbor_entry_2,
                [
                    "SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS",
                    self.dst_mac_2,
                ],
            )
            nhop_2 = npu.create(
                SaiObjType.NEXT_HOP,
                [
                    "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                    "SAI_NEXT_HOP_ATTR_IP", self.nhop_ip_2,
                    "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", self.rif_oid,
                ],
            )
            self.nhop_2 = nhop_2
            self.neighbor_key_2 = neighbor_entry_2
            npu.set(self.route_key, ["SAI_ROUTE_ENTRY_ATTR_NEXT_HOP_ID", nhop_2])
            send_packet(dataplane, self.dev_port11, pkt)
            verify_packet(dataplane, exp_pkt_2, self.dev_port10)

            npu.set(self.route_key, ["SAI_ROUTE_ENTRY_ATTR_PACKET_ACTION", "SAI_PACKET_ACTION_DROP"])
            send_packet(dataplane, self.dev_port11, pkt)
            verify_no_other_packets(dataplane, timeout=3)

            npu.set(self.route_key, ["SAI_ROUTE_ENTRY_ATTR_PACKET_ACTION", "SAI_PACKET_ACTION_FORWARD"])
            send_packet(dataplane, self.dev_port11, pkt)
            verify_packet(dataplane, exp_pkt_2, self.dev_port10)

            npu.set(self.route_key, ["SAI_ROUTE_ENTRY_ATTR_PACKET_ACTION", "SAI_PACKET_ACTION_TRAP"])
            cpu_queue0 = self._cpu_queue0(npu)
            pre_stats = npu.get_stats(cpu_queue0, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()[
                "SAI_QUEUE_STAT_PACKETS"
            ]
            send_packet(dataplane, self.dev_port11, pkt)
            time.sleep(4)
            post_stats = npu.get_stats(cpu_queue0, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()[
                "SAI_QUEUE_STAT_PACKETS"
            ]
            assert post_stats == pre_stats + 1

            npu.set(self.route_key, ["SAI_ROUTE_ENTRY_ATTR_PACKET_ACTION", "SAI_PACKET_ACTION_FORWARD"])
            send_packet(dataplane, self.dev_port11, pkt)
            verify_packet(dataplane, exp_pkt_2, self.dev_port10)

        finally:
            if neighbor_entry_2 is not None:
                npu.remove(neighbor_entry_2, False)
            if nhop_2 is not None:
                npu.remove(nhop_2, False)


class TestRouteIngressRif:
    """
    Verifies that a route can forward a packet back through its ingress RIF.
    """

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dst_mac = "00:11:22:33:44:55"
        request.cls.dev_port10 = 10
        request.cls.nhop_ip = "10.10.10.2"
        request.cls.route_ip = "10.10.10.1/32"
        request.cls.vrf_oid = topo.default_vrf
        request.cls.rif_oid = topo.port10_rif
        request.cls.neighbor_key = _neighbor_entry_key(npu, request.cls.rif_oid, request.cls.nhop_ip)

        npu.create(
            request.cls.neighbor_key,
            [
                "SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS",
                request.cls.dst_mac,
            ],
        )
        request.cls.nhop = npu.create(
            SaiObjType.NEXT_HOP,
            [
                "SAI_NEXT_HOP_ATTR_TYPE",
                "SAI_NEXT_HOP_TYPE_IP",
                "SAI_NEXT_HOP_ATTR_IP",
                request.cls.nhop_ip,
                "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID",
                request.cls.rif_oid,
            ],
        )
        npu.create_route(
            request.cls.route_ip,
            request.cls.vrf_oid,
            request.cls.nhop,
        )
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove_route(request.cls.route_ip, request.cls.vrf_oid)
        npu.remove(request.cls.nhop)
        npu.remove(request.cls.neighbor_key)
        npu._topo_initialized = False

    def test_route_ingress_rif(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt = simple_tcp_packet(
            eth_dst=self.dst_mac,
            eth_src=self.router_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=63,
        )

        send_packet(dataplane, self.dev_port10, pkt)
        verify_packet(dataplane, exp_pkt, self.dev_port10)


class TestEmptyEcmpGroup:
    """Verifies that packets routed to an empty ECMP group are dropped."""

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dev_port10 = 10
        request.cls.route_ip = "10.10.10.1/32"
        request.cls.vrf_oid = topology.default_vrf
        request.cls.nhop_group = npu.create(
            SaiObjType.NEXT_HOP_GROUP,
            [
                "SAI_NEXT_HOP_GROUP_ATTR_TYPE",
                "SAI_NEXT_HOP_GROUP_TYPE_ECMP",
            ],
        )
        npu.create_route(
            request.cls.route_ip,
            request.cls.vrf_oid,
            request.cls.nhop_group,
        )
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove_route(request.cls.route_ip, request.cls.vrf_oid)
        npu.remove(request.cls.nhop_group)
        npu._topo_initialized = False

    def test_empty_ecmp_group(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )

        send_packet(dataplane, self.dev_port10, pkt)
        verify_no_other_packets(dataplane, timeout=3)


class TestSviNeighbor:
    """Verifies routed forwarding through a neighbor on a VLAN SVI."""

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return
        if len(npu.port_oids) <= 26:
            pytest.skip("SviNeighborTest requires physical port indices 24–26 (27 ports)")

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dev_port10 = 10
        request.cls.dev_port24 = 24
        request.cls.vrf_oid = topo.default_vrf
        request.cls.port_oids = npu.port_oids[24:27]
        request.cls.dst_macs = [
            "00:11:22:33:44:55",
            "00:22:22:33:44:55",
            "00:33:22:33:44:55",
        ]
        request.cls.nhop_ips = ["10.10.0.1", "10.10.0.2", "10.10.0.3"]
        request.cls.route_ips = [
            "10.10.10.1/32",
            "10.10.10.2/32",
            "10.10.10.3/32",
        ]

        request.cls.bridge_ports = [
            npu.create(
                SaiObjType.BRIDGE_PORT,
                [
                    "SAI_BRIDGE_PORT_ATTR_TYPE", "SAI_BRIDGE_PORT_TYPE_PORT",
                    "SAI_BRIDGE_PORT_ATTR_PORT_ID", port_oid,
                    "SAI_BRIDGE_PORT_ATTR_ADMIN_STATE", "true",
                ],
            )
            for port_oid in request.cls.port_oids
        ]
        request.cls.vlan_oid = npu.create(SaiObjType.VLAN, ["SAI_VLAN_ATTR_VLAN_ID", "100"])
        request.cls.vlan_members = [
            npu.create_vlan_member(
                request.cls.vlan_oid,
                bridge_port,
                "SAI_VLAN_TAGGING_MODE_UNTAGGED",
            )
            for bridge_port in request.cls.bridge_ports
        ]
        for port_oid in request.cls.port_oids:
            npu.set(port_oid, ["SAI_PORT_ATTR_PORT_VLAN_ID", "100"])

        request.cls.vlan_rif = npu.create(
            SaiObjType.ROUTER_INTERFACE,
            [
                "SAI_ROUTER_INTERFACE_ATTR_VIRTUAL_ROUTER_ID", request.cls.vrf_oid,
                "SAI_ROUTER_INTERFACE_ATTR_TYPE", "SAI_ROUTER_INTERFACE_TYPE_VLAN",
                "SAI_ROUTER_INTERFACE_ATTR_VLAN_ID", request.cls.vlan_oid,
            ],
        )

        request.cls.neighbor_keys = []
        request.cls.nhops = []
        for nhop_ip, dst_mac, route_ip in zip(
            request.cls.nhop_ips,
            request.cls.dst_macs,
            request.cls.route_ips):
            neighbor_key = _neighbor_entry_key(npu, request.cls.vlan_rif, nhop_ip)
            npu.create(neighbor_key, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", dst_mac])
            nhop = npu.create(
                SaiObjType.NEXT_HOP,
                [
                    "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                    "SAI_NEXT_HOP_ATTR_IP", nhop_ip,
                    "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", request.cls.vlan_rif,
                ],
            )
            npu.create_route(route_ip, request.cls.vrf_oid, nhop)
            request.cls.neighbor_keys.append(neighbor_key)
            request.cls.nhops.append(nhop)

        for dst_mac, bridge_port in zip(request.cls.dst_macs, request.cls.bridge_ports):
            npu.create_fdb(request.cls.vlan_oid, dst_mac, bridge_port)

        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        for dst_mac in reversed(request.cls.dst_macs):
            npu.remove_fdb(request.cls.vlan_oid, dst_mac)
        for route_ip in reversed(request.cls.route_ips):
            npu.remove_route(route_ip, request.cls.vrf_oid)
        for nhop in reversed(request.cls.nhops):
            npu.remove(nhop)
        for neighbor_key in reversed(request.cls.neighbor_keys):
            npu.remove(neighbor_key)
        npu.remove(request.cls.vlan_rif)
        for port_oid in request.cls.port_oids:
            npu.set(port_oid, ["SAI_PORT_ATTR_PORT_VLAN_ID", "0"])
        for vlan_member in reversed(request.cls.vlan_members):
            npu.remove(vlan_member)
        npu.remove(request.cls.vlan_oid)
        for bridge_port in reversed(request.cls.bridge_ports):
            npu.remove(bridge_port)
        npu._topo_initialized = False

    def test_svi_neighbor(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.route_ips[0].split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt = simple_tcp_packet(
            eth_dst=self.dst_macs[0],
            eth_src=self.router_mac,
            ip_dst=self.route_ips[0].split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=63,
        )

        send_packet(dataplane, self.dev_port10, pkt)
        verify_packets(dataplane, exp_pkt, [self.dev_port24])


class TestCpuForward:
    """Verifies route forwarding to the CPU with an IP2ME hostif trap."""

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dev_port10 = 10
        request.cls.route_ip = "10.10.10.1/32"
        request.cls.vrf_oid = topo.default_vrf
        request.cls.cpu_port = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_CPU_PORT", "oid:0x0"], False)[1].oid()
        npu.create_route(request.cls.route_ip, request.cls.vrf_oid, request.cls.cpu_port)
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove_route(request.cls.route_ip, request.cls.vrf_oid)
        npu._topo_initialized = False

    @staticmethod
    def _queue_stat(npu, queue_oid):
        return npu.get_stats(queue_oid, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()["SAI_QUEUE_STAT_PACKETS"]    

    def test_cpu_forward(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.route_ip.split("/")[0],
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )

        send_packet(dataplane, self.dev_port10, pkt)
        verify_no_other_packets(dataplane, timeout=3)

        trap_group = None
        trap = None
        try:
            trap_group = npu.create(
                "SAI_OBJECT_TYPE_HOSTIF_TRAP_GROUP",
                [
                    "SAI_HOSTIF_TRAP_GROUP_ATTR_ADMIN_STATE", "true",
                    "SAI_HOSTIF_TRAP_GROUP_ATTR_QUEUE", "4",
                ],
            )
            trap = npu.create(
                "SAI_OBJECT_TYPE_HOSTIF_TRAP",
                [
                    "SAI_HOSTIF_TRAP_ATTR_TRAP_GROUP", trap_group,
                    "SAI_HOSTIF_TRAP_ATTR_TRAP_TYPE", "SAI_HOSTIF_TRAP_TYPE_IP2ME",
                    "SAI_HOSTIF_TRAP_ATTR_PACKET_ACTION", "SAI_PACKET_ACTION_TRAP",
                ],
            )

            cpu_queue4 = npu.get_list(self.cpu_port, "SAI_PORT_ATTR_QOS_QUEUE_LIST", "oid:0x0")[4]
            pre_stats = self._queue_stat(npu, cpu_queue4)

            send_packet(dataplane, self.dev_port10, pkt)
            time.sleep(4)

            post_stats = self._queue_stat(npu, cpu_queue4)
            assert post_stats == pre_stats + 1, (
                "CPU queue4 packet counter did not increment for IP2ME trap: "
                f"pre={pre_stats}, post={post_stats}"
            )
        finally:
            if trap is not None:
                npu.remove(trap)
            if trap_group is not None:
                npu.remove(trap_group)


class TestRemoveAddNeighbor:
    """Verifies forwarding, gleaning, and recovery when a neighbor is removed and re-added."""

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(
            npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]
        ).value()
        request.cls.ipv4_addr = "10.1.1.10"
        request.cls.mac_addr = "00:10:10:10:10:10"
        request.cls.dev_port10 = 10
        request.cls.lag_dev_ports = [17, 18, 19]
        request.cls.vrf_oid = topo.default_vrf
        request.cls.rif_oid = topo.lag4_rif
        request.cls.route_ip = request.cls.ipv4_addr + "/32"
        request.cls.neighbor_key = _neighbor_entry_key(
            npu, request.cls.rif_oid, request.cls.ipv4_addr)
        npu.create(request.cls.neighbor_key, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", request.cls.mac_addr])
        request.cls.neighbor_present = True
        npu.create_route(request.cls.route_ip, request.cls.vrf_oid, request.cls.rif_oid)
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove_route(request.cls.route_ip, request.cls.vrf_oid)
        if request.cls.neighbor_present:
            npu.remove(request.cls.neighbor_key)
        npu._topo_initialized = False

    @staticmethod
    def _cpu_queue0(npu):
        cpu_port = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_CPU_PORT", "oid:0x0"], False)[1].oid()
        return npu.get_list(cpu_port, "SAI_PORT_ATTR_QOS_QUEUE_LIST", "oid:0x0")[0]

    @staticmethod
    def _queue_stat(npu, queue_oid):
        return npu.get_stats(queue_oid, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()["SAI_QUEUE_STAT_PACKETS"]    

    def test_remove_add_neighbor(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_udp_packet(
            eth_dst=self.router_mac,
            ip_dst=self.ipv4_addr,
            ip_ttl=64,
        )
        exp_pkt = simple_udp_packet(
            eth_dst=self.mac_addr,
            eth_src=self.router_mac,
            ip_dst=self.ipv4_addr,
            ip_ttl=63,
        )

        send_packet(dataplane, self.dev_port10, pkt)
        verify_packet_any_port(dataplane, exp_pkt, self.lag_dev_ports)

        npu.remove(self.neighbor_key)
        self.__class__.neighbor_present = False

        cpu_queue0 = self._cpu_queue0(npu)
        pre_stats = self._queue_stat(npu, cpu_queue0)
        send_packet(dataplane, self.dev_port10, pkt)
        verify_no_other_packets(dataplane)
        post_stats = self._queue_stat(npu, cpu_queue0)
        assert post_stats == pre_stats + 1, (
            "CPU queue0 packet counter did not increment after neighbor removal: "
            f"pre={pre_stats}, post={post_stats}"
        )

        npu.create(self.neighbor_key, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.mac_addr])
        self.__class__.neighbor_present = True

        send_packet(dataplane, self.dev_port10, pkt)
        verify_packet_any_port(dataplane, exp_pkt, self.lag_dev_ports)


class TestRouteNeighborCollision:
    """Verifies forwarding and CPU gleaning for RIF routes with and without a neighbor."""

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.src_mac = "00:22:22:22:22:22"
        request.cls.dst_mac = "00:11:22:33:44:55"
        request.cls.dev_port10 = 10
        request.cls.dev_port11 = 11
        request.cls.ip_addr = "10.10.10.1"
        request.cls.route_ip = request.cls.ip_addr + "/32"
        request.cls.vrf_oid = topo.default_vrf
        request.cls.rif_oid = topo.port10_rif
        request.cls.neighbor_key = _neighbor_entry_key(npu, request.cls.rif_oid, request.cls.ip_addr)

        npu.create(request.cls.neighbor_key,["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", request.cls.dst_mac])
        request.cls.neighbor_present = True
        request.cls.route_present = False
        request.cls.nhop = npu.create(
            SaiObjType.NEXT_HOP,
            [
                "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                "SAI_NEXT_HOP_ATTR_IP", request.cls.ip_addr,
                "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", request.cls.rif_oid,
            ],
        )
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        if request.cls.route_present:
            npu.remove_route(request.cls.route_ip, request.cls.vrf_oid)
        npu.remove(request.cls.nhop, False)
        if request.cls.neighbor_present:
            npu.remove(request.cls.neighbor_key)
        npu._topo_initialized = False

    @staticmethod
    def _cpu_queue0(npu):
        cpu_port = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_CPU_PORT", "oid:0x0"], False)[1].oid()
        return npu.get_list(cpu_port, "SAI_PORT_ATTR_QOS_QUEUE_LIST", "oid:0x0")[0]

    @staticmethod
    def _queue_stat(npu, queue_oid):
        return npu.get_stats(queue_oid, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()["SAI_QUEUE_STAT_PACKETS"]    

    def _verify_forwarding(self, dataplane, pkt, exp_pkt):
        send_packet(dataplane, self.dev_port11, pkt)
        verify_packets(dataplane, exp_pkt, [self.dev_port10])

    def _verify_cpu_glean(self, npu, dataplane, pkt, cpu_queue0):
        pre_stats = self._queue_stat(npu, cpu_queue0)
        send_packet(dataplane, self.dev_port11, pkt)
        time.sleep(4)
        post_stats = self._queue_stat(npu, cpu_queue0)
        assert post_stats == pre_stats + 1, (
            "CPU queue0 packet counter did not increment for neighbor glean: "
            f"pre={pre_stats}, post={post_stats}"
        )

    def _create_route(self, npu):
        npu.create_route(self.route_ip, self.vrf_oid, self.rif_oid)
        self.__class__.route_present = True

    def _remove_route(self, npu):
        npu.remove_route(self.route_ip, self.vrf_oid)
        self.__class__.route_present = False

    def _create_neighbor(self, npu):
        npu.create(self.neighbor_key, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dst_mac])
        self.__class__.neighbor_present = True

    def _remove_neighbor(self, npu):
        npu.remove(self.neighbor_key)
        self.__class__.neighbor_present = False

    def test_route_neighbor_collision(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=self.src_mac,
            ip_dst=self.ip_addr,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt = simple_tcp_packet(
            eth_dst=self.dst_mac,
            eth_src=self.router_mac,
            ip_dst=self.ip_addr,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=63,
        )
        cpu_queue0 = self._cpu_queue0(npu)

        self._verify_forwarding(dataplane, pkt, exp_pkt)

        self._create_route(npu)
        self._verify_forwarding(dataplane, pkt, exp_pkt)

        self._remove_route(npu)
        self._verify_forwarding(dataplane, pkt, exp_pkt)

        self._create_route(npu)
        self._verify_forwarding(dataplane, pkt, exp_pkt)

        self._remove_neighbor(npu)
        self._verify_cpu_glean(npu, dataplane, pkt, cpu_queue0)

        self._create_neighbor(npu)
        self._verify_forwarding(dataplane, pkt, exp_pkt)

        self._remove_route(npu)
        self._remove_neighbor(npu)
        send_packet(dataplane, self.dev_port11, pkt)
        verify_no_other_packets(dataplane)

        self._create_route(npu)
        self._verify_cpu_glean(npu, dataplane, pkt, cpu_queue0)

        self._create_neighbor(npu)
        self._verify_forwarding(dataplane, pkt, exp_pkt)


class L3DirBcastRouteTestHelper:
    """Shared topology and traffic checks for directed-broadcast route tests."""

    @pytest.fixture(autouse=True)
    def setup_class(self, request, npu, topology):
        topo = topology
        if npu._topo_initialized:
            return
        if len(npu.port_oids) <= 25:
            pytest.skip(
                "Directed-broadcast tests require physical port indices 24–25"
            )

        request.cls.router_mac = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_SRC_MAC_ADDRESS"]).value()
        request.cls.dev_port10 = 10
        request.cls.dev_port24 = 24
        request.cls.dev_port25 = 25
        request.cls.vrf_oid = topo.default_vrf
        request.cls.port10_rif = topo.port10_rif
        request.cls.ip_addr1 = "10.10.10.1"
        request.cls.ip_addr1_subnet = "10.10.10.0/24"
        request.cls.dmac1 = "00:11:22:33:44:55"
        request.cls.dir_bcast_ip_addr1 = "10.10.10.255"
        request.cls.dir_bcast_dmac1 = "ff:ff:ff:ff:ff:ff"
        request.cls.ip_addr2 = "20.20.20.1"
        request.cls.ip_addr2_subnet = "20.20.20.0/24"
        request.cls.dmac2 = "22:11:22:33:44:55"
        request.cls.port24_oid = npu.port_oids[24]
        request.cls.port25_oid = npu.port_oids[25]

        request.cls.port24_bp = npu.create(
            SaiObjType.BRIDGE_PORT,
            [
                "SAI_BRIDGE_PORT_ATTR_TYPE",
                "SAI_BRIDGE_PORT_TYPE_PORT",
                "SAI_BRIDGE_PORT_ATTR_PORT_ID",
                request.cls.port24_oid,
                "SAI_BRIDGE_PORT_ATTR_ADMIN_STATE",
                "true",
            ],
        )
        request.cls.port25_bp = npu.create(
            SaiObjType.BRIDGE_PORT,
            [
                "SAI_BRIDGE_PORT_ATTR_TYPE",
                "SAI_BRIDGE_PORT_TYPE_PORT",
                "SAI_BRIDGE_PORT_ATTR_PORT_ID",
                request.cls.port25_oid,
                "SAI_BRIDGE_PORT_ATTR_ADMIN_STATE",
                "true",
            ],
        )
        request.cls.vlan100 = npu.create(SaiObjType.VLAN,["SAI_VLAN_ATTR_VLAN_ID", "100"])
        request.cls.vlan100_member1 = npu.create_vlan_member(
        request.cls.vlan100, request.cls.port24_bp, "SAI_VLAN_TAGGING_MODE_UNTAGGED")
        request.cls.vlan100_member2 = npu.create_vlan_member(request.cls.vlan100, request.cls.port25_bp, "SAI_VLAN_TAGGING_MODE_UNTAGGED")
        npu.set(request.cls.port24_oid, ["SAI_PORT_ATTR_PORT_VLAN_ID", "100"])
        npu.set(request.cls.port25_oid, ["SAI_PORT_ATTR_PORT_VLAN_ID", "100"])
        npu.create_fdb(
            request.cls.vlan100,
            request.cls.dmac1,
            request.cls.port24_bp,
            entry_type="SAI_FDB_ENTRY_TYPE_STATIC",
            action="SAI_PACKET_ACTION_FORWARD",
        )
        request.cls.vlan100_rif = npu.create(
            SaiObjType.ROUTER_INTERFACE,
            [
                "SAI_ROUTER_INTERFACE_ATTR_VIRTUAL_ROUTER_ID", request.cls.vrf_oid,
                "SAI_ROUTER_INTERFACE_ATTR_TYPE", "SAI_ROUTER_INTERFACE_TYPE_VLAN",
                "SAI_ROUTER_INTERFACE_ATTR_VLAN_ID", request.cls.vlan100,
            ],
        )
        npu._topo_initialized = True

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def teardown_class(cls, request, npu):
        yield
        npu.remove(request.cls.vlan100_rif)
        npu.remove_fdb(request.cls.vlan100, request.cls.dmac1)
        npu.set(request.cls.port24_oid, ["SAI_PORT_ATTR_PORT_VLAN_ID", "0"])
        npu.set(request.cls.port25_oid, ["SAI_PORT_ATTR_PORT_VLAN_ID", "0"])
        npu.remove(request.cls.vlan100_member2)
        npu.remove(request.cls.vlan100_member1)
        npu.remove(request.cls.vlan100)
        npu.remove(request.cls.port25_bp)
        npu.remove(request.cls.port24_bp)
        npu._topo_initialized = False

    @staticmethod
    def _cpu_queue0(npu):
        cpu_port = npu.get(npu.switch_oid, ["SAI_SWITCH_ATTR_CPU_PORT", "oid:0x0"], False)[1].oid()
        return npu.get_list( cpu_port, "SAI_PORT_ATTR_QOS_QUEUE_LIST", "oid:0x0")[0]

    @staticmethod
    def _queue_stat(npu, queue_oid):
        return npu.get_stats(queue_oid, ["SAI_QUEUE_STAT_PACKETS", ""]).counters()["SAI_QUEUE_STAT_PACKETS"]

    def _verify_cpu_glean(self, npu, dataplane, ingress_port, ip_dst, eth_src):
        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src=eth_src,
            ip_dst=ip_dst,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        cpu_queue0 = self._cpu_queue0(npu)
        pre_stats = self._queue_stat(npu, cpu_queue0)
        send_packet(dataplane, ingress_port, pkt)
        time.sleep(4)
        post_stats = self._queue_stat(npu, cpu_queue0)
        assert post_stats == pre_stats + 1, (
            "CPU queue0 packet counter did not increment for directed-route glean: "
            f"pre={pre_stats}, post={post_stats}"
        )

    def traffic_trap_test1(self, npu, dataplane):
        """Verify CPU gleaning for route destinations without neighbors."""
        self._verify_cpu_glean(
            npu,
            dataplane,
            self.dev_port10,
            self.ip_addr1,
            "00:22:22:22:22:21",
        )
        self._verify_cpu_glean(
            npu,
            dataplane,
            self.dev_port24,
            self.ip_addr2,
            "00:22:22:22:22:22",
        )

    def traffic_trap_test2(self, npu, dataplane):
        """Verify CPU gleaning for unresolved hosts within routed subnets."""
        self._verify_cpu_glean(
            npu,
            dataplane,
            self.dev_port10,
            "10.10.10.2",
            "00:22:22:22:22:21",
        )
        self._verify_cpu_glean(
            npu,
            dataplane,
            self.dev_port24,
            "20.20.20.2",
            "00:22:22:22:22:22",
        )

    def traffic_test(self, dataplane):
        """Verify unicast and directed-broadcast forwarding."""
        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src="00:22:22:22:22:21",
            ip_dst=self.ip_addr1,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt = simple_tcp_packet(
            eth_dst=self.dmac1,
            eth_src=self.router_mac,
            ip_dst=self.ip_addr1,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=63,
        )
        send_packet(dataplane, self.dev_port10, pkt)
        verify_packets(dataplane, exp_pkt, [self.dev_port24])

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src="00:22:22:22:22:22",
            ip_dst=self.dir_bcast_ip_addr1,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt = simple_tcp_packet(
            eth_dst=self.dir_bcast_dmac1,
            eth_src=self.router_mac,
            ip_dst=self.dir_bcast_ip_addr1,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=63,
        )
        send_packet(dataplane, self.dev_port10, pkt)
        verify_packets(
            dataplane,
            exp_pkt,
            [self.dev_port24, self.dev_port25],
        )

        pkt = simple_tcp_packet(
            eth_dst=self.router_mac,
            eth_src="00:22:22:22:22:23",
            ip_dst=self.ip_addr2,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=64,
        )
        exp_pkt = simple_tcp_packet(
            eth_dst=self.dmac2,
            eth_src=self.router_mac,
            ip_dst=self.ip_addr2,
            ip_src="192.168.0.1",
            ip_id=105,
            ip_ttl=63,
        )
        send_packet(dataplane, self.dev_port25, pkt)
        verify_packets(dataplane, exp_pkt, [self.dev_port10])


class TestDirBcastGleanAndForward(L3DirBcastRouteTestHelper):
    """Verifies CPU gleaning before neighbor resolution and forwarding afterward."""

    def test_directed_broadcast_glean_and_forward(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        route1_created = False
        route2_created = False
        neighbor0 = None
        neighbor1 = None
        neighbor2 = None
        nhop1 = None
        nhop2 = None

        try:
            npu.create_route(self.ip_addr1_subnet, self.vrf_oid, self.vlan100_rif)
            route1_created = True
            npu.create_route(self.ip_addr2_subnet, self.vrf_oid, self.port10_rif)
            route2_created = True

            self.traffic_trap_test1(npu, dataplane)
            self.traffic_trap_test2(npu, dataplane)

            neighbor0 = _neighbor_entry_key(npu, self.vlan100_rif, self.dir_bcast_ip_addr1,
            )
            npu.create(neighbor0, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dir_bcast_dmac1])

            neighbor1 = _neighbor_entry_key(npu, self.vlan100_rif, self.ip_addr1)
            npu.create(neighbor1, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dmac1])
            nhop1 = npu.create(
                SaiObjType.NEXT_HOP,
                [
                    "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                    "SAI_NEXT_HOP_ATTR_IP", self.ip_addr1,
                    "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", self.vlan100_rif,
                ],
            )

            neighbor2 = _neighbor_entry_key(npu, self.port10_rif, self.ip_addr2)
            npu.create(neighbor2, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dmac2])
            nhop2 = npu.create(
                SaiObjType.NEXT_HOP,
                [
                    "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                    "SAI_NEXT_HOP_ATTR_IP", self.ip_addr2,
                    "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", self.port10_rif,
                ],
            )

            self.traffic_test(dataplane)
            self.traffic_trap_test2(npu, dataplane)
        finally:
            if route1_created:
                npu.remove_route(self.ip_addr1_subnet, self.vrf_oid)
            if route2_created:
                npu.remove_route(self.ip_addr2_subnet, self.vrf_oid)
            if nhop1 is not None:
                npu.remove(nhop1, False)
            if nhop2 is not None:
                npu.remove(nhop2, False)
            if neighbor1 is not None:
                npu.remove(neighbor1, False)
            if neighbor2 is not None:
                npu.remove(neighbor2, False)
            if neighbor0 is not None:
                npu.remove(neighbor0, False)


class TestDirBcastForward(L3DirBcastRouteTestHelper):
    """Verifies directed-broadcast and unicast forwarding with full neighbor/nhop config."""

    def test_directed_broadcast_forward(self, npu, dataplane):
        if not npu.run_traffic:
            pytest.skip("Traffic generation disabled")

        route1_created = False
        route2_created = False
        neighbor0 = None
        neighbor1 = None
        neighbor2 = None
        nhop1 = None
        nhop2 = None

        try:
            neighbor1 = _neighbor_entry_key(npu, self.vlan100_rif, self.ip_addr1)
            npu.create(neighbor1, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dmac1])
            nhop1 = npu.create(
                SaiObjType.NEXT_HOP,
                [
                    "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                    "SAI_NEXT_HOP_ATTR_IP", self.ip_addr1,
                    "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", self.vlan100_rif,
                ],
            )

            neighbor2 = _neighbor_entry_key(npu, self.port10_rif, self.ip_addr2)
            npu.create(neighbor2, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dmac2])
            nhop2 = npu.create(
                SaiObjType.NEXT_HOP,
                [
                    "SAI_NEXT_HOP_ATTR_TYPE", "SAI_NEXT_HOP_TYPE_IP",
                    "SAI_NEXT_HOP_ATTR_IP", self.ip_addr2,
                    "SAI_NEXT_HOP_ATTR_ROUTER_INTERFACE_ID", self.port10_rif,
                ],
            )

            neighbor0 = _neighbor_entry_key(npu, self.vlan100_rif, self.dir_bcast_ip_addr1)
            npu.create(neighbor0, ["SAI_NEIGHBOR_ENTRY_ATTR_DST_MAC_ADDRESS", self.dir_bcast_dmac1])
            npu.create_route(self.ip_addr1_subnet, self.vrf_oid, self.vlan100_rif)
            route1_created = True
            npu.create_route(self.ip_addr2_subnet, self.vrf_oid, self.port10_rif)
            route2_created = True

            self.traffic_test(dataplane)
            self.traffic_trap_test2(npu, dataplane)
        finally:
            if route1_created:
                npu.remove_route(self.ip_addr1_subnet, self.vrf_oid)
            if route2_created:
                npu.remove_route(self.ip_addr2_subnet, self.vrf_oid)
            if nhop1 is not None:
                npu.remove(nhop1, False)
            if nhop2 is not None:
                npu.remove(nhop2, False)
            if neighbor1 is not None:
                npu.remove(neighbor1, False)
            if neighbor2 is not None:
                npu.remove(neighbor2, False)
            if neighbor0 is not None:
                npu.remove(neighbor0, False)

