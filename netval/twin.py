"""Two-tier network digital twin.

Tier 1 (static, Batfish-style): parses every config, computes the converged
control plane (connected, static, OSPF incl. adjacency compatibility checks,
eBGP with route-maps / prefix-lists, redistribution) and answers data-plane
questions by symbolic forwarding (ACLs, NAT, ECMP, loops, null routes).
It has no notion of time, packet size or load.

Tier 2 (emulation): replays the same network as a discrete-time packet and
protocol emulation.  It adds what static analysis cannot see: packet sizes and
MTU black holes (DF probes), protocol timers / BFD / SPF throttling (time to
converge after a silent link failure) and link utilisation from the telemetry
traffic matrix.  It answers only for the concrete probes it sends.
"""
from __future__ import annotations

import heapq
import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from .model import Snapshot, Device, Iface

SERVICES = ["icmp", "tcp/22", "tcp/25", "tcp/80", "tcp/443", "tcp/5432", "udp/53"]
AD = {"connected": 0, "static": 1, "bgp": 20, "ospf": 110, "ospf_ext": 110}


@lru_cache(maxsize=None)
def net(p: str) -> ipaddress.IPv4Network:
    return ipaddress.ip_network(p, strict=False)


@lru_cache(maxsize=None)
def addr(a: str) -> int:
    return int(ipaddress.ip_address(a.split("/")[0]))


def in_net(a: int, n: ipaddress.IPv4Network) -> bool:
    return (a & int(n.netmask)) == int(n.network_address)


@dataclass
class Route:
    prefix: str
    proto: str
    metric: int = 0
    nexthops: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # (out_iface, nh_ip|None)
    null: bool = False
    as_path: Tuple[int, ...] = ()

    @property
    def ad(self):
        return AD[self.proto]


# =========================================================================== control plane
class ControlPlane:
    def __init__(self, snap: Snapshot, down_links: Tuple[Tuple[str, str], ...] = ()):
        self.s = snap
        self.dev: Dict[str, Device] = snap.devices
        self.down_extra = set(down_links)          # (dev, iface) forced down (failure emulation)
        self.peer: Dict[Tuple[str, str], Tuple[str, str]] = {}
        for a, ia, b, ib in snap.links:
            self.peer[(a, ia)] = (b, ib)
            self.peer[(b, ib)] = (a, ia)
        self.owner: Dict[int, Tuple[str, str]] = {}
        for d in self.dev.values():
            for i in d.interfaces.values():
                if i.ip and self.up(d.name, i.name):
                    self.owner[addr(i.ip)] = (d.name, i.name)
        self.adj = self._ospf_adjacencies()
        self.bgp_sessions = self._bgp_sessions()
        self.rib = self._compute_rib()
        self._fib_index = {d: self._index(r) for d, r in self.rib.items()}

    # ------------------------------------------------------------------ interfaces
    def up(self, d: str, i: str) -> bool:
        dev = self.dev.get(d)
        if dev is None or i not in dev.interfaces:
            return False
        itf = dev.interfaces[i]
        if itf.shutdown or (d, i) in self.down_extra:
            return False
        p = self.peer.get((d, i))
        if p:
            pd = self.dev.get(p[0])
            if pd is None or p[1] not in pd.interfaces:
                return False
            pi = pd.interfaces[p[1]]
            if pi.shutdown or p in self.down_extra:
                return False
        return True

    def ifc(self, d: str, i: str) -> Iface:
        return self.dev[d].interfaces[i]

    # ------------------------------------------------------------------ OSPF adjacencies
    def _ospf_adjacencies(self):
        res = {}
        for a, ia, b, ib in self.s.links:
            A, B = self.dev.get(a), self.dev.get(b)
            if not A or not B or ia not in A.interfaces or ib not in B.interfaces:
                continue
            x, y = A.interfaces[ia], B.interfaces[ib]
            if x.ospf_area is None and y.ospf_area is None:
                continue
            key = (a, ia, b, ib)
            reason = None
            if not (A.ospf and B.ospf) or x.ospf_area is None or y.ospf_area is None:
                reason = "ospf not enabled on both ends"
            elif not self.up(a, ia):
                reason = "interface down"
            elif x.passive or y.passive:
                reason = "passive interface"
            elif x.ospf_area != y.ospf_area:
                reason = "area mismatch"
            elif not x.ip or not y.ip or net(x.ip) != net(y.ip):
                reason = "subnet mismatch"
            elif x.hello != y.hello or x.dead != y.dead:
                reason = f"timer mismatch hello {x.hello}/{y.hello} dead {x.dead}/{y.dead}"
            elif x.mtu != y.mtu and not (x.mtu_ignore and y.mtu_ignore):
                reason = f"MTU mismatch {x.mtu}/{y.mtu} (stuck in ExStart)"
            res[key] = ("up" if reason is None else "down", reason)
        return res

    def adjacency_state(self, a: str, b: str) -> Tuple[str, str]:
        for (x, ix, y, iy), (st, why) in self.adj.items():
            if {x, y} == {a, b}:
                return st, why or ""
        return "none", "no ospf link between devices"

    # ------------------------------------------------------------------ BGP sessions
    def _bgp_sessions(self):
        sess = []
        for d in self.dev.values():
            if not d.bgp:
                continue
            for n in d.bgp.neighbors:
                o = self.owner.get(addr(n.ip))
                if not o:
                    continue
                pd = self.dev[o[0]]
                if not pd.bgp or pd.bgp.asn != n.remote_as:
                    continue
                # local iface on same subnet
                local = None
                for i in d.interfaces.values():
                    if i.ip and self.up(d.name, i.name) and in_net(addr(n.ip), net(i.ip)):
                        local = i
                if not local:
                    continue
                back = [m for m in pd.bgp.neighbors if addr(m.ip) == addr(local.ip) and m.remote_as == d.bgp.asn]
                if back:
                    sess.append((d.name, n, o[0], back[0], local.name))
        return sess

    def bgp_session_up(self, a: str, b: str) -> bool:
        return any(x == a and y == b for x, _, y, _, _ in self.bgp_sessions)

    # ------------------------------------------------------------------ policy helpers
    def pl_match(self, d: Device, name: str, prefix: str) -> bool:
        ents = d.prefix_lists.get(name)
        if ents is None:
            return False
        p = net(prefix)
        for e in sorted(ents, key=lambda e: e.seq):
            P = net(e.prefix)
            if not p.subnet_of(P):
                continue
            lo = e.ge if e.ge is not None else P.prefixlen
            hi = e.le if e.le is not None else (P.prefixlen if e.ge is None else 32)
            if lo <= p.prefixlen <= hi:
                return e.action == "permit"
        return False

    def rm_permit(self, d: Device, name: Optional[str], prefix: str) -> bool:
        if name is None:
            return True
        ents = d.route_maps.get(name)
        if ents is None:
            return False
        for e in sorted(ents, key=lambda e: e.seq):
            if e.match_pl is None or self.pl_match(d, e.match_pl, prefix):
                return e.action == "permit"
        return False

    # ------------------------------------------------------------------ RIB
    def _connected(self, d: Device):
        out = {}
        for i in d.interfaces.values():
            if i.ip and self.up(d.name, i.name):
                p = str(net(i.ip))
                out[p] = Route(p, "connected", 0, [(i.name, None)])
        return out

    def _statics(self, d: Device, conn):
        out = {}
        for p, nh in d.statics:
            p = str(net(p))
            if nh == "null0":
                out[p] = Route(p, "static", 0, [], null=True)
                continue
            for i in d.interfaces.values():
                if i.ip and self.up(d.name, i.name) and in_net(addr(nh), net(i.ip)) and addr(nh) != addr(i.ip):
                    out.setdefault(p, Route(p, "static", 0, []))
                    out[p].nexthops.append((i.name, nh))
                    break
        return out

    def _bgp(self, rib):
        learned = defaultdict(dict)   # dev -> prefix -> Route
        # two passes allow re-advertisement through a second eBGP hop
        for _ in range(3):
            new = defaultdict(dict)
            for (a, n, b, back, local_if) in self.bgp_sessions:
                A, B = self.dev[a], self.dev[b]
                cands = {}
                for p in A.bgp.networks:
                    p = str(net(p))
                    if p in rib[a]:
                        cands[p] = ()
                for proto, rm in A.bgp.redistribute:
                    for p, r in rib[a].items():
                        pr = "ospf" if r.proto in ("ospf", "ospf_ext") else r.proto
                        if pr == proto and self.rm_permit(A, rm, p):
                            cands.setdefault(p, ())
                for p, r in learned[a].items():
                    cands.setdefault(p, r.as_path)
                for p, path in cands.items():
                    full = (A.bgp.asn,) + path
                    if B.bgp.asn in full:
                        continue
                    if not self.rm_permit(A, n.rm_out, p):
                        continue
                    if not self.rm_permit(B, back.rm_in, p):
                        continue
                    peer_ip = self.ifc(a, local_if).ip.split("/")[0]
                    # B reaches A via its iface on the shared subnet
                    b_local = [i for i in B.interfaces.values() if i.ip and in_net(addr(peer_ip), net(i.ip))]
                    if not b_local:
                        continue
                    r = Route(p, "bgp", len(full), [(b_local[0].name, peer_ip)], as_path=full)
                    old = new[b].get(p)
                    if old is None or (len(r.as_path), r.nexthops[0][1]) < (len(old.as_path), old.nexthops[0][1]):
                        new[b][p] = r
            if {k: set(v) for k, v in new.items()} == {k: set(v) for k, v in learned.items()}:
                learned = new
                break
            learned = new
        return learned

    def _ospf(self, rib):
        routers = [d for d in self.dev.values() if d.ospf]
        edges = defaultdict(list)       # a -> [(b, cost, out_if, nh_ip)]
        for (a, ia, b, ib), (st, _) in self.adj.items():
            if st != "up":
                continue
            x, y = self.ifc(a, ia), self.ifc(b, ib)
            edges[a].append((b, x.ospf_cost, ia, y.ip.split("/")[0]))
            edges[b].append((a, y.ospf_cost, ib, x.ip.split("/")[0]))
        stubs = defaultdict(list)      # prefix -> [(router, cost)]
        ext = defaultdict(list)        # prefix -> [asbr]
        for d in routers:
            for i in d.interfaces.values():
                if i.ospf_area is not None and i.ip and self.up(d.name, i.name):
                    c = 1 if i.name.startswith("lo") else (10 if i.name.startswith("vlan") else i.ospf_cost)
                    stubs[str(net(i.ip))].append((d.name, c))
            for proto, rm in d.ospf.redistribute:
                for p, r in rib[d.name].items():
                    if r.proto in ("ospf", "ospf_ext"):
                        continue
                    if r.proto == proto and self.rm_permit(d, rm, p):
                        ext[p].append(d.name)
            if d.ospf.default_originate and "0.0.0.0/0" in rib[d.name] and rib[d.name]["0.0.0.0/0"].proto != "ospf_ext":
                ext["0.0.0.0/0"].append(d.name)
        out = {}
        for src in routers:
            dist, first = self._dijkstra(src.name, edges)
            routes = {}
            for p, advs in stubs.items():
                best, nhs = None, set()
                for r, c in advs:
                    if r == src.name or r not in dist:
                        continue
                    m = dist[r] + c
                    if best is None or m < best:
                        best, nhs = m, set(first[r])
                    elif m == best:
                        nhs |= set(first[r])
                if best is not None and nhs:
                    routes[p] = Route(p, "ospf", best, sorted(nhs))
            for p, asbrs in ext.items():
                if p in routes:
                    continue
                best, nhs = None, set()
                for r in asbrs:
                    if r == src.name or r not in dist:
                        continue
                    m = dist[r]
                    if best is None or m < best:
                        best, nhs = m, set(first[r])
                    elif m == best:
                        nhs |= set(first[r])
                if best is not None and nhs:
                    routes[p] = Route(p, "ospf_ext", 20 * 1000 + best, sorted(nhs))
            out[src.name] = routes
        return out

    @staticmethod
    def _dijkstra(src, edges):
        dist = {src: 0}
        first = {src: set()}
        pq = [(0, src)]
        done = set()
        while pq:
            d, u = heapq.heappop(pq)
            if u in done:
                continue
            done.add(u)
            for v, c, oif, nh in edges[u]:
                nd = d + c
                fh = {(oif, nh)} if u == src else first[u]
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    first[v] = set(fh)
                    heapq.heappush(pq, (nd, v))
                elif nd == dist[v]:
                    first[v] |= fh
        return dist, first

    def _compute_rib(self):
        conn = {n: self._connected(d) for n, d in self.dev.items()}
        stat = {n: self._statics(d, conn[n]) for n, d in self.dev.items()}
        rib = {n: self._merge(conn[n], stat[n]) for n in self.dev}
        prev = None
        for _ in range(6):
            bgp = self._bgp(rib)
            ospf = self._ospf(rib)
            rib = {n: self._merge(conn[n], stat[n], bgp.get(n, {}), ospf.get(n, {})) for n in self.dev}
            sig = {n: {p: (r.proto, tuple(r.nexthops)) for p, r in t.items()} for n, t in rib.items()}
            if sig == prev:
                break
            prev = sig
        return rib

    @staticmethod
    def _merge(*tables):
        out = {}
        for t in tables:
            for p, r in t.items():
                o = out.get(p)
                if o is None or (r.ad, r.metric) < (o.ad, o.metric):
                    out[p] = r
        return out

    @staticmethod
    def _index(rib):
        idx = defaultdict(dict)
        for p, r in rib.items():
            n = net(p)
            idx[n.prefixlen][int(n.network_address)] = r
        return sorted(idx.items(), key=lambda kv: -kv[0])

    def lookup(self, d: str, a: int) -> Optional[Route]:
        for plen, tbl in self._fib_index[d]:
            mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF if plen else 0
            r = tbl.get(a & mask)
            if r is not None:
                return r
        return None


def addr_str(a: str) -> str:
    return a.split("/")[0]


# =========================================================================== data plane
@dataclass
class Pkt:
    src: int
    dst: int
    proto: str
    port: Optional[int]
    size: Optional[int] = None


@dataclass
class PathResult:
    disposition: str
    hops: List[str]
    detail: str = ""
    weight: float = 1.0
    nat_src: Optional[int] = None
    links: List[Tuple[str, str]] = field(default_factory=list)   # (dev, out_iface)


def parse_service(svc: str):
    if svc == "icmp":
        return "icmp", None
    pr, port = svc.split("/")
    return pr, int(port)


class DataPlane:
    def __init__(self, cp: ControlPlane):
        self.cp = cp
        self.s = cp.s
        self.hosts = self.s.hosts
        self.host_by_ip = {addr(h.ip): h for h in self.hosts.values()}
        self._gw_cache = {}

    # ------------------------------------------------------------------ host attachment
    def gateway(self, hname: str):
        if hname in self._gw_cache:
            return self._gw_cache[hname]
        res = self._gateway(self.hosts[hname])
        self._gw_cache[hname] = res
        return res

    def _gateway(self, h):
        cp = self.cp
        d, i = h.attach
        dev = cp.dev.get(d)
        if dev is None or i not in dev.interfaces:
            return None, "attachment port missing"
        itf = dev.interfaces[i]
        if itf.shutdown:
            return None, f"access port {d}:{i} shutdown"
        cand = None
        if itf.sw_mode == "access":
            v = itf.access_vlan
            if dev.kind == "router":
                cand = (d, f"vlan{v}")
            else:
                for (pd, pi) in [cp.peer.get((d, n)) for n, x in dev.interfaces.items()
                                 if x.sw_mode == "trunk" and v in x.trunk_vlans and cp.up(d, n)]:
                    if pd is None:
                        continue
                    px = cp.dev[pd].interfaces.get(pi)
                    if px and px.sw_mode == "trunk" and v in px.trunk_vlans:
                        cand = (pd, f"vlan{v}")
                        break
                if cand is None:
                    return None, f"vlan {v} not carried on any uplink trunk from {d}"
        else:
            cand = (d, i)
        gd, gi = cand
        g = cp.dev[gd].interfaces.get(gi)
        if g is None or not g.ip or g.shutdown:
            return None, f"no gateway interface {gd}:{gi}"
        if not in_net(addr(h.ip), net(g.ip)):
            return None, f"host ip {h.ip} not in gateway subnet {g.ip}"
        if addr(h.gw) != addr(g.ip):
            return None, f"host default gateway {h.gw} does not match {g.ip}"
        return (gd, gi), ""

    def resolve(self, name: str) -> Optional[int]:
        if name in self.hosts:
            return addr(self.hosts[name].ip)
        if name in self.cp.dev:
            lo = self.cp.dev[name].interfaces.get("lo")
            if lo and lo.ip:
                return addr(lo.ip)
        return None

    # ------------------------------------------------------------------ ACL
    def acl_permits(self, d: str, acl: Optional[str], p: Pkt) -> Tuple[bool, str]:
        if not acl:
            return True, ""
        dev = self.cp.dev[d]
        ents = dev.acls.get(acl)
        if ents is None:
            return True, ""      # IOS: undefined ACL permits all
        for e in sorted(ents, key=lambda e: e.seq):
            if e.proto != "ip" and e.proto != p.proto:
                continue
            if e.src != "any" and not in_net(p.src, net(e.src)):
                continue
            if e.dst != "any" and not in_net(p.dst, net(e.dst)):
                continue
            if e.port is not None and e.port != p.port:
                continue
            return e.action == "permit", f"{acl} seq {e.seq}"
        return False, f"{acl} implicit deny"

    def nat_translate(self, d: str, in_if: Optional[str], out_if: str, p: Pkt) -> Optional[int]:
        dev = self.cp.dev[d]
        o = dev.interfaces[out_if]
        if o.nat != "outside":
            return None
        if in_if is not None and dev.interfaces[in_if].nat != "inside":
            return None
        for acl, oif in dev.nat:
            if oif != out_if:
                continue
            ents = dev.acls.get(acl, [])
            for e in sorted(ents, key=lambda e: e.seq):
                if e.src == "any" or in_net(p.src, net(e.src)):
                    if e.action == "permit":
                        return addr(o.ip)
                    break
        return None

    # ------------------------------------------------------------------ trace
    def trace(self, src_host: str, dst: int, svc: str = "icmp", size: Optional[int] = None,
              src_dev: Optional[str] = None) -> List[PathResult]:
        proto, port = parse_service(svc)
        if src_dev is not None:      # packet originated by a router (loopback)
            p = Pkt(addr(self.cp.dev[src_dev].interfaces["lo"].ip), dst, proto, port, size)
            return self._router(src_dev, None, p, [src_dev], set(), 1.0, None, [])
        h = self.hosts[src_host]
        if size is not None and size > h.mtu:
            return [PathResult("SRC_MTU", [src_host], f"probe {size} > host mtu {h.mtu}")]
        p = Pkt(addr(h.ip), dst, proto, port, size)
        gw, why = self.gateway(src_host)
        if gw is None:
            return [PathResult("HOST_ISOLATED", [src_host], why)]
        gd, gi = gw
        gif = self.cp.ifc(gd, gi)
        if in_net(dst, net(gif.ip)) and dst != addr(gif.ip):
            dh = self.host_by_ip.get(dst)
            if dh and self.gateway(dh.name)[0] == gw:
                return [PathResult("ACCEPTED", [src_host, dh.name], "same segment")]
            return [PathResult("NO_HOST", [src_host], "destination not on segment")]
        if size is not None and size > gif.mtu:
            return [PathResult("MTU_DROP", [src_host, gd], f"{gd}:{gi} mtu {gif.mtu}")]
        return self._router(gd, gi, p, [src_host, gd], set(), 1.0, None, [])

    def _router(self, d, in_if, p: Pkt, hops, visited, w, nat_src, links) -> List[PathResult]:
        cp = self.cp
        key = (d, p.dst, p.src)
        if key in visited or len(hops) > 32:
            return [PathResult("LOOP", hops, f"revisits {d}", w, nat_src, links)]
        visited = visited | {key}
        if in_if is not None:
            ok, why = self.acl_permits(d, cp.ifc(d, in_if).acl_in, p)
            if not ok:
                return [PathResult("DENIED_IN", hops, f"{d}:{in_if} {why}", w, nat_src, links)]
        own = cp.owner.get(p.dst)
        if own and own[0] == d:
            return [PathResult("ACCEPTED", hops, f"owned by {d}", w, nat_src, links)]
        r = cp.lookup(d, p.dst)
        if r is None:
            return [PathResult("NO_ROUTE", hops, f"no route on {d}", w, nat_src, links)]
        if r.null:
            return [PathResult("NULL_ROUTED", hops, f"null0 on {d} ({r.prefix})", w, nat_src, links)]
        nhs = r.nexthops[:8]
        out: List[PathResult] = []
        for oif, nh in nhs:
            wb = w / len(nhs)
            o = cp.ifc(d, oif)
            ok, why = self.acl_permits(d, o.acl_out, p)
            if not ok:
                out.append(PathResult("DENIED_OUT", hops, f"{d}:{oif} {why}", wb, nat_src, links))
                continue
            if p.size is not None and p.size > o.mtu:
                out.append(PathResult("MTU_DROP", hops, f"{d}:{oif} mtu {o.mtu} < {p.size}", wb, nat_src, links))
                continue
            p2 = p
            ns = nat_src
            t = self.nat_translate(d, in_if, oif, p)
            if t is not None:
                ns = p.src
                p2 = Pkt(t, p.dst, p.proto, p.port, p.size)
            l2 = links + [(d, oif)]
            if nh is None:     # connected
                dh = self.host_by_ip.get(p.dst)
                if dh and self.gateway(dh.name)[0] == (d, oif):
                    if p.size is not None and p.size > dh.mtu:
                        out.append(PathResult("MTU_DROP", hops + [dh.name], f"host {dh.name} mtu {dh.mtu}", wb, ns, l2))
                    else:
                        out.append(PathResult("ACCEPTED", hops + [dh.name], "", wb, ns, l2))
                    continue
                own = cp.owner.get(p.dst)
                pr = cp.peer.get((d, oif))
                if own and pr and own == pr:
                    out.extend(self._router(own[0], own[1], p2, hops + [own[0]], visited, wb, ns, l2))
                    continue
                out.append(PathResult("NO_HOST", hops, f"no host {ipaddress.ip_address(p.dst)} on {d}:{oif}", wb, ns, l2))
                continue
            nd = cp.owner.get(addr(nh))
            if nd is None:
                lb = self.host_by_ip.get(addr(nh))       # next hop is a host (e.g. load balancer VIP)
                if lb and self.gateway(lb.name)[0] == (d, oif):
                    out.append(PathResult("ACCEPTED", hops + [lb.name], f"delivered to {lb.name}", wb, ns, l2))
                    continue
            if nd is None or cp.peer.get((d, oif)) != nd:
                out.append(PathResult("NO_ROUTE", hops, f"next hop {nh} unreachable from {d}", wb, ns, l2))
                continue
            ni = cp.ifc(*nd)
            if p.size is not None and p.size > ni.mtu:
                out.append(PathResult("MTU_DROP", hops, f"{nd[0]}:{nd[1]} mtu {ni.mtu} < {p.size}", wb, ns, l2))
                continue
            out.extend(self._router(nd[0], nd[1], p2, hops + [nd[0]], visited, wb, ns, l2))
        return out

    # ------------------------------------------------------------------ reverse (stateful) check
    def reverse_ok(self, dst_name: str, back_to: int, orig_src: int) -> bool:
        """Route-only return path (ACLs are stateful) incl. NAT un-translation."""
        cp = self.cp
        if dst_name in self.hosts:
            gw, _ = self.gateway(dst_name)
            if gw is None:
                return False
            d = gw[0]
        else:
            d = dst_name
        target = back_to
        for _ in range(40):
            own = cp.owner.get(target)
            if own and own[0] == d:
                if target != orig_src:
                    target = orig_src          # NAT un-translate on the NAT router
                    continue
                return True
            r = cp.lookup(d, target)
            if r is None or r.null or not r.nexthops:
                return False
            oif, nh = r.nexthops[0]
            if nh is None:
                dh = self.host_by_ip.get(target)
                if dh and self.gateway(dh.name)[0] == (d, oif):
                    return True
                own = cp.owner.get(target)
                if own and cp.peer.get((d, oif)) == own:
                    d = own[0]
                    continue
                return False
            nd = cp.owner.get(addr(nh))
            if nd is None:
                return False
            d = nd[0]
        return False


# =========================================================================== twin facade
SUMMARY_OK = "ACCEPTED"


class Twin:
    """Facade used by tests, the agent and the baselines."""

    TIER2_BFD_DETECT = 0.9       # 3 x 300 ms
    FLOOD_PER_HOP = 0.01
    FIB_UPDATE = 0.05

    def __init__(self, snap: Snapshot):
        self.s = snap
        self.cp = ControlPlane(snap)
        self.dp = DataPlane(self.cp)
        self._reach = {}

    # ------------------------------------------------------------------ Tier 1 queries
    def endpoints(self) -> List[str]:
        return sorted(self.s.hosts)

    def reach(self, src: str, dst: str, svc: str) -> dict:
        k = (src, dst, svc)
        if k in self._reach:
            return self._reach[k]
        a = self.dp.resolve(dst)
        if a is None:
            res = {"disp": "UNKNOWN_DST", "paths": [], "detail": f"unknown destination {dst}"}
        else:
            if src in self.s.hosts:
                paths = self.dp.trace(src, a, svc)
            else:
                paths = self.dp.trace(None, a, svc, src_dev=src)
            disp = summarize(paths)
            detail = "; ".join(sorted({p.detail for p in paths if p.disposition != "ACCEPTED" and p.detail}))[:200]
            if disp == "ACCEPTED":
                src_ip = addr(self.s.hosts[src].ip) if src in self.s.hosts else addr(self.cp.dev[src].interfaces["lo"].ip)
                # translated source seen by destination
                trans = src_ip
                for p in paths:
                    if p.nat_src is not None:
                        trans = self._translated_src(p, src_ip)
                if not self.dp.reverse_ok(dst, trans, src_ip):
                    disp, detail = "NO_RETURN", "no return route"
            res = {"disp": disp, "paths": [p.hops for p in paths], "detail": detail,
                   "links": sorted({l for p in paths for l in p.links})}
        self._reach[k] = res
        return res

    def _translated_src(self, path: PathResult, src_ip: int) -> int:
        # find the NAT router on the path: first device whose outside iface did the translation
        for d, oif in path.links:
            o = self.cp.ifc(d, oif)
            if o.nat == "outside" and o.ip:
                return addr(o.ip)
        return src_ip

    def allowed(self, src, dst, svc) -> bool:
        return self.reach(src, dst, svc)["disp"] == "ACCEPTED"

    def route_present(self, dev: str, prefix: str) -> bool:
        return dev in self.cp.rib and str(net(prefix)) in self.cp.rib[dev]

    def route_table(self, dev: str):
        return self.cp.rib.get(dev, {})

    def acl_decision(self, dev: str, acl: str, src: str, dst: str, svc: str) -> str:
        sa, da = self.dp.resolve(src), self.dp.resolve(dst)
        pr, port = parse_service(svc)
        ok, _ = self.dp.acl_permits(dev, acl, Pkt(sa, da, pr, port))
        return "permit" if ok else "deny"

    def adjacency(self, a: str, b: str):
        return self.cp.adjacency_state(a, b)

    def all_adjacencies(self):
        return {f"{a}--{b}": st for (a, ia, b, ib), (st, _) in self.cp.adj.items()}

    def bgp_sessions(self):
        return sorted({tuple(sorted((a, b))) for a, _, b, _, _ in self.cp.bgp_sessions})

    def path_contains(self, src, dst, hop, svc="icmp") -> bool:
        r = self.reach(src, dst, svc)
        return bool(r["paths"]) and all(hop in p for p in r["paths"])

    # ------------------------------------------------------------------ Tier 2 emulation
    def ping(self, src: str, dst: str, size: int = 64, svc: str = "icmp") -> dict:
        a = self.dp.resolve(dst)
        if a is None:
            return {"ok": False, "detail": "unknown destination"}
        paths = self.dp.trace(src, a, svc, size=size) if src in self.s.hosts else \
            self.dp.trace(None, a, svc, size=size, src_dev=src)
        d = summarize(paths)
        det = "; ".join(sorted({p.detail for p in paths if p.disposition != "ACCEPTED" and p.detail}))
        if d == "ACCEPTED":   # a probe only succeeds if the reply comes back (stateful return path, incl. NAT)
            src_ip = addr(self.s.hosts[src].ip) if src in self.s.hosts else addr(self.cp.dev[src].interfaces["lo"].ip)
            trans = src_ip
            for p in paths:
                if p.nat_src is not None:
                    trans = self._translated_src(p, src_ip)
            if not self.dp.reverse_ok(dst, trans, src_ip):
                d, det = "NO_RETURN", "reply has no route back"
        return {"ok": d == "ACCEPTED", "disp": d, "detail": det[:200]}

    def link_loads(self) -> Dict[str, float]:
        """Utilisation (%) per directed link from the telemetry traffic matrix (ECMP split)."""
        load = defaultdict(float)
        phys = {(x, ix) for x, ix, y, iy in self.s.links} | {(y, iy) for x, ix, y, iy in self.s.links}
        for src, dst, mbps, *svc in self.s.traffic:
            a = self.dp.resolve(dst)
            if a is None or src not in self.s.hosts:
                continue
            for p in self.dp.trace(src, a, svc[0] if svc else "tcp/443"):
                if p.disposition != "ACCEPTED":
                    continue
                for d, oif in p.links:
                    if (d, oif) in phys:
                        load[(d, oif)] += mbps * p.weight
        util = {}
        for (d, oif), mb in load.items():
            bw = self.cp.ifc(d, oif).bandwidth
            util[f"{d}:{oif}"] = round(100.0 * mb / bw, 1)
        return util

    def link_util(self, a: str, b: str) -> float:
        loads = self.link_loads()
        best = 0.0
        for x, ix, y, iy in self.s.links:
            if {x, y} == {a, b}:
                best = max(best, loads.get(f"{x}:{ix}", 0.0), loads.get(f"{y}:{iy}", 0.0))
        return best

    def convergence(self, a: str, b: str) -> dict:
        """Emulate a silent failure of link a--b and measure traffic-loss duration."""
        link = next(((x, ix, y, iy) for x, ix, y, iy in self.s.links if {x, y} == {a, b}), None)
        if link is None:
            return {"seconds": None, "detail": "no such link"}
        x, ix, y, iy = link
        st, _ = self.cp.adjacency_state(x, y)
        flows = [(t[0], t[1], t[3] if len(t) > 3 else "tcp/443") for t in self.s.traffic]
        affected = []
        for s, d, sv in flows:
            r = self.reach(s, d, sv)
            if r["disp"] == "ACCEPTED" and any((u == x and o == ix) or (u == y and o == iy) for u, o in r.get("links", [])):
                affected.append((s, d, sv))
        if not affected:
            return {"seconds": 0.0, "detail": "no traffic on link", "affected": 0}
        X, Y = self.cp.ifc(x, ix), self.cp.ifc(y, iy)
        if X.bfd and Y.bfd:
            det = {x: self.TIER2_BFD_DETECT, y: self.TIER2_BFD_DETECT}
        else:
            det = {x: float(X.dead), y: float(Y.dead)}
        post = Twin.__new__(Twin)
        post.s = self.s
        post.cp = ControlPlane(self.s, down_links=((x, ix), (y, iy)))
        post.dp = DataPlane(post.cp)
        post._reach = {}
        # hop distance on the post-failure adjacency graph
        g = defaultdict(set)
        for (u, iu, v, iv), (st2, _) in post.cp.adj.items():
            if st2 == "up":
                g[u].add(v)
                g[v].add(u)

        def hops_from(s):
            dist = {s: 0}
            q = [s]
            while q:
                n = q.pop(0)
                for m in g[n]:
                    if m not in dist:
                        dist[m] = dist[n] + 1
                        q.append(m)
            return dist
        hx, hy = hops_from(x), hops_from(y)
        worst = 0.0
        for s, d, sv in affected:
            r = post.reach(s, d, sv)
            if r["disp"] != "ACCEPTED":
                return {"seconds": float("inf"), "detail": f"{s}->{d} not restored ({r['disp']})", "affected": len(affected)}
            routers = {h for p in r["paths"] for h in p if h in self.cp.dev and self.cp.dev[h].ospf}
            t = 0.0
            for rt in routers:
                arr = min(det[x] + self.FLOOD_PER_HOP * hx.get(rt, 99), det[y] + self.FLOOD_PER_HOP * hy.get(rt, 99))
                t = max(t, arr + self.cp.dev[rt].ospf.spf[0] / 1000.0 + self.FIB_UPDATE)
            worst = max(worst, t)
        return {"seconds": round(worst, 2), "detail": f"{len(affected)} flows rerouted", "affected": len(affected)}


def summarize(paths: List[PathResult]) -> str:
    ds = {p.disposition for p in paths}
    if ds == {"ACCEPTED"}:
        return "ACCEPTED"
    for k in ("LOOP", "MTU_DROP", "DENIED_IN", "DENIED_OUT", "NULL_ROUTED", "NO_ROUTE", "NO_HOST",
              "HOST_ISOLATED", "SRC_MTU"):
        if k in ds:
            return k if "ACCEPTED" not in ds else "PARTIAL_" + k
    return sorted(ds)[0]
