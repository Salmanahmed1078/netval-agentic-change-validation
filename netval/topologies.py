"""The three lab topologies used in Phase 3 (campus, data-centre leaf-spine, ISP).

Every topology is produced as a Snapshot: rendered device configs, host
inventory, physical links, the operator's intent policy (invariants + SLAs)
and a telemetry traffic matrix.
"""
from __future__ import annotations

import ipaddress
from typing import Dict, List

from .model import (Device, Iface, Ospf, Bgp, Neighbor, AclEntry, PlEntry, RmEntry, Host,
                    build_snapshot, Snapshot)


class Builder:
    def __init__(self, p2p_base: str):
        self.d: Dict[str, Device] = {}
        self.hosts: List[Host] = []
        self.links: List[List[str]] = []
        self._p2p = ipaddress.ip_network(p2p_base).subnets(new_prefix=30)
        self._lo = 0

    def _next_if(self, dev: Device, prefix="eth"):
        n = 1
        while f"{prefix}{n}" in dev.interfaces:
            n += 1
        return f"{prefix}{n}"

    def router(self, name, lo, ospf=True, asn=None, spf=(50, 200, 5000)):
        d = Device(name, "router")
        d.interfaces["lo"] = Iface("lo", ip=f"{lo}/32", ospf_area=0 if ospf else None)
        if ospf:
            d.ospf = Ospf(router_id=lo, spf=list(spf))
        if asn:
            d.bgp = Bgp(asn)
        self.d[name] = d
        return d

    def switch(self, name):
        d = Device(name, "switch")
        self.d[name] = d
        return d

    def link(self, a, b, bw=10000, cost=10, ospf=True, bfd=True, mtu=1500):
        sub = next(self._p2p)
        h = list(sub.hosts())
        A, B = self.d[a], self.d[b]
        ia, ib = self._next_if(A), self._next_if(B)
        for dev, ifn, ip, peer in ((A, ia, h[0], b), (B, ib, h[1], a)):
            dev.interfaces[ifn] = Iface(ifn, desc=f"to {peer}", ip=f"{ip}/30", bandwidth=bw, mtu=mtu,
                                        ospf_area=0 if ospf else None, ospf_cost=cost, bfd=bfd and ospf)
        self.links.append([a, ia, b, ib])
        return ia, ib, str(h[0]), str(h[1])

    def routed_segment(self, dev, prefix, hosts, ospf=True, bw=10000):
        D = self.d[dev]
        n = ipaddress.ip_network(prefix)
        ifn = self._next_if(D)
        gw = str(n.network_address + 1)
        D.interfaces[ifn] = Iface(ifn, desc="host segment", ip=f"{gw}/{n.prefixlen}", bandwidth=bw,
                                  ospf_area=0 if ospf else None, passive=ospf)
        for k, (hn, grp, *mtu) in enumerate(hosts):
            self.hosts.append(Host(hn, f"{n.network_address + 10 + k}/{n.prefixlen}", gw, [dev, ifn], grp,
                                   mtu[0] if mtu else 1500))
        return ifn

    def svi(self, dev, vlan, prefix):
        n = ipaddress.ip_network(prefix)
        gw = str(n.network_address + 1)
        self.d[dev].interfaces[f"vlan{vlan}"] = Iface(f"vlan{vlan}", desc=f"vlan {vlan} gateway",
                                                     ip=f"{gw}/{n.prefixlen}", ospf_area=0, passive=True)
        return gw

    def trunk(self, sw, rtr, vlans, bw=1000):
        S, R = self.d[sw], self.d[rtr]
        isw, ir = self._next_if(S, "ge"), self._next_if(R)
        S.interfaces[isw] = Iface(isw, desc=f"uplink to {rtr}", sw_mode="trunk", trunk_vlans=list(vlans), bandwidth=bw)
        R.interfaces[ir] = Iface(ir, desc=f"trunk to {sw}", sw_mode="trunk", trunk_vlans=list(vlans), bandwidth=bw)
        self.links.append([sw, isw, rtr, ir])

    def access_host(self, sw, hname, vlan, ip, gw, group, mtu=1500):
        S = self.d[sw]
        p = self._next_if(S, "ge")
        S.interfaces[p] = Iface(p, desc=f"host {hname}", sw_mode="access", access_vlan=vlan)
        self.hosts.append(Host(hname, ip, gw, [sw, p], group, mtu))

    def acl(self, dev, name, lines):
        self.d[dev].acls[name] = [AclEntry(10 * (k + 1), *l) if len(l) == 4 else AclEntry(10 * (k + 1), *l)
                                  for k, l in enumerate(lines)]

    def pl(self, dev, name, prefixes, le=None):
        self.d[dev].prefix_lists[name] = [PlEntry(5 * (k + 1), "permit", p, le=le) for k, p in enumerate(prefixes)]

    def rm(self, dev, name, pl):
        self.d[dev].route_maps[name] = [RmEntry(10, "permit", pl)]

    def ebgp(self, a, b, a_out=None, b_in=None, b_out=None, a_in=None, bw=10000, mtu=1500):
        ia, ib, ipa, ipb = self.link(a, b, bw=bw, ospf=False, mtu=mtu)
        A, B = self.d[a], self.d[b]
        A.bgp.neighbors.append(Neighbor(ipb, B.bgp.asn, a_in, a_out))
        B.bgp.neighbors.append(Neighbor(ipa, A.bgp.asn, b_in, b_out))
        return ia, ib, ipa, ipb


# =========================================================================== campus
def campus() -> Snapshot:
    b = Builder("10.0.1.0/24")
    for i, n in enumerate(["core1", "core2", "dist1", "dist2", "srv1"], 1):
        b.router(n, f"10.255.1.{i}")
    b.router("edge1", "10.255.1.9", asn=65010)
    b.router("inet", "203.0.113.254", ospf=False, asn=64500)
    for s in ("acc1", "acc2", "acc3"):
        b.switch(s)
    b.link("core1", "core2")
    b.link("core1", "dist1")
    b.link("core2", "dist1")
    b.link("core1", "dist2")
    b.link("core2", "dist2", bw=1000)
    b.link("core1", "srv1")
    b.link("core2", "srv1")
    b.link("core1", "edge1", bw=1000)
    b.link("core2", "edge1", bw=1000)
    # edge1 <-> inet : eBGP, NAT outside
    D, I = b.d["edge1"], b.d["inet"]
    D.bgp.networks.append("198.51.100.0/24")
    D.statics.append(["198.51.100.0/24", "null0"])
    ia, ib, ipa, ipb = b.ebgp("edge1", "inet", a_out="RM_TO_ISP", bw=10000)
    D.interfaces[ia].nat = "outside"
    for n, x in D.interfaces.items():
        if n not in (ia, "lo"):
            x.nat = "inside"
    D.statics.append(["0.0.0.0/0", ipb])
    D.ospf.default_originate = True
    b.pl("edge1", "PL_PUBLIC", ["198.51.100.0/24"])
    b.rm("edge1", "RM_TO_ISP", "PL_PUBLIC")
    b.acl("edge1", "NAT_INSIDE", [("permit", "ip", "10.1.0.0/16", "any")])
    D.nat.append(["NAT_INSIDE", ia])
    I.bgp.networks += ["8.8.8.0/24", "192.0.2.0/24"]
    b.routed_segment("inet", "8.8.8.0/24", [("ext-web", "internet")], ospf=False)
    b.routed_segment("inet", "192.0.2.0/24", [("partner1", "partner")], ospf=False)
    # user access
    g10 = b.svi("dist1", 10, "10.1.10.0/24")
    g20 = b.svi("dist1", 20, "10.1.20.0/24")
    g30 = b.svi("dist2", 30, "10.1.30.0/24")
    g15 = b.svi("dist2", 15, "10.1.15.0/24")
    b.trunk("acc1", "dist1", [10, 20])
    b.trunk("acc2", "dist1", [10, 20])
    b.trunk("acc3", "dist2", [15, 30])
    b.access_host("acc1", "staff1", 10, "10.1.10.10/24", g10, "staff")
    b.access_host("acc1", "printer1", 10, "10.1.10.50/24", g10, "staff")
    b.access_host("acc1", "fin1", 20, "10.1.20.10/24", g20, "finance")
    b.access_host("acc2", "fin2", 20, "10.1.20.11/24", g20, "finance")
    b.access_host("acc2", "staff2", 10, "10.1.10.11/24", g10, "staff")
    b.access_host("acc3", "guest1", 30, "10.1.30.10/24", g30, "guest")
    b.access_host("acc3", "kiosk1", 30, "10.1.30.11/24", g30, "guest")
    b.access_host("acc3", "lab1", 15, "10.1.15.10/24", g15, "lab")
    # servers + noc
    pay = b.routed_segment("srv1", "10.20.1.0/24", [("payroll1", "payroll")])
    b.routed_segment("srv1", "10.20.2.0/24", [("web1", "web")])
    b.routed_segment("srv1", "10.20.3.0/24", [("dns1", "dns")])
    b.routed_segment("core2", "10.99.0.0/24", [("noc1", "noc")])
    b.d["srv1"].statics.append(["10.20.99.0/24", "null0"])      # reserved range, local discard
    # policies
    b.acl("dist1", "ACL_STAFF_IN", [
        ("permit", "tcp", "10.1.10.0/24", "10.20.2.0/24", 443), ("permit", "tcp", "10.1.10.0/24", "10.20.2.0/24", 80),
        ("permit", "udp", "10.1.10.0/24", "10.20.3.0/24", 53), ("deny", "ip", "10.1.10.0/24", "10.20.1.0/24"),
        ("deny", "ip", "10.1.10.0/24", "10.99.0.0/24"), ("permit", "ip", "any", "any")])
    b.acl("dist1", "ACL_FIN_IN", [
        ("permit", "tcp", "10.1.20.0/24", "10.20.1.0/24", 443), ("permit", "udp", "10.1.20.0/24", "10.20.3.0/24", 53),
        ("deny", "ip", "10.1.20.0/24", "10.99.0.0/24"), ("permit", "ip", "any", "any")])
    b.acl("dist2", "ACL_GUEST_IN", [
        ("permit", "udp", "10.1.30.0/24", "10.20.3.0/24", 53), ("deny", "ip", "10.1.30.0/24", "10.0.0.0/8"),
        ("permit", "ip", "any", "any")])
    b.acl("dist2", "ACL_LAB_IN", [
        ("deny", "ip", "10.1.15.0/24", "10.20.1.0/24"), ("deny", "ip", "10.1.15.0/24", "10.1.20.0/24"),
        ("permit", "ip", "any", "any")])
    b.acl("srv1", "ACL_PAYROLL_OUT", [
        ("permit", "tcp", "10.1.20.0/24", "10.20.1.0/24", 443), ("permit", "tcp", "10.99.0.0/24", "10.20.1.0/24", 22),
        ("permit", "icmp", "10.99.0.0/24", "10.20.1.0/24"), ("deny", "ip", "any", "any")])
    b.d["dist1"].interfaces["vlan10"].acl_in = "ACL_STAFF_IN"
    b.d["dist1"].interfaces["vlan20"].acl_in = "ACL_FIN_IN"
    b.d["dist2"].interfaces["vlan30"].acl_in = "ACL_GUEST_IN"
    b.d["dist2"].interfaces["vlan15"].acl_in = "ACL_LAB_IN"
    b.d["srv1"].interfaces[pay].acl_out = "ACL_PAYROLL_OUT"
    policy = {
        "name": "campus",
        "invariants": {
            "no_loops": True,
            "mgmt_reachable": {"src": "noc1", "targets": ["core1", "core2", "dist1", "dist2", "srv1", "edge1"]},
            "isolation": [["guest1", "payroll1"], ["guest1", "fin1"], ["guest1", "noc1"], ["guest1", "staff1"],
                          ["lab1", "payroll1"], ["staff1", "payroll1"]],
            "no_leak": {"externals": ["inet"], "forbidden": ["10.0.0.0/8"]},
        },
        "sla": {"convergence_max_s": 3.0, "link_util_max_pct": 90.0},
        "redundant_links": [["core1", "dist1"], ["core2", "dist1"], ["core1", "dist2"], ["core2", "dist2"],
                            ["core1", "srv1"], ["core2", "srv1"], ["core1", "edge1"], ["core2", "edge1"]],
    }
    traffic = [["staff1", "web1", 300], ["fin1", "payroll1", 100], ["staff2", "ext-web", 300],
               ["guest1", "ext-web", 450], ["kiosk1", "ext-web", 250], ["lab1", "web1", 300],
               ["fin2", "web1", 100], ["printer1", "dns1", 20]]
    return build_snapshot(b.d, b.hosts, b.links, policy, traffic)


# =========================================================================== data centre
def datacenter() -> Snapshot:
    b = Builder("10.0.2.0/24")
    b.router("spine1", "10.255.2.1")
    b.router("spine2", "10.255.2.2")
    for k in range(1, 7):
        b.router(f"leaf{k}", f"10.255.2.{10 + k}")
    b.router("border1", "10.255.2.21", asn=65020)
    b.router("border2", "10.255.2.22", asn=65020)
    b.router("oob", "10.255.2.30")
    b.router("wan", "198.18.255.1", ospf=False, asn=64600)
    for k in range(1, 7):
        bw = 10000 if k == 6 else 40000
        b.link(f"leaf{k}", "spine1", bw=bw)
        b.link(f"leaf{k}", "spine2", bw=bw)
    for s in ("spine1", "spine2"):
        b.link("border1", s, bw=100000)
        b.link("border2", s, bw=100000)
        b.link("oob", s, bw=1000)
    W = b.d["wan"]
    W.bgp.networks.append("198.18.0.0/24")
    b.routed_segment("wan", "198.18.0.0/24", [("wan-user1", "wan")], ospf=False)
    for br in ("border1", "border2"):
        b.ebgp(br, "wan", a_out="RM_TO_WAN", a_in="RM_FROM_WAN", bw=10000)
        b.pl(br, "PL_PUBLIC", ["10.101.1.0/24", "10.201.1.0/24"])
        b.pl(br, "PL_WAN", ["198.18.0.0/24"])
        b.rm(br, "RM_TO_WAN", "PL_PUBLIC")
        b.rm(br, "RM_FROM_WAN", "PL_WAN")
        b.rm(br, "RM_BGP2OSPF", "PL_WAN")
        b.d[br].bgp.redistribute.append(["ospf", "RM_TO_WAN"])
        b.d[br].ospf.redistribute.append(["bgp", "RM_BGP2OSPF"])
    # tenants (leaf = L3 gateway with access ports)
    def leaf_vlan(leaf, vlan, prefix, hosts, acl_in=None, acl_out=None):
        gw = b.svi(leaf, vlan, prefix)
        n = ipaddress.ip_network(prefix)
        L = b.d[leaf]
        L.interfaces[f"vlan{vlan}"].mtu = 9000
        for k, (hn, grp) in enumerate(hosts):
            p = b._next_if(L, "ge")
            L.interfaces[p] = Iface(p, desc=f"server {hn}", sw_mode="access", access_vlan=vlan)
            b.hosts.append(Host(hn, f"{n.network_address + 10 + k}/{n.prefixlen}", gw, [leaf, p], grp, 9000))
        if acl_in:
            L.interfaces[f"vlan{vlan}"].acl_in = acl_in
        if acl_out:
            L.interfaces[f"vlan{vlan}"].acl_out = acl_out
    ta = [("deny", "ip", "10.100.0.0/13", "10.200.0.0/13"), ("permit", "ip", "any", "any")]
    tb = [("deny", "ip", "10.200.0.0/13", "10.100.0.0/13"), ("permit", "ip", "any", "any")]
    for lf in ("leaf1", "leaf2", "leaf3"):
        b.acl(lf, "ACL_TENANT_A_IN", ta)
    for lf in ("leaf4", "leaf5"):
        b.acl(lf, "ACL_TENANT_B_IN", tb)
    b.acl("leaf3", "ACL_DBA_OUT", [("permit", "tcp", "10.102.1.0/24", "10.103.1.0/24", 5432),
                                   ("permit", "icmp", "10.99.0.0/24", "10.103.1.0/24"),
                                   ("permit", "tcp", "10.99.0.0/24", "10.103.1.0/24", 22), ("deny", "ip", "any", "any")])
    b.acl("leaf5", "ACL_DBB_OUT", [("permit", "tcp", "10.201.1.0/24", "10.203.1.0/24", 5432),
                                   ("permit", "icmp", "10.99.0.0/24", "10.203.1.0/24"),
                                   ("permit", "tcp", "10.99.0.0/24", "10.203.1.0/24", 22), ("deny", "ip", "any", "any")])
    leaf_vlan("leaf1", 110, "10.101.1.0/24", [("web-a1", "tenantA-web")], "ACL_TENANT_A_IN")
    leaf_vlan("leaf1", 111, "10.101.2.0/24", [("stage-a1", "tenantA-stage")], "ACL_TENANT_A_IN")
    leaf_vlan("leaf2", 120, "10.102.1.0/24", [("app-a1", "tenantA-app")], "ACL_TENANT_A_IN")
    leaf_vlan("leaf3", 130, "10.103.1.0/24", [("db-a1", "tenantA-db")], "ACL_TENANT_A_IN", "ACL_DBA_OUT")
    leaf_vlan("leaf4", 210, "10.201.1.0/24", [("web-b1", "tenantB-web")], "ACL_TENANT_B_IN")
    leaf_vlan("leaf4", 211, "10.201.2.0/24", [("stage-b1", "tenantB-stage")], "ACL_TENANT_B_IN")
    leaf_vlan("leaf5", 230, "10.203.1.0/24", [("db-b1", "tenantB-db")], "ACL_TENANT_B_IN", "ACL_DBB_OUT")
    leaf_vlan("leaf6", 300, "10.30.0.0/24", [("dns-dc1", "shared-dns")])
    leaf_vlan("leaf6", 310, "10.31.0.0/24", [("backup1", "shared-backup")])
    b.routed_segment("oob", "10.99.0.0/24", [("noc1", "noc")], bw=1000)
    for lf, pfx in (("leaf2", "10.102.99.0/24"), ("leaf4", "10.201.99.0/24"), ("leaf6", "10.30.99.0/24")):
        b.d[lf].statics.append([pfx, "null0"])
    policy = {
        "name": "datacenter",
        "invariants": {
            "no_loops": True,
            "mgmt_reachable": {"src": "noc1", "targets": ["spine1", "spine2", "leaf1", "leaf2", "leaf3", "leaf4",
                                                          "leaf5", "leaf6", "border1", "border2"]},
            "isolation": [["web-a1", "web-b1"], ["app-a1", "db-b1"], ["web-b1", "db-a1"], ["stage-b1", "app-a1"],
                          ["wan-user1", "db-a1"], ["wan-user1", "noc1"]],
            "no_leak": {"externals": ["wan"], "forbidden": ["10.99.0.0/24", "10.103.1.0/24", "10.203.1.0/24",
                                                            "10.0.0.0/16"]},
        },
        "sla": {"convergence_max_s": 3.0, "link_util_max_pct": 90.0},
        "redundant_links": [[f"leaf{k}", s] for k in range(1, 7) for s in ("spine1", "spine2")] +
                           [["border1", "spine1"], ["border2", "spine2"]],
    }
    traffic = [["web-a1", "app-a1", 8000], ["app-a1", "db-a1", 6000, "tcp/5432"], ["web-b1", "db-b1", 5000, "tcp/5432"],
               ["wan-user1", "web-a1", 4000], ["wan-user1", "web-b1", 3000], ["db-a1", "backup1", 6000],
               ["db-b1", "backup1", 5500], ["web-a1", "dns-dc1", 300], ["stage-a1", "app-a1", 1000]]
    return build_snapshot(b.d, b.hosts, b.links, policy, traffic)


# =========================================================================== ISP (NSFNET + edges)
NSF = ["SEA", "PAO", "SDG", "SLC", "BOU", "HOU", "LNK", "CHM", "PIT", "ATL", "AAR", "ITH", "PRN", "CPK"]
NSF_EDGES = [(0, 1), (0, 2), (0, 7), (1, 2), (1, 3), (2, 5), (3, 4), (3, 10), (4, 5), (4, 6), (5, 9),
             (5, 12), (6, 7), (7, 8), (8, 9), (8, 11), (8, 13), (10, 11), (10, 13), (11, 12), (12, 13)]
SLOW = {(3, 10), (6, 7), (4, 5)}


def isp() -> Snapshot:
    b = Builder("10.0.3.0/24")
    for k, n in enumerate(NSF, 1):
        b.router(n, f"10.255.3.{k}", asn=65000 if n in ("SEA", "ATL", "PRN", "CPK", "PIT", "PAO") else None)
    for a, c in NSF_EDGES:
        b.link(NSF[a], NSF[c], bw=2500 if (a, c) in SLOW else 10000)
    b.router("cust1", "172.16.1.254", ospf=False, asn=65101)
    b.router("cust2", "172.16.2.254", ospf=False, asn=65102)
    b.router("cust3", "172.16.3.254", ospf=False, asn=65103)
    b.router("peer1", "8.8.255.1", ospf=False, asn=64701)
    b.router("peer2", "1.1.255.1", ospf=False, asn=64702)
    b.switch("noc-sw")
    # customers
    for cust, pes, pfx, host in (("cust1", ["SEA"], "172.16.1.0/24", "c1-host"),
                                 ("cust2", ["ATL"], "172.16.2.0/24", "c2-host"),
                                 ("cust3", ["PRN", "CPK"], "172.16.3.0/24", "c3-host")):
        b.d[cust].bgp.networks.append(pfx)
        b.routed_segment(cust, pfx, [(host, cust)], ospf=False)
        for pe in pes:
            n = cust[-1]
            ia, ib, ipa, ipb = b.ebgp(pe, cust, a_in=f"RM_CUST{n}_IN", bw=10000)
            b.pl(pe, f"PL_CUST{n}", [pfx])
            b.rm(pe, f"RM_CUST{n}_IN", f"PL_CUST{n}")
            b.acl(pe, f"ACL_CUST{n}_IN", [("permit", "ip", pfx, "any"), ("deny", "ip", "any", "any")])
            b.d[pe].interfaces[ia].acl_in = f"ACL_CUST{n}_IN"
            b.d[cust].statics.append(["0.0.0.0/0", ipa])
            if not any(r[0] == "bgp" for r in b.d[pe].ospf.redistribute):
                b.pl(pe, "PL_ALL_CUST", ["172.16.0.0/16"], le=24)
                b.rm(pe, "RM_BGP2OSPF", "PL_ALL_CUST")
                b.d[pe].ospf.redistribute.append(["bgp", "RM_BGP2OSPF"])
    # upstream peers
    for peer, pe, pfx, host in (("peer1", "PIT", "8.8.8.0/24", "ext-dns1"), ("peer2", "PAO", "1.1.1.0/24", "ext-dns2")):
        b.d[peer].bgp.networks.append(pfx)
        b.routed_segment(peer, pfx, [(host, "internet")], ospf=False)
        b.ebgp(pe, peer, a_in="RM_PEER_IN", a_out="RM_TO_PEER", bw=10000)
        b.pl(pe, "PL_INET", ["8.8.8.0/24", "1.1.1.0/24"])
        b.pl(pe, "PL_CUST_ALL", ["172.16.0.0/16"], le=24)
        b.rm(pe, "RM_PEER_IN", "PL_INET")
        b.rm(pe, "RM_TO_PEER", "PL_CUST_ALL")
        b.rm(pe, "RM_INET2OSPF", "PL_INET")
        b.d[pe].bgp.redistribute.append(["ospf", "RM_TO_PEER"])
        b.d[pe].ospf.redistribute.append(["bgp", "RM_INET2OSPF"])
    # NOC LAN
    g99 = b.svi("BOU", 99, "10.99.0.0/24")
    g98 = b.svi("BOU", 98, "10.98.0.0/24")
    b.trunk("noc-sw", "BOU", [98, 99], bw=1000)
    b.access_host("noc-sw", "noc1", 99, "10.99.0.10/24", g99, "noc")
    b.access_host("noc-sw", "noc2", 99, "10.99.0.11/24", g99, "noc")
    b.access_host("noc-sw", "noclab1", 98, "10.98.0.10/24", g98, "noc-lab")
    b.access_host("noc-sw", "noclab2", 98, "10.98.0.11/24", g98, "noc-lab")
    b.d["BOU"].statics.append(["10.97.0.0/16", "null0"])
    b.acl("BOU", "ACL_NOC_OUT", [("permit", "ip", "10.0.0.0/8", "10.99.0.0/24"), ("deny", "ip", "any", "any")])
    b.d["BOU"].interfaces["vlan99"].acl_out = "ACL_NOC_OUT"
    policy = {
        "name": "isp",
        "invariants": {
            "no_loops": True,
            "mgmt_reachable": {"src": "noc1", "targets": list(NSF)},
            "isolation": [["c1-host", "noc1"], ["ext-dns1", "noc1"], ["c3-host", "noc1"], ["ext-dns2", "noclab1"]],
            "no_leak": {"externals": ["peer1", "peer2", "cust1", "cust2", "cust3"],
                        "forbidden": ["10.0.0.0/8"]},
        },
        "sla": {"convergence_max_s": 3.0, "link_util_max_pct": 90.0},
        "redundant_links": [[NSF[a], NSF[c]] for a, c in NSF_EDGES],
    }
    traffic = [["c1-host", "ext-dns1", 3000], ["c2-host", "ext-dns2", 2000], ["c3-host", "ext-dns1", 3000],
               ["c1-host", "c2-host", 1500], ["c2-host", "c3-host", 1200], ["c3-host", "ext-dns2", 1500],
               ["c1-host", "ext-dns2", 1000], ["c2-host", "ext-dns1", 1800]]
    return build_snapshot(b.d, b.hosts, b.links, policy, traffic)


TOPOLOGIES = {"campus": campus, "datacenter": datacenter, "isp": isp}
