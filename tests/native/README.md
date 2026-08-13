# Native tests

This directory contains native pytest-based SAI Challenger tests migrated from the legacy SAI PTF suite. The tests use SAI Challenger fixtures and APIs for device configuration while retaining PTF/Scapy for dataplane packet generation and verification.

Unlike legacy PTF tests, these tests configure SAI objects directly through the SAI Challenger `npu` fixture and use pytest fixtures for setup, cleanup, and failure recovery. The same test code can run through Redis or Thrift and across virtual or hardware testbeds; only the testbed configuration changes. Traffic verification remains optional, so control-plane checks can run without a dataplane connection.

## Test files

- `test_fdb.py` — static and dynamic FDB entries, learning modes, MAC moves, aging, flush operations, miss actions, attributes, and FDB events.
- `test_vlan.py` — VLAN forwarding, tagging, flooding, pruning, statistics, member management, ACL binding, learning, and negative scenarios.
- `test_route.py` — IPv4/IPv6 route forwarding, drop and update behavior, ingress RIF selection, ECMP, SVI neighbors, CPU forwarding, route/neighbor collisions, and directed broadcast routes.

All modules use the shared `sai_ptf_topology`, support a single NPU per testbed, and recover the topology after a previous test failure. Tests that only validate SAI objects or attributes can run without traffic enabled.

## Shared topology

The [sai_ptf_topology](../../topologies/sai_ptf_topology.py) fixture provides the common L2/L3 layout and aliases such as `topology.vlan10`, `topology.port10_rif`, and `topology.lag3_rif`.


| Ports | Configuration                           | Purpose                    |
| ----- | --------------------------------------- | -------------------------- |
| 0–1   | VLAN 10: port 0 untagged, port 1 tagged | L2 VLAN and FDB tests      |
| 2–3   | VLAN 20: port 2 untagged, port 3 tagged | Additional VLAN paths      |
| 4–6   | `lag1`, untagged member of VLAN 10      | L2 LAG and FDB tests       |
| 7–9   | `lag2`, untagged member of VLAN 20      | Additional LAG paths       |
| 10–13 | Individual port RIFs                    | Route ingress and egress   |
| 14–16 | `lag3` with `lag3_rif`                  | Routed LAG path            |
| 17–19 | `lag4` with `lag4_rif`                  | Additional routed LAG path |
| 20–21 | VLAN 30 with `vlan30_rif`               | SVI routing                |
| 22–23 | `lag5`, untagged member of VLAN 30      | SVI over LAG               |
| 24–31 | Unassigned by the base topology         | Temporary test resources   |


The fixture installs default IPv4 and IPv6 drop routes, exposes CPU queue counters, and restores the original default VLAN and bridge-port state during teardown. Tests must remove their own objects in reverse dependency order.

## Running the tests

Before the first standalone SAIVS run, build the image and start the container:

```
./build.sh -a trident2 -t saivs
./run.sh -a trident2 -t saivs
```

See the [standalone mode guide](../../docs/standalone_mode.md) for additional
build options.

Run all three modules:

```sh
./exec.sh --no-tty pytest --testbed=saivs_standalone -s -v native/
```

## Legacy references

- `SAI/ptf/saifdb.py`
- `SAI/ptf/saivlan.py`
- `SAI/ptf/sairoute.py`

