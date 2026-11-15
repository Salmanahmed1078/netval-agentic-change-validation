"""Labelled change-case generator (Phase 2, Step 2).

Every *template* is one realistic change request (a ticket) for one of the
eight change classes.  A template yields a SAFE implementation and one or more
FAULTY implementations (the same ticket, but the diff contains a mistake drawn
from the fault families of NetArena [4] / CORNETTO [6]).  Some templates are
fault-only: the diff does exactly what the ticket asks, but the ticket itself
is harmful (e.g. draining a link overloads the backup path).

Ground truth always comes from the exhaustive oracle, never from the template:
a 'safe' variant the oracle flags (or a 'fault' it does not flag) is dropped.
"""
from __future__ import annotations

import copy
import ipaddress
import random
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional

from .model import Snapshot, Device, AclEntry, PlEntry, RmEntry, render, parse
from .twin import Twin, net, addr
from .topologies import TOPOLOGIES
from .oracle import profile, oracle
from .impact import unified_diff, structured_diff

CLASSES = ["acl", "static_route", "ospf", "bgp_policy", "vlan_move", "mtu", "maintenance", "nat_redistribution"]
CLASS_LABEL = {"acl": "ACL / firewall edit", "static_route": "Static route", "ospf": "OSPF cost / timers",
               "bgp_policy": "BGP route-map / prefix-list", "vlan_move": "VLAN move", "mtu": "MTU change",
               "maintenance": "Interface maintenance (drain/shutdown)", "nat_redistribution": "NAT / redistribution"}
INVS = [("inv", "no_loops"), ("inv", "mgmt_reachable"), ("inv", "isolation"), ("inv", "no_leak")]


# =========================================================================== editable network
class Net:
    def __init__(self, base: Snapshot):
        self.base = base
        self.d: Dict[str, Device] = {n: parse(t) for n, t in base.configs.items()}
        self.hosts = copy.deepcopy(base.hosts_raw)

    def host(self, name):
        return next(h for h in self.hosts if h["name"] == name)

    def link_if(self, a, b):
        for x, ix, y, iy in self.base.links:
            if (x, y) == (a, b):
                return ix, iy
            if (x, y) == (b, a):
                return iy, ix
        raise KeyError(f"no link {a}-{b}")

    def ip_on(self, dev, peer):
        """IP address of `dev` on its link towards `peer`."""
        i, _ = self.link_if(dev, peer)
        return self.d[dev].interfaces[i].ip.split("/")[0]

    def snapshot(self) -> Snapshot:
        return Snapshot({n: render(d) for n, d in self.d.items()}, self.hosts, self.base.links,
                        self.base.policy, self.base.traffic)


@dataclass
class Template:
    tid: str
    cls: str
    clear: str
    vague: str
    intent: dict
    safe: Optional[Callable[[Net], None]]
    faults: Dict[str, Callable[[Net], None]]
    required: List[tuple] = field(default_factory=list)


def _hosts_in(base: Snapshot, prefix: str) -> List[str]:
    n = net(prefix)
    return [h.name for h in base.hosts.values() if ipaddress.ip_address(h.addr) in n]


def _intent_keys(intent: dict) -> List[tuple]:
    k = []
    for s, d, svc, _ in intent.get("flows", []):
        k.append(("flow", s, d, svc))
    for p, _, _ in intent.get("routes", []):
        k.append(("route", str(net(p))))
    for a, b, _ in intent.get("adj", []):
        k.append(("adj",) + tuple(sorted((a, b))))
    for s, d, _, _ in intent.get("paths", []):
        k.append(("path", s, d))
    for s, d, _, _ in intent.get("mtu", []):
        k.append(("mtu", s, d))
    for a, b in intent.get("drained", []):
        k.append(("load",) + tuple(sorted((a, b))))
    for h, x in intent.get("host_like", []):
        k.append(("flow", h, x, "*"))
    return k


# =========================================================================== ACL template
ALT_PORT = {443: 8443, 22: 2222, 25: 587, 5432: 5433, 53: 5353, 80: 8080}


def acl_template(tid, base, devs, acl, entry, flows, clear, vague, fault_names, alt_acl=None):
    """entry = (seq, action, proto, src, dst, port); flows = [[src,dst,svc,want],...]"""
    seq, action, proto, src, dst, port = entry

    def add(n: Net, e: AclEntry, acl_name=acl):
        for d in devs:
            n.d[d].acls[acl_name].append(copy.copy(e))

    def safe(n):
        add(n, AclEntry(seq, action, proto, src, dst, port))

    faults = {}
    if "wrong_port" in fault_names:
        faults["wrong_port"] = (lambda n: add(n, AclEntry(seq, action, proto, src, dst, ALT_PORT[port]))) if port \
            else (lambda n: add(n, AclEntry(seq, action, "tcp", src, dst, 7)))
    if "shadow" in fault_names:
        def shadow(n):
            ents = sorted(n.d[devs[0]].acls[acl], key=lambda e: e.seq)
            opp = [e for e in ents if e.action != action and e.seq > seq]
            new_seq = (opp[0].seq + 5) if opp else seq + 100
            add(n, AclEntry(new_seq, action, proto, src, dst, port))
        faults["shadowed_rule"] = shadow
    if "broad" in fault_names:
        if action == "permit":
            faults["over_broad_rule"] = lambda n: add(n, AclEntry(seq, action, "ip", src, "any", None))
        else:
            faults["over_broad_rule"] = lambda n: add(n, AclEntry(seq, action, "ip", src, dst, None))
    if "broad_src" in fault_names:
        wide = str(ipaddress.ip_network(src).supernet(new_prefix=16))
        faults["over_broad_source"] = lambda n: add(n, AclEntry(seq, action, proto, wide, dst, port))
    if "wrong_acl" in fault_names and alt_acl:
        faults["wrong_acl"] = lambda n: add(n, AclEntry(seq, action, proto, src, dst, port), alt_acl)
    if "one_device" in fault_names and len(devs) > 1:
        def one(n):
            n.d[devs[0]].acls[acl].append(AclEntry(seq, action, proto, src, dst, port))
        faults["only_one_device"] = one
    intent = {"flows": flows}
    req = _intent_keys(intent) + INVS + [("flow", f[0], f[1], "*") for f in flows[:2]]
    return Template(tid, "acl", clear, vague, intent, safe, faults, req)


def flows_for(base, src_prefix, dst_hosts, svc, want):
    return [[s, d, svc, want] for s in _hosts_in(base, src_prefix) for d in dst_hosts if s != d]


# =========================================================================== static route templates
def steer_template(tid, base: Snapshot, rtr: str, dst_host: str, rng):
    tw = Twin(base)
    pp = profile(base)
    h = base.hosts[dst_host]
    pfx = str(net(h.ip))
    cur = tw.cp.rib[rtr][pfx]
    cur_nh = {nh for _, nh in cur.nexthops}
    nbrs = []
    for (a, ia, b, ib), (st, _) in tw.cp.adj.items():
        if st != "up" or rtr not in (a, b):
            continue
        n = b if a == rtr else a
        nip = Net(base).ip_on(n, rtr)
        if nip in cur_nh and len(cur_nh) == 1:
            continue
        r = tw.cp.rib[n].get(pfx)
        my_ip = Net(base).ip_on(rtr, n)
        if r and all(nh != my_ip for _, nh in r.nexthops):
            nbrs.append((n, nip))
    srcs = sorted(x for x in tw.endpoints() if x != dst_host and tw.allowed(x, dst_host, "icmp")
                  and rtr in sum(tw.reach(x, dst_host, "icmp")["paths"], []))
    rng.shuffle(nbrs)
    old_devs = sorted({tw.cp.owner[addr(nh)][0] for nh in cur_nh if nh and addr(nh) in tw.cp.owner})
    for via, via_ip in nbrs:
        for src in srcs:
            intent = {"paths": [[src, dst_host, via, True]], "route_change_ok": [pfx]}
            n = Net(base)
            n.d[rtr].statics.append([pfx, via_ip])
            if oracle(pp, profile(n.snapshot()), intent, base.policy)["label"] != "safe":
                continue
            via_dev = " / ".join(old_devs) or "its current next hop"

            def safe(n, via_ip=via_ip):
                n.d[rtr].statics.append([pfx, via_ip])

            def bad_nh(n, via=via):   # neighbour's loopback instead of its interface address -> not installed
                n.d[rtr].statics.append([pfx, n.d[via].interfaces["lo"].ip.split("/")[0]])

            def wrong_dev(n, via=via):  # configured on the neighbour, pointing back -> loop
                n.d[via].statics.append([pfx, n.ip_on(rtr, via)])
            clear = (f"Traffic engineering: on {rtr}, add a static route for {pfx} ({dst_host}'s subnet) with next hop "
                     f"{via_ip} ({via}'s interface on the {rtr}-{via} link), so that traffic from {rtr} towards {dst_host} "
                     f"(e.g. from {src}) goes only via {via} (today: {via_dev}). Reachability must not change for anyone.")
            vague = f"Move the traffic going to {dst_host} onto the path through {via}."
            req = _intent_keys(intent) + INVS + [("route", pfx), ("flow", src, dst_host, "*")]
            return Template(tid, "static_route", clear, vague, intent, safe,
                            {"unresolvable_next_hop": bad_nh, "wrong_device_loop": wrong_dev}, req)
    # no candidate keeps failover intact: the ticket itself is unsafe (static pin -> micro-loop on failure)
    for via, via_ip in nbrs:
        for src in srcs:
            intent = {"paths": [[src, dst_host, via, True]], "route_change_ok": [pfx]}
            n = Net(base)
            n.d[rtr].statics.append([pfx, via_ip])
            o = oracle(pp, profile(n.snapshot()), intent, base.policy)
            if o["label"] == "unsafe" and o["intent_ok"] and all("failover" in r for r in o["reasons"]):
                via_dev = " / ".join(old_devs) or "its current next hop"
                clear = (f"Traffic engineering: on {rtr}, add a static route for {pfx} ({dst_host}'s subnet) with next hop "
                         f"{via_ip} ({via}'s interface on the {rtr}-{via} link), so that traffic from {rtr} towards "
                         f"{dst_host} (e.g. from {src}) goes only via {via} (today: {via_dev}). Reachability must not change "
                         f"and failover must stay within the 3 s SLA.")
                vague = f"Pin the traffic going to {dst_host} onto the path through {via}."
                req = _intent_keys(intent) + INVS + [("route", pfx), ("flow", src, dst_host, "*")]
                return Template(tid, "static_route", clear, vague, intent, None,
                                {"static_pin_breaks_failover": lambda n, via_ip=via_ip: n.d[rtr].statics.append([pfx, via_ip])},
                                req)
    return None


def null_template(tid, base, devs, target, typo_len, clear, vague):
    good = target
    bad = str(ipaddress.ip_network(target).supernet(new_prefix=typo_len))

    def safe(n):
        for d in devs:
            n.d[d].statics.append([good, "null0"])

    def typo(n):
        for d in devs:
            n.d[d].statics.append([bad, "null0"])
    intent = {"routes": [[good, "present", devs]]}
    req = _intent_keys(intent) + INVS
    return Template(tid, "static_route", clear, vague, intent, safe, {"prefix_length_typo": typo}, req)


# =========================================================================== OSPF templates
def _demand_links(tw: Twin, base: Snapshot):
    """links (a,b) between OSPF routers used by single-path demand flows -> list of (a,b,src,dst)"""
    out = []
    for t in base.traffic:
        r = tw.reach(t[0], t[1], t[3] if len(t) > 3 else "tcp/443")
        if r["disp"] != "ACCEPTED":
            continue
        seen = set()
        for p in r["paths"]:
            for x, y in zip(p, p[1:]):
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                if x in base.devices and y in base.devices and base.devices[x].ospf and base.devices[y].ospf:
                    if tw.adjacency(x, y)[0] == "up":
                        out.append((x, y, t[0], t[1]))
    return out


def _gained_links(pre: Snapshot, post: Snapshot):
    a, b = Twin(pre), Twin(post)
    la, lb = a.link_loads(), b.link_loads()
    out = set()
    for x, ix, y, iy in post.links:
        if lb.get(f"{x}:{ix}", 0) > la.get(f"{x}:{ix}", 0) + 1e-6 or lb.get(f"{y}:{iy}", 0) > la.get(f"{y}:{iy}", 0) + 1e-6:
            out.add(tuple(sorted((x, y))))
    return out


def cost_template(tid, base: Snapshot, rng, used: set):
    tw = Twin(base)
    cands = [c for c in _demand_links(tw, base) if tuple(sorted(c[:2])) not in used]
    rng.shuffle(cands)
    for x, y, s, d in cands:
        n = Net(base)
        ix, iy = n.link_if(x, y)
        n.d[x].interfaces[ix].ospf_cost = 100
        n.d[y].interfaces[iy].ospf_cost = 100
        post = n.snapshot()
        tp = Twin(post)
        r = tp.reach(s, d, "icmp")
        oldp = tw.reach(s, d, "icmp")["paths"]
        if r["disp"] != "ACCEPTED":
            continue
        if any(x in p and y in p and abs(p.index(x) - p.index(y)) == 1 for p in r["paths"]):
            continue
        newnodes = [h for h in r["paths"][0] if h in base.devices and all(h in p for p in r["paths"])
                    and not all(h in p for p in oldp)]
        if not newnodes:
            continue
        via = newnodes[0]
        if oracle(profile(base), profile(post), {"paths": [[s, d, via, True]]}, base.policy)["label"] != "safe":
            continue
        used.add(tuple(sorted((x, y))))

        def safe(n, x=x, y=y):
            a, b = n.link_if(x, y)
            n.d[x].interfaces[a].ospf_cost = 100
            n.d[y].interfaces[b].ospf_cost = 100

        def one_end(n, x=x, y=y):     # only the downstream end changed -> forward path unchanged
            a, b = n.link_if(x, y)
            n.d[y].interfaces[b].ospf_cost = 100

        def typo(n, x=x, y=y):        # cost 1 instead of 100 -> attracts even more traffic
            a, b = n.link_if(x, y)
            n.d[x].interfaces[a].ospf_cost = 1
            n.d[y].interfaces[b].ospf_cost = 1
        intent = {"paths": [[s, d, via, True]]}
        gained = _gained_links(base, post)
        clear = (f"Traffic engineering: raise the OSPF cost of the {x}-{y} link to 100 on both ends ({x}:{ix} and {y}:{iy}) "
                 f"so that the {s} -> {d} traffic moves to the path through {via}. No reachability change is expected and "
                 f"no link may exceed 90% utilisation.")
        vague = f"Shift the {s} to {d} traffic away from the {x}-{y} link."
        req = _intent_keys(intent) + INVS + [("load",) + g for g in sorted(gained)] + [("adj",) + tuple(sorted((x, y)))]
        return Template(tid, "ospf", clear, vague, intent, safe, {"one_sided_cost": one_end, "cost_typo": typo}, req)
    return None


def timer_template(tid, base: Snapshot, rng, used: set):
    tw = Twin(base)
    links = [tuple(l) for l in base.policy["redundant_links"] if tuple(sorted(l)) not in used]
    links = [l for l in links if tw.convergence(*l).get("affected", 0) > 0]
    if not links:
        return None
    x, y = rng.choice(sorted(links))
    used.add(tuple(sorted((x, y))))

    def setit(n, ends, bfd=True):
        a, b = n.link_if(x, y)
        for dev, i in ((x, a), (y, b))[:ends] if ends < 2 else ((x, a), (y, b)):
            itf = n.d[dev].interfaces[i]
            itf.hello, itf.dead = 5, 20
            itf.bfd = bfd

    clear = (f"Reduce OSPF control-plane chatter on the {x}-{y} link: set 'ip ospf hello-interval 5' and "
             f"'ip ospf dead-interval 20' on both ends, keeping BFD enabled so failover stays sub-second "
             f"(convergence SLA 3 s). No routing or reachability change expected.")
    vague = f"Tune the OSPF timers on the {x}-{y} link."
    intent = {}
    req = INVS + [("adj",) + tuple(sorted((x, y))), ("conv",) + tuple(sorted((x, y)))]
    return Template(tid, "ospf", clear, vague, intent, lambda n: setit(n, 2),
                    {"bfd_removed": lambda n: setit(n, 2, bfd=False), "timer_mismatch_one_end": lambda n: setit(n, 1)}, req)


def spf_template(tid, base: Snapshot, routers: List[str]):
    def setit(n, vals):
        for r in routers:
            n.d[r].ospf.spf = list(vals)
    clear = (f"Dampen SPF churn on {', '.join(routers)}: set 'timers throttle spf 200 400 5000' (initial delay 200 ms). "
             f"Failover must stay within the 3 s convergence SLA; nothing else changes.")
    vague = f"Make SPF on {routers[0]} and its peers a bit less aggressive."
    tw = Twin(base)
    lk = sorted(tuple(sorted(l)) for l in base.policy["redundant_links"] if routers[0] in l and tw.convergence(*l).get("affected", 0))
    req = INVS + [("conv",) + l for l in lk[:2]]
    return Template(tid, "ospf", clear, vague, {}, lambda n: setit(n, (200, 400, 5000)),
                    {"spf_delay_typo": lambda n: setit(n, (5000, 10000, 20000))}, req)


# =========================================================================== MTU templates
def mtu_core_template(tid, base: Snapshot, rng, used: set):
    tw = Twin(base)
    cands = [c for c in _demand_links(tw, base) if tuple(sorted(c[:2])) not in used]
    if not cands:
        return None
    x, y, s, d = rng.choice(sorted(cands))
    used.add(tuple(sorted((x, y))))

    def setm(n, v, ends=2):
        a, b = n.link_if(x, y)
        n.d[x].interfaces[a].mtu = v
        if ends == 2:
            n.d[y].interfaces[b].mtu = v

    def typo(n):
        setm(n, 1400)
    clear = (f"Prepare the {x}-{y} core link for jumbo frames: set 'mtu 9000' on both ends. Hosts stay at 1500, so "
             f"no behavioural change is expected; OSPF adjacency {x}-{y} must stay up and 1500-byte traffic such as "
             f"{s} -> {d} must still pass without fragmentation.")
    vague = f"Enable jumbo frames on the {x}-{y} link."
    intent = {"adj": [[x, y, "up"]], "mtu": [[s, d, 1500, "pass"]]}
    req = _intent_keys(intent) + INVS
    return Template(tid, "mtu", clear, vague, intent, lambda n: setm(n, 9000),
                    {"mtu_one_end_only": lambda n: setm(n, 9000, 1), "mtu_value_typo": typo}, req)


def jumbo_fabric_template(tid, base: Snapshot, leaves: List[str], src: str, dst: str, typo_link):
    spines = ["spine1", "spine2"]

    def setall(n, skip=None, typo=None):
        for lf in leaves:
            for sp in spines:
                a, b = n.link_if(lf, sp)
                n.d[lf].interfaces[a].mtu = 9000
                if (lf, sp) != skip:
                    n.d[sp].interfaces[b].mtu = 9000
        if typo:
            a, b = n.link_if(*typo)
            n.d[typo[0]].interfaces[a].mtu = 1400
            n.d[typo[1]].interfaces[b].mtu = 1400
    links = ", ".join(f"{l}-{s}" for l in leaves for s in spines)
    clear = (f"Enable jumbo frames (MTU 9000) on the fabric links {links} (both ends) so that {src} and {dst} can exchange "
             f"9000-byte frames end to end. All OSPF adjacencies must stay up; nothing else changes.")
    vague = f"Turn on jumbo frames between {src} and {dst}."
    intent = {"mtu": [[src, dst, 9000, "pass"], [dst, src, 9000, "pass"]]}
    req = _intent_keys(intent) + INVS + [("adj",) + tuple(sorted((l, s))) for l in leaves for s in spines]
    return Template(tid, "mtu", clear, vague, intent, lambda n: setall(n),
                    {"mtu_one_end_missed": lambda n: setall(n, skip=(leaves[-1], "spine2")),
                     "mtu_value_typo": lambda n: setall(n, typo=typo_link)}, req)


# =========================================================================== maintenance templates
def drain_template(tid, base: Snapshot, rng, used: set, want_overload=False):
    tw = Twin(base)
    umax = base.policy["sla"]["link_util_max_pct"]
    links = [tuple(l) for l in base.policy["redundant_links"] if tuple(sorted(l)) not in used]
    rng.shuffle(links)
    for x, y in links:
        if tw.link_util(x, y) <= 0:
            continue
        n = Net(base)
        a, b = n.link_if(x, y)
        n.d[x].interfaces[a].ospf_cost = 65535
        n.d[y].interfaces[b].ospf_cost = 65535
        post = n.snapshot()
        tp = Twin(post)
        over = max(tp.link_util(p, q) for p, _, q, _ in post.links)
        if tp.link_util(x, y) > 0:
            continue
        if (over > umax) != want_overload:
            continue
        used.add(tuple(sorted((x, y))))

        def safe(n, x=x, y=y):
            a, b = n.link_if(x, y)
            n.d[x].interfaces[a].ospf_cost = 65535
            n.d[y].interfaces[b].ospf_cost = 65535

        la = tw.link_loads()
        ia_, ib_ = Net(base).link_if(x, y)
        end = x if la.get(f"{x}:{ia_}", 0) <= la.get(f"{y}:{ib_}", 0) else y

        def one_end(n, x=x, y=y, end=end):
            a, b = n.link_if(x, y)
            if end == x:
                n.d[x].interfaces[a].ospf_cost = 65535
            else:
                n.d[y].interfaces[b].ospf_cost = 65535
        gained = _gained_links(base, post)
        clear = (f"Fibre maintenance tonight on the {x}-{y} link: drain it by setting 'ip ospf cost 65535' on both ends "
                 f"({x}:{a}, {y}:{b}). Expected: no traffic on {x}-{y}, all reachability unchanged, no other link above "
                 f"{umax:.0f}% utilisation.")
        vague = f"Take the {x}-{y} link out of service for maintenance without an outage."
        intent = {"drained": [[x, y]]}
        req = _intent_keys(intent) + INVS + [("load",) + g for g in sorted(gained)]
        if want_overload:
            return Template(tid, "maintenance", clear, vague, intent, None, {"backup_path_overload": safe}, req)
        return Template(tid, "maintenance", clear, vague, intent, safe, {"drained_one_end_only": one_end}, req)
    return None


def shutdown_template(tid, base: Snapshot, rng, used: set, wrong_iface_pick):
    tw = Twin(base)
    links = [tuple(l) for l in base.policy["redundant_links"] if tuple(sorted(l)) not in used]
    rng.shuffle(links)
    for x, y in links:
        n = Net(base)
        a, b = n.link_if(x, y)
        n.d[x].interfaces[a].shutdown = True
        n.d[y].interfaces[b].shutdown = True
        post = n.snapshot()
        r = oracle(profile(base), profile(post), {"adj": [[x, y, "down"]]}, base.policy)
        if r["label"] != "safe":
            continue
        wrong = wrong_iface_pick(n, x)
        if wrong is None:
            continue
        used.add(tuple(sorted((x, y))))

        def safe(n, x=x, y=y):
            a, b = n.link_if(x, y)
            n.d[x].interfaces[a].shutdown = True
            n.d[y].interfaces[b].shutdown = True

        def wrong_if(n, x=x, wrong=wrong):
            n.d[x].interfaces[wrong].shutdown = True
        clear = (f"Hardware swap on the {x}-{y} link: shut down {x}:{a} and {y}:{b}. The OSPF adjacency {x}-{y} will go "
                 f"down; traffic must reroute over the redundant paths with no loss of reachability.")
        vague = f"Shut the {x}-{y} link for a hardware swap."
        intent = {"adj": [[x, y, "down"]]}
        req = INVS + [("adj",) + tuple(sorted((x, y)))]
        return Template(tid, "maintenance", clear, vague, intent, safe, {"wrong_interface_shut": wrong_if}, req)
    return None


# =========================================================================== VLAN move
def vlan_template(tid, base: Snapshot, host: str, new_vlan: int, new_ip: str, new_gw: str, like: str, typo_vlan: int,
                  clear, vague):
    def move(n, vlan=new_vlan, ip=new_ip, gw=new_gw):
        h = n.host(host)
        d, p = h["attach"]
        n.d[d].interfaces[p].access_vlan = vlan
        h["ip"], h["gw"] = ip, gw
    old = base.hosts[host]
    intent = {"host_like": [[host, like]]}
    req = INVS + [("flow", host, like, "*")]
    return Template(tid, "vlan_move", clear, vague, intent, move,
                    {"vlan_id_typo": lambda n: move(n, vlan=typo_vlan),
                     "host_not_readdressed": lambda n: move(n, ip=old.ip, gw=old.gw)}, req)


# =========================================================================== BGP / redistribution
def vip_template(tid, base, rtr, vip, nh_host, discard, targets, clear, vague):
    nh = base.hosts[nh_host].addr

    def common(n):
        n.d[rtr].statics.append([vip, nh])

    def safe(n):
        common(n)
        n.d[rtr].prefix_lists["PL_VIP"] = [PlEntry(5, "permit", vip)]
        n.d[rtr].route_maps["RM_VIP"] = [RmEntry(10, "permit", "PL_VIP")]
        n.d[rtr].ospf.redistribute.append(["static", "RM_VIP"])

    def no_rm(n):
        common(n)
        n.d[rtr].ospf.redistribute.append(["static", None])

    def wrong_pl(n):
        common(n)
        n.d[rtr].prefix_lists["PL_VIP"] = [PlEntry(5, "permit", vip)]
        n.d[rtr].route_maps["RM_VIP"] = [RmEntry(10, "permit", "PL_VIP_LIST")]
        n.d[rtr].ospf.redistribute.append(["static", "RM_VIP"])
    intent = {"routes": [[vip, "present", targets]]}
    req = _intent_keys(intent) + INVS + [("route", str(net(discard)))]
    return Template(tid, "nat_redistribution", clear, vague, intent, safe,
                    {"redistribute_without_route_map": no_rm, "route_map_references_missing_list": wrong_pl}, req)


def pl_add_template(tid, cls, devs, pl, prefix, typo, intent, clear, vague, extra=None, leak=None, wrong_list=None,
                    pre_fn=None):
    def base_fn(n):
        if pre_fn:
            pre_fn(n)

    def add(n, p, lst=pl):
        base_fn(n)
        for d in devs:
            ents = n.d[d].prefix_lists.setdefault(lst, [])
            ents.append(PlEntry(max([e.seq for e in ents] + [0]) + 5, "permit", p))
    faults = {"prefix_list_typo": lambda n: add(n, typo)}
    if leak:
        faults[leak[0]] = lambda n: (add(n, prefix), leak[1](n))
    if wrong_list:
        faults["entry_added_to_wrong_list"] = lambda n: add(n, prefix, wrong_list)
    req = _intent_keys(intent) + INVS + (extra or [])
    return Template(tid, cls, clear, vague, intent, lambda n: add(n, prefix), faults, req)


# =========================================================================== per-topology catalogues
def campus_templates(base, rng) -> List[Template]:
    T = []
    used = set()
    # ---- ACL
    T.append(acl_template("C-ACL-1", base, ["dist1"], "ACL_STAFF_IN", (45, "permit", "icmp", "10.1.10.0/24", "10.99.0.0/24", None),
                          flows_for(base, "10.1.10.0/24", ["noc1"], "icmp", "allow"),
                          "Helpdesk request: staff must be able to ping the NOC. On dist1, in ACL_STAFF_IN, add "
                          "'45 permit icmp 10.1.10.0/24 10.99.0.0/24' before the deny of the NOC subnet (seq 50). Expected: "
                          "staff1, staff2 and printer1 can ping noc1; SSH and all other services to the NOC stay blocked; nothing else changes.",
                          "Let staff users ping the NOC so the helpdesk can troubleshoot.",
                          ["wrong_port", "shadow", "broad", "wrong_acl"], alt_acl="ACL_FIN_IN"))
    T.append(acl_template("C-ACL-2", base, ["dist1"], "ACL_STAFF_IN", (15, "deny", "tcp", "10.1.10.0/24", "10.20.2.0/24", 80),
                          flows_for(base, "10.1.10.0/24", ["web1"], "tcp/80", "deny"),
                          "Enforce HTTPS: on dist1 add '15 deny tcp 10.1.10.0/24 10.20.2.0/24 eq 80' to ACL_STAFF_IN "
                          "(before the permit at seq 20). Staff lose plain HTTP to web1; HTTPS (443) and DNS keep working.",
                          "Staff should only reach the intranet web server over HTTPS.",
                          ["shadow", "broad", "wrong_port", "wrong_acl"], alt_acl="ACL_FIN_IN"))
    # ---- static
    T.append(steer_template("C-STA-1", base, "dist1", "web1", rng))
    T.append(null_template("C-STA-2", base, ["edge1"], "192.0.2.66/32", 24,
                           "Security request: discard traffic to the compromised partner address 192.0.2.66 by adding "
                           "'ip route 192.0.2.66/32 null0' on edge1. The rest of the partner network 192.0.2.0/24 "
                           "(e.g. partner1) must stay reachable.",
                           "Block traffic to the compromised partner host."))
    # ---- OSPF
    T.append(cost_template("C-OSPF-1", base, rng, used))
    T.append(timer_template("C-OSPF-2", base, rng, used))
    # ---- BGP
    def leak_c(n):
        n.d["edge1"].route_maps["RM_TO_ISP"].append(RmEntry(20, "permit", None))
        n.d["edge1"].bgp.redistribute.append(["ospf", None])

    def adv_pre(n):
        n.d["edge1"].statics.append(["198.51.101.0/24", "null0"])
        n.d["edge1"].bgp.networks.append("198.51.101.0/24")
    T.append(pl_add_template("C-BGP-1", "bgp_policy", ["edge1"], "PL_PUBLIC", "198.51.101.0/24", "198.51.110.0/24",
                             {"routes": [["198.51.101.0/24", "present", ["inet"]]]},
                             "Announce our second public block 198.51.101.0/24 to the ISP: on edge1 add a null0 anchor "
                             "route and 'network 198.51.101.0/24' under BGP, and permit it in PL_PUBLIC (used by RM_TO_ISP). "
                             "Only the two public blocks may be announced.",
                             "Start advertising our new public address block to the ISP.",
                             leak=("internal_routes_leaked", leak_c), pre_fn=adv_pre))

    def rmin_safe(n):
        n.d["edge1"].prefix_lists["PL_PARTNER"] = [PlEntry(5, "permit", "192.0.2.0/24")]
        n.d["edge1"].route_maps["RM_FROM_ISP"] = [RmEntry(10, "permit", "PL_PARTNER")]
        n.d["edge1"].bgp.neighbors[0].rm_in = "RM_FROM_ISP"

    def rmin_typo(n):
        rmin_safe(n)
        n.d["edge1"].prefix_lists["PL_PARTNER"] = [PlEntry(5, "permit", "192.0.20.0/24")]

    def rmin_name(n):
        rmin_safe(n)
        n.d["edge1"].bgp.neighbors[0].rm_in = "RM_FROM_IPS"
    it = {"routes": [["8.8.8.0/24", "absent", ["edge1"]], ["192.0.2.0/24", "present", ["edge1"]]]}
    T.append(Template("C-BGP-2", "bgp_policy",
                      "Inbound filtering from the ISP: on edge1 create PL_PARTNER (permit 192.0.2.0/24) and route-map "
                      "RM_FROM_ISP permitting it, and apply it inbound on the ISP neighbor. Only the partner prefix "
                      "192.0.2.0/24 should be learned; everything else keeps working through the static default route.",
                      "Only accept the partner network from the ISP over BGP.", it, rmin_safe,
                      {"prefix_list_typo": rmin_typo, "route_map_name_typo": rmin_name}, _intent_keys(it) + INVS))
    # ---- VLAN
    T.append(vlan_template("C-VLAN-1", base, "staff2", 20, "10.1.20.21/24", "10.1.20.1", "fin2", 21,
                           "staff2 has joined Finance: on acc2 move staff2's access port to VLAN 20 and re-address the host "
                           "to 10.1.20.21/24 with gateway 10.1.20.1. Afterwards staff2 must have exactly the same access as "
                           "the other finance hosts (e.g. fin2).", "Move staff2 into the Finance VLAN."))
    T.append(vlan_template("C-VLAN-2", base, "kiosk1", 15, "10.1.15.21/24", "10.1.15.1", "lab1", 16,
                           "Repurpose kiosk1 as a lab machine: on acc3 move its port to VLAN 15 and re-address it to "
                           "10.1.15.21/24 (gateway 10.1.15.1). It must then behave like lab1.",
                           "Put kiosk1 in the lab network."))
    # ---- MTU
    T.append(mtu_core_template("C-MTU-1", base, rng, used))
    T.append(mtu_core_template("C-MTU-2", base, rng, used))
    # ---- maintenance
    T.append(drain_template("C-MNT-1", base, rng, used))
    T.append(drain_template("C-MNT-2", base, rng, used, want_overload=True))

    def shut_pick(n, x):
        for i, itf in n.d[x].interfaces.items():
            if itf.desc == "host segment" or (itf.ip and not itf.ospf_area and i != "lo"):
                return i
        return None
    T.append(shutdown_template("C-MNT-3", base, rng, used, shut_pick))
    # ---- NAT / redistribution
    def nat(n, p):
        n.d["edge1"].acls["NAT_INSIDE"].append(AclEntry(20, "permit", "ip", p, "any"))
    it = {"flows": [["noc1", "ext-web", "*", "allow"], ["noc1", "partner1", "*", "allow"]]}
    T.append(Template("C-NAT-1", "nat_redistribution",
                      "Give the NOC subnet 10.99.0.0/24 internet access for software updates: add "
                      "'20 permit ip 10.99.0.0/24 any' to NAT_INSIDE on edge1. Only noc1's internet access should change.",
                      "The NOC needs internet access for updates.", it, lambda n: nat(n, "10.99.0.0/24"),
                      {"nat_source_too_broad": lambda n: nat(n, "10.0.0.0/8"),
                       "nat_wrong_subnet": lambda n: nat(n, "10.9.0.0/24")}, _intent_keys(it) + INVS))
    T.append(vip_template("C-RED-1", base, "srv1", "10.20.50.0/24", "web1", "10.20.99.0/24",
                          ["core1", "core2", "dist1", "dist2", "edge1"],
                          "New load-balanced service VIP 10.20.50.0/24 hosted behind web1: on srv1 add "
                          "'ip route 10.20.50.0/24 10.20.2.10' and redistribute only this static into OSPF using "
                          "prefix-list PL_VIP and route-map RM_VIP. Other statics on srv1 (the 10.20.99.0/24 discard) must "
                          "stay local.", "Make the new web VIP reachable from the rest of the campus."))
    return [t for t in T if t]


def dc_templates(base, rng) -> List[Template]:
    T, used = [], set()
    T.append(acl_template("D-ACL-1", base, ["leaf3"], "ACL_DBA_OUT", (25, "permit", "tcp", "10.101.2.0/24", "10.103.1.0/24", 5432),
                          [["stage-a1", "db-a1", "tcp/5432", "allow"]],
                          "Staging needs database access: on leaf3 add '25 permit tcp 10.101.2.0/24 10.103.1.0/24 eq 5432' "
                          "to ACL_DBA_OUT (before the final deny). Only stage-a1 -> db-a1 on tcp/5432 should open.",
                          "Let the tenant-A staging server talk to the tenant-A database.", ["shadow", "broad", "wrong_port"]))
    T.append(acl_template("D-ACL-2", base, ["leaf1"], "ACL_TENANT_A_IN", (5, "deny", "tcp", "10.101.2.0/24", "10.102.1.0/24", 443),
                          [["stage-a1", "app-a1", "tcp/443", "deny"]],
                          "Staging must not call the production app API: on leaf1 add '5 deny tcp 10.101.2.0/24 "
                          "10.102.1.0/24 eq 443' to ACL_TENANT_A_IN. web-a1 (same ACL, VLAN 110) must keep its access to app-a1.",
                          "Stop staging from hitting the production app.", ["broad", "broad_src", "wrong_port"]))
    T.append(acl_template("D-ACL-3", base, ["leaf4"], "ACL_TENANT_B_IN", (5, "deny", "tcp", "10.201.1.0/24", "10.31.0.0/24", 22),
                          [["web-b1", "backup1", "tcp/22", "deny"]],
                          "Security finding: tenant-B web servers can SSH to the shared backup server. On leaf4 add "
                          "'5 deny tcp 10.201.1.0/24 10.31.0.0/24 eq 22' to ACL_TENANT_B_IN. Other traffic to backup1 is unchanged.",
                          "Block SSH from tenant B web to the backup server.", ["broad", "broad_src", "wrong_port"]))
    T.append(steer_template("D-STA-1", base, "leaf1", "app-a1", rng))
    T.append(steer_template("D-STA-2", base, "leaf4", "db-b1", rng))
    T.append(null_template("D-STA-3", base, ["border1", "border2"], "198.18.0.99/32", 24,
                           "A WAN host 198.18.0.99 is attacking us: on border1 and border2 add 'ip route 198.18.0.99/32 null0'. "
                           "All other WAN users (198.18.0.0/24, e.g. wan-user1) must keep their access.",
                           "Blackhole the attacking WAN address."))
    T.append(cost_template("D-OSPF-1", base, rng, used))
    T.append(timer_template("D-OSPF-2", base, rng, used))
    T.append(spf_template("D-OSPF-3", base, ["spine1", "spine2"]))
    # BGP
    def leak_pl(n):
        for b in ("border1", "border2"):
            n.d[b].prefix_lists["PL_PUBLIC"].append(PlEntry(50, "permit", "10.0.0.0/8", le=24))
    T.append(pl_add_template("D-BGP-1", "bgp_policy", ["border1", "border2"], "PL_PUBLIC", "10.101.2.0/24", "10.110.2.0/24",
                             {"routes": [["10.101.2.0/24", "present", ["wan"]]], "flows": [["wan-user1", "stage-a1", "*", "allow"], ["stage-a1", "wan-user1", "*", "allow"]]},
                             "Publish the tenant-A staging subnet 10.101.2.0/24 to the WAN: add it to PL_PUBLIC on border1 and "
                             "border2. wan-user1 should then reach stage-a1; no other internal prefix may be announced.",
                             "Make tenant A's staging site reachable from the WAN.",
                             leak=("over_broad_prefix_list_leak", leak_pl)))

    def wd(n, keep_first):
        for b in ("border1", "border2"):
            ents = n.d[b].prefix_lists["PL_PUBLIC"]
            n.d[b].prefix_lists["PL_PUBLIC"] = [e for e in ents if e.prefix != ("10.101.1.0/24" if keep_first else "10.201.1.0/24")]
    it = {"routes": [["10.201.1.0/24", "absent", ["wan"]]], "flows": [["wan-user1", "web-b1", "*", "deny"], ["web-b1", "wan-user1", "*", "deny"]]}
    T.append(Template("D-BGP-2", "bgp_policy",
                      "Tenant B's public web (10.201.1.0/24) is being moved to a CDN: remove it from PL_PUBLIC on border1 "
                      "and border2 so it is no longer announced to the WAN. wan-user1 will lose access to web-b1; web-a1 "
                      "stays published.", "Stop announcing tenant B's web servers to the WAN.", it,
                      lambda n: wd(n, False), {"wrong_prefix_removed": lambda n: wd(n, True)}, _intent_keys(it) + INVS))

    def wan_pre(n):
        n.d["wan"].statics.append(["198.18.1.0/24", "null0"])
        n.d["wan"].bgp.networks.append("198.18.1.0/24")
    T.append(pl_add_template("D-BGP-3", "bgp_policy", ["border1", "border2"], "PL_WAN", "198.18.1.0/24", "198.18.10.0/24",
                             {"routes": [["198.18.1.0/24", "present", ["leaf1", "leaf4", "spine1"]]]},
                             "The WAN team announces a new user range 198.18.1.0/24. Accept it on border1 and border2 by adding "
                             "it to PL_WAN (used by RM_FROM_WAN and RM_BGP2OSPF) so that the fabric learns it.",
                             "Accept the WAN's new user range.", wrong_list="PL_PUBLIC", pre_fn=wan_pre))
    T.append(vlan_template("D-VLAN-1", base, "web-a1", 111, "10.101.2.21/24", "10.101.2.1", "stage-a1", 112,
                           "Demote web-a1 to staging: on leaf1 move its port to VLAN 111 and re-address it to 10.101.2.21/24 "
                           "(gw 10.101.2.1). It must then have the same access as stage-a1.", "Move web-a1 to the staging VLAN."))
    T.append(vlan_template("D-VLAN-2", base, "stage-b1", 210, "10.201.1.21/24", "10.201.1.1", "web-b1", 212,
                           "Promote stage-b1 to production web: on leaf4 move its port to VLAN 210 and re-address it to "
                           "10.201.1.21/24 (gw 10.201.1.1). It must behave exactly like web-b1.",
                           "Promote the tenant-B staging server to production."))
    T.append(vlan_template("D-VLAN-3", base, "stage-a1", 110, "10.101.1.21/24", "10.101.1.1", "web-a1", 101,
                           "Promote stage-a1 into the tenant-A web VLAN 110 on leaf1 with address 10.101.1.21/24 "
                           "(gw 10.101.1.1); it must then behave exactly like web-a1.", "Move stage-a1 into production web."))
    T.append(jumbo_fabric_template("D-MTU-1", base, ["leaf1", "leaf2"], "web-a1", "app-a1", ("leaf1", "spine1")))
    T.append(jumbo_fabric_template("D-MTU-2", base, ["leaf2", "leaf6"], "app-a1", "dns-dc1", ("leaf6", "spine2")))
    T.append(jumbo_fabric_template("D-MTU-3", base, ["leaf4", "leaf6"], "web-b1", "backup1", ("leaf4", "spine1")))
    T.append(drain_template("D-MNT-1", base, rng, used))
    T.append(drain_template("D-MNT-2", base, rng, used, want_overload=True))

    def shut_pick(n, x):
        for i, itf in sorted(n.d[x].interfaces.items()):
            if itf.sw_mode == "access":
                return i
        return None
    T.append(shutdown_template("D-MNT-3", base, rng, used, shut_pick))
    T.append(vip_template("D-RED-1", base, "leaf2", "10.102.50.0/24", "app-a1", "10.102.99.0/24",
                          ["leaf1", "leaf3", "spine1", "border1"],
                          "Publish the tenant-A app VIP 10.102.50.0/24 (served by app-a1) inside the fabric: on leaf2 add "
                          "'ip route 10.102.50.0/24 10.102.1.10' and redistribute only this static into OSPF using PL_VIP / "
                          "RM_VIP. The local discard route 10.102.99.0/24 must not be redistributed.",
                          "Make the new app VIP reachable across the fabric."))
    T.append(vip_template("D-RED-2", base, "leaf4", "10.201.50.0/24", "web-b1", "10.201.99.0/24",
                          ["leaf1", "leaf5", "spine2", "border2"],
                          "Publish the tenant-B web VIP 10.201.50.0/24 (served by web-b1): on leaf4 add 'ip route "
                          "10.201.50.0/24 10.201.1.10' and redistribute only that static into OSPF via PL_VIP / RM_VIP. "
                          "The discard route 10.201.99.0/24 stays local.", "Advertise the tenant-B VIP in the fabric."))
    T.append(vip_template("D-RED-3", base, "leaf6", "10.30.50.0/24", "dns-dc1", "10.30.99.0/24",
                          ["leaf1", "leaf4", "spine1", "border1"],
                          "Anycast DNS VIP 10.30.50.0/24 served by dns-dc1: on leaf6 add 'ip route 10.30.50.0/24 10.30.0.10' "
                          "and redistribute only this static into OSPF (PL_VIP / RM_VIP). Keep the 10.30.99.0/24 discard local.",
                          "Advertise the DNS VIP."))
    return [t for t in T if t]


def isp_templates(base, rng) -> List[Template]:
    T, used = [], set()
    dsts = lambda src: [h for h in base.hosts if h != src]
    T.append(acl_template("I-ACL-1", base, ["ATL"], "ACL_CUST2_IN", (5, "deny", "tcp", "172.16.2.0/24", "any", 25),
                          [[s, d, "tcp/25", "deny"] for s in ["c2-host"] for d in dsts("c2-host") if Twin(base).allowed(s, d, "tcp/25")],
                          "Abuse report: customer 2 is sending spam. On ATL add '5 deny tcp 172.16.2.0/24 any eq 25' to "
                          "ACL_CUST2_IN. Only outbound SMTP (tcp/25) from customer 2 is blocked; all other services stay up.",
                          "Stop customer 2 from sending spam.", ["broad", "wrong_port", "shadow"]))
    T.append(acl_template("I-ACL-2", base, ["SEA"], "ACL_CUST1_IN", (5, "deny", "tcp", "172.16.1.0/24", "172.16.3.0/24", 22),
                          [["c1-host", "c3-host", "tcp/22", "deny"]],
                          "Customer 3 complains about SSH brute-force from customer 1. On SEA add '5 deny tcp 172.16.1.0/24 "
                          "172.16.3.0/24 eq 22' to ACL_CUST1_IN; nothing else changes.",
                          "Block the SSH scanning from customer 1 towards customer 3.", ["broad", "wrong_port", "shadow"]))
    T.append(acl_template("I-ACL-3", base, ["PRN", "CPK"], "ACL_CUST3_IN", (5, "deny", "udp", "172.16.3.0/24", "1.1.1.0/24", 53),
                          [["c3-host", "ext-dns2", "udp/53", "deny"]],
                          "Customer 3 (dual-homed to PRN and CPK) wants its hosts forced to the ISP resolver: on BOTH PRN and "
                          "CPK add '5 deny udp 172.16.3.0/24 1.1.1.0/24 eq 53' to ACL_CUST3_IN.",
                          "Block customer 3 from using the external public resolver.", ["broad", "wrong_port"]))
    T.append(steer_template("I-STA-1", base, "SEA", "c2-host", rng))
    T.append(null_template("I-STA-2", base, ["PIT", "PAO"], "172.16.2.66/32", 24,
                           "DDoS against customer 2's address 172.16.2.66: on the border routers PIT and PAO add "
                           "'ip route 172.16.2.66/32 null0' (remote-triggered blackhole). Customer 2's other hosts (c2-host) "
                           "must remain reachable from the internet.", "Blackhole the DDoS target address at our borders."))
    T.append(steer_template("I-STA-3", base, "ATL", "c1-host", rng))
    T.append(cost_template("I-OSPF-1", base, rng, used))
    T.append(cost_template("I-OSPF-2", base, rng, used))
    T.append(timer_template("I-OSPF-3", base, rng, used))

    def c1_pre(n):
        n.d["cust1"].statics.append(["172.16.11.0/24", "null0"])
        n.d["cust1"].bgp.networks.append("172.16.11.0/24")

    def leak_p(n):
        n.d["PIT"].route_maps["RM_TO_PEER"].append(RmEntry(20, "permit", None))
    T.append(pl_add_template("I-BGP-1", "bgp_policy", ["SEA"], "PL_CUST1", "172.16.11.0/24", "172.16.111.0/24",
                             {"routes": [["172.16.11.0/24", "present", ["peer1", "peer2"]]]},
                             "Customer 1 announces a new prefix 172.16.11.0/24. On SEA add it to PL_CUST1 (used by RM_CUST1_IN) "
                             "so we accept it; it must then be visible to both upstream peers. No other routes may be sent to peers.",
                             "Accept and propagate customer 1's new prefix.",
                             leak=("peer_route_map_leak", leak_p), pre_fn=c1_pre))

    def single(n, p, le=None):
        n.d["PAO"].prefix_lists["PL_CUST_ALL"].insert(0, PlEntry(3, "deny", p, le=le))
    it = {"routes": [["172.16.2.0/24", "absent", ["peer2"]]],
          "flows": [["c2-host", "ext-dns2", "*", "deny"], ["ext-dns2", "c2-host", "*", "deny"]]}
    T.append(Template("I-BGP-2", "bgp_policy",
                      "Customer 2 wants single-homed transit via peer1 only: on PAO add 'seq 3 deny 172.16.2.0/24' to "
                      "PL_CUST_ALL so 172.16.2.0/24 is no longer announced to peer2. Customer 2 accepts it will lose "
                      "connectivity to peer2's networks (ext-dns2); all other customers are unaffected.",
                      "Stop announcing customer 2 to peer2.", it, lambda n: single(n, "172.16.2.0/24"),
                      {"deny_too_broad": lambda n: single(n, "172.16.0.0/16", le=24)}, _intent_keys(it) + INVS))

    def p1_pre(n):
        n.d["peer1"].statics.append(["8.8.4.0/24", "null0"])
        n.d["peer1"].bgp.networks.append("8.8.4.0/24")
    T.append(pl_add_template("I-BGP-3", "bgp_policy", ["PIT"], "PL_INET", "8.8.4.0/24", "8.8.44.0/24",
                             {"routes": [["8.8.4.0/24", "present", ["SEA", "ATL", "BOU"]]]},
                             "peer1 starts announcing 8.8.4.0/24. On PIT add it to PL_INET (used by RM_PEER_IN and RM_INET2OSPF) "
                             "so the backbone learns it.", "Accept peer1's new prefix.", wrong_list="PL_CUST_ALL", pre_fn=p1_pre))
    T.append(vlan_template("I-VLAN-1", base, "noclab1", 99, "10.99.0.21/24", "10.99.0.1", "noc2", 97,
                           "noclab1 becomes a production NOC workstation: on noc-sw move its port to VLAN 99 and re-address "
                           "it to 10.99.0.21/24 (gw 10.99.0.1). It must have the same access as noc2.",
                           "Move noclab1 into the NOC VLAN."))
    T.append(vlan_template("I-VLAN-2", base, "noc2", 98, "10.98.0.21/24", "10.98.0.1", "noclab1", 89,
                           "noc2 is being repurposed as a lab box: move its noc-sw port to VLAN 98 and re-address it to "
                           "10.98.0.21/24 (gw 10.98.0.1); it should then behave like noclab1.", "Move noc2 to the lab VLAN."))
    T.append(vlan_template("I-VLAN-3", base, "noclab2", 99, "10.99.0.22/24", "10.99.0.1", "noc2", 9,
                           "Promote noclab2 to the NOC VLAN 99 on noc-sw, new address 10.99.0.22/24, gateway 10.99.0.1. "
                           "It must have the same access as noc2.", "Put noclab2 in the NOC network."))
    T.append(mtu_core_template("I-MTU-1", base, rng, used))
    T.append(mtu_core_template("I-MTU-2", base, rng, used))
    T.append(mtu_core_template("I-MTU-3", base, rng, used))
    T.append(drain_template("I-MNT-1", base, rng, used))
    T.append(drain_template("I-MNT-2", base, rng, used, want_overload=True))

    def shut_pick(n, x):
        for i, itf in sorted(n.d[x].interfaces.items()):
            if itf.ip and itf.ospf_area is None and i != "lo":
                return i
        return None
    T.append(shutdown_template("I-MNT-3", base, rng, used, shut_pick))
    T.append(vip_template("I-RED-1", base, "BOU", "10.99.50.0/24", "noc1", "10.97.0.0/16",
                          ["SEA", "ATL", "PIT", "LNK"],
                          "Monitoring VIP 10.99.50.0/24 served by noc1: on BOU add 'ip route 10.99.50.0/24 10.99.0.10' and "
                          "redistribute only this static into OSPF (PL_VIP / RM_VIP). The 10.97.0.0/16 discard route must "
                          "stay local to BOU.", "Make the monitoring VIP reachable across the backbone."))

    def cpk(n, dev, p, le=None):
        rm = n.d[dev].route_maps["RM_BGP2OSPF"]
        n.d[dev].prefix_lists["PL_NO_C3"] = [PlEntry(5, "permit", p, le=le)]
        rm.insert(0, RmEntry(5, "deny", "PL_NO_C3"))
    tw = Twin(base)
    it = {"paths": [["c1-host", "c3-host", "PRN", True]], "routes": [["172.16.3.0/24", "present", ["SEA", "ATL"]]]}
    T.append(Template("I-RED-2", "nat_redistribution",
                      "CPK is being retired as an entry point for customer 3: on CPK add a deny entry (prefix-list PL_NO_C3 "
                      "= 172.16.3.0/24) at the top of RM_BGP2OSPF, so customer 3's prefix is only injected into OSPF by PRN. "
                      "Customer 3 must stay reachable (via PRN).", "Make PRN the only router injecting customer 3's route.", it,
                      lambda n: cpk(n, "CPK", "172.16.3.0/24"),
                      {"applied_on_wrong_router": lambda n: cpk(n, "PRN", "172.16.3.0/24"),
                       "deny_all_customers": lambda n: (cpk(n, "CPK", "172.16.3.0/24"), cpk(n, "PRN", "172.16.0.0/16", 24))},
                      _intent_keys(it) + INVS))
    return [t for t in T if t]


CATALOG = {"campus": campus_templates, "datacenter": dc_templates, "isp": isp_templates}

TERSE = ["fix ticket #{n}", "CHG-{n}", "per change request {n}", "{n} - as discussed", "implements CHG-{n}",
         "ticket {n} follow-up"]


# =========================================================================== generation
def build_case(cid, topo, tmpl: Template, variant: str, fn, base: Snapshot, pre_prof, rng) -> Optional[dict]:
    n = Net(base)
    try:
        fn(n)
        post = n.snapshot()
    except Exception as e:  # pragma: no cover
        print("  build failed", cid, variant, e)
        return None
    if post.configs == base.configs and post.hosts_raw == base.hosts_raw:
        return None
    o = oracle(pre_prof, profile(post), tmpl.intent, base.policy)
    want = "safe" if variant == "safe" else "unsafe"
    if o["label"] != want:
        return None
    num = rng.randint(1000, 9999)
    return {
        "id": cid, "topology": topo, "cls": tmpl.cls, "template": tmpl.tid, "variant": variant,
        "label": o["label"], "oracle_reasons": o["reasons"], "intent_spec": tmpl.intent,
        "tickets": {"clear": tmpl.clear, "vague": tmpl.vague, "terse": rng.choice(TERSE).format(n=num)},
        "required": [list(k) for k in dict.fromkeys(tuple(k) for k in tmpl.required)],
        "post": post.to_json(), "diff": unified_diff(base, post),
    }


def generate(seed: int = 7, verbose=True) -> dict:
    rng = random.Random(seed)
    out = {"bases": {}, "cases": []}
    for topo, fn in TOPOLOGIES.items():
        base = fn()
        out["bases"][topo] = base.to_json()
        pre_prof = profile(base)
        temps = CATALOG[topo](base, rng)
        for tmpl in temps:
            made = []
            if tmpl.safe is not None:
                c = build_case(f"{tmpl.tid}-S", topo, tmpl, "safe", tmpl.safe, base, pre_prof, rng)
                if c:
                    made.append(c)
                elif verbose:
                    print("  !! safe variant rejected by oracle:", tmpl.tid)
            names = list(tmpl.faults)
            rng.shuffle(names)
            for fname in names:
                c = build_case(f"{tmpl.tid}-F", topo, tmpl, fname, tmpl.faults[fname], base, pre_prof, rng)
                if c:
                    made.append(c)
                    break
            else:
                if verbose:
                    print("  !! no fault variant accepted:", tmpl.tid, names)
            out["cases"] += made
        if verbose:
            print(f"{topo}: {sum(c['topology'] == topo for c in out['cases'])} cases from {len(temps)} templates")
    return out
