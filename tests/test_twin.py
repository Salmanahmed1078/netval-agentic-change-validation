"""Verification of the digital twin against known networking behaviour (run: .venv/bin/python -m pytest -q)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

from netval.model import parse, render, AclEntry, RmEntry
from netval.topologies import campus, datacenter, isp
from netval.twin import Twin
from netval.cases import Net
from netval.dsl import execute, check_invariant
from netval.oracle import profile, oracle


@pytest.fixture(scope="module")
def C():
    return campus()


def test_config_round_trip_all_devices():
    for fn in (campus, datacenter, isp):
        s = fn()
        for name, txt in s.configs.items():
            assert render(parse(txt)) == txt, name


def test_baseline_invariants_hold():
    for fn in (campus, datacenter, isp):
        tw = Twin(fn())
        for inv in ("no_loops", "mgmt_reachable", "isolation", "no_leak"):
            assert check_invariant(inv, tw)[0], (fn.__name__, inv)


def test_acl_first_match_semantics(C):
    tw = Twin(C)
    assert tw.allowed("staff1", "web1", "tcp/443")          # seq 10 permit
    assert not tw.allowed("staff1", "payroll1", "tcp/443")  # seq 40 deny before permit any
    assert tw.allowed("fin1", "payroll1", "tcp/443")
    assert not tw.allowed("fin1", "payroll1", "tcp/22")     # payroll outbound ACL implicit deny


def test_shadowed_rule_has_no_effect(C):
    n = Net(C)
    n.d["dist1"].acls["ACL_STAFF_IN"].append(AclEntry(55, "permit", "icmp", "10.1.10.0/24", "10.99.0.0/24"))
    assert not Twin(n.snapshot()).allowed("staff1", "noc1", "icmp")
    n = Net(C)
    n.d["dist1"].acls["ACL_STAFF_IN"].append(AclEntry(45, "permit", "icmp", "10.1.10.0/24", "10.99.0.0/24"))
    assert Twin(n.snapshot()).allowed("staff1", "noc1", "icmp")


def test_mtu_mismatch_breaks_ospf_adjacency(C):
    n = Net(C)
    a, b = n.link_if("core1", "core2")
    n.d["core1"].interfaces[a].mtu = 9000
    st, why = Twin(n.snapshot()).adjacency("core1", "core2")
    assert st == "down" and "ExStart" in why


def test_consistent_low_mtu_is_invisible_to_tier1_but_caught_by_tier2(C):
    n = Net(C)
    a, b = n.link_if("core1", "srv1")
    n.d["core1"].interfaces[a].mtu = 1400
    n.d["srv1"].interfaces[b].mtu = 1400
    tw0, tw = Twin(C), Twin(n.snapshot())
    assert tw.adjacency("core1", "srv1")[0] == "up"
    t = {"t": "reach", "src": "staff1", "dst": "web1", "svc": "tcp/443", "expect": "unchanged"}
    assert execute(t, tw0, tw)["status"] == "pass"                       # Tier 1 sees nothing
    probes = [tw.ping(h, "web1", 1500)["ok"] for h in ("staff1", "fin1", "lab1")]
    assert not all(probes)                                                  # Tier 2 DF probe black-holed


def test_timer_mismatch_and_bfd_convergence(C):
    tw = Twin(C)
    assert tw.convergence("core1", "dist1")["seconds"] < 3                  # BFD on
    n = Net(C)
    a, b = n.link_if("core1", "dist1")
    for d, i in (("core1", a), ("dist1", b)):
        n.d[d].interfaces[i].bfd = False
    assert Twin(n.snapshot()).convergence("core1", "dist1")["seconds"] >= 40   # dead interval
    n = Net(C)
    n.d["core1"].interfaces[a].hello = 5
    assert Twin(n.snapshot()).adjacency("core1", "dist1")[0] == "down"


def test_nat_needed_for_internet_and_return_path(C):
    tw = Twin(C)
    assert tw.allowed("staff1", "ext-web", "tcp/443")       # 10.1/16 NATed on edge1
    assert not tw.allowed("noc1", "ext-web", "tcp/443")     # not NATed -> no return route
    assert tw.reach("noc1", "ext-web", "tcp/443")["disp"] == "NO_RETURN"


def test_static_loop_detected(C):
    n = Net(C)
    n.d["core2"].statics.append(["10.20.2.0/24", n.ip_on("dist1", "core2")])
    tw = Twin(n.snapshot())
    assert "LOOP" in tw.reach("staff1", "web1", "icmp")["disp"]
    assert not check_invariant("no_loops", tw)[0]


def test_bgp_route_map_leak_detected():
    s = isp()
    n = Net(s)
    n.d["PIT"].route_maps["RM_TO_PEER"].append(RmEntry(20, "permit", None))
    assert not check_invariant("no_leak", Twin(n.snapshot()))[0]


def test_oracle_labels_drain_overload_unsafe_and_drain_safe(C):
    n = Net(C)
    a, b = n.link_if("core1", "dist2")
    n.d["core1"].interfaces[a].ospf_cost = 65535
    n.d["dist2"].interfaces[b].ospf_cost = 65535
    o = oracle(profile(C), profile(n.snapshot()), {"drained": [["core1", "dist2"]]}, C.policy)
    assert o["label"] == "unsafe" and any("%" in r for r in o["reasons"])


def test_ecmp_split_load():
    tw = Twin(datacenter())
    assert abs(tw.link_util("leaf6", "spine1") - tw.link_util("leaf6", "spine2")) < 1e-6
