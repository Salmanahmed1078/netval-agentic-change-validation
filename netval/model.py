"""Device configuration model, renderer and parser.

Configs are kept as plain text in an IOS/FRR-like dialect so that a change is a
real text diff (like a Git pull request).  The twin never reads the Python
objects of the generator; it always parses the rendered text again.
"""
from __future__ import annotations

import copy
import ipaddress
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# --------------------------------------------------------------------------- data classes
@dataclass
class Iface:
    name: str
    desc: str = ""
    ip: Optional[str] = None            # "10.0.0.1/30"
    bandwidth: int = 1000               # Mbps
    mtu: int = 1500
    ospf_area: Optional[int] = None
    ospf_cost: int = 10
    hello: int = 10
    dead: int = 40
    passive: bool = False
    bfd: bool = False
    mtu_ignore: bool = False
    acl_in: Optional[str] = None
    acl_out: Optional[str] = None
    nat: Optional[str] = None           # inside | outside
    shutdown: bool = False
    sw_mode: Optional[str] = None       # access | trunk
    access_vlan: Optional[int] = None
    trunk_vlans: List[int] = field(default_factory=list)


@dataclass
class AclEntry:
    seq: int
    action: str                          # permit | deny
    proto: str                           # ip | tcp | udp | icmp
    src: str                             # any | prefix
    dst: str
    port: Optional[int] = None


@dataclass
class PlEntry:
    seq: int
    action: str
    prefix: str
    ge: Optional[int] = None
    le: Optional[int] = None


@dataclass
class RmEntry:
    seq: int
    action: str
    match_pl: Optional[str] = None


@dataclass
class Neighbor:
    ip: str
    remote_as: int
    rm_in: Optional[str] = None
    rm_out: Optional[str] = None


@dataclass
class Ospf:
    router_id: str
    spf: List[int] = field(default_factory=lambda: [50, 200, 5000])   # ms
    redistribute: List[List[Optional[str]]] = field(default_factory=list)  # [proto, route-map]
    default_originate: bool = False


@dataclass
class Bgp:
    asn: int
    neighbors: List[Neighbor] = field(default_factory=list)
    networks: List[str] = field(default_factory=list)
    redistribute: List[List[Optional[str]]] = field(default_factory=list)


@dataclass
class Device:
    name: str
    kind: str = "router"                 # router | switch
    interfaces: Dict[str, Iface] = field(default_factory=dict)
    ospf: Optional[Ospf] = None
    bgp: Optional[Bgp] = None
    statics: List[List[str]] = field(default_factory=list)          # [prefix, nexthop|null0]
    prefix_lists: Dict[str, List[PlEntry]] = field(default_factory=dict)
    route_maps: Dict[str, List[RmEntry]] = field(default_factory=dict)
    acls: Dict[str, List[AclEntry]] = field(default_factory=dict)
    nat: List[List[str]] = field(default_factory=list)              # [acl_name, out_iface]

    def iface(self, name: str) -> Iface:
        if name not in self.interfaces:
            self.interfaces[name] = Iface(name)
        return self.interfaces[name]


# --------------------------------------------------------------------------- render
def _acl_line(e: AclEntry) -> str:
    s = f" {e.seq} {e.action} {e.proto} {e.src} {e.dst}"
    if e.port is not None:
        s += f" eq {e.port}"
    return s


def render(dev: Device) -> str:
    L: List[str] = [f"hostname {dev.name}", f"device-role {dev.kind}", "!"]
    for name in sorted(dev.interfaces, key=_iface_sort):
        i = dev.interfaces[name]
        L.append(f"interface {name}")
        if i.desc:
            L.append(f" description {i.desc}")
        if i.sw_mode == "access":
            L.append(f" switchport access vlan {i.access_vlan}")
        elif i.sw_mode == "trunk":
            L.append(" switchport mode trunk")
            L.append(" switchport trunk allowed vlan " + ",".join(str(v) for v in sorted(i.trunk_vlans)))
        if i.ip:
            L.append(f" ip address {i.ip}")
        if i.sw_mode is None and not name.startswith("lo"):
            L.append(f" bandwidth {i.bandwidth}")
            L.append(f" mtu {i.mtu}")
        if i.ospf_area is not None:
            L.append(f" ip ospf area {i.ospf_area}")
            if not name.startswith("lo") and not name.startswith("vlan"):
                L.append(f" ip ospf cost {i.ospf_cost}")
                L.append(f" ip ospf hello-interval {i.hello}")
                L.append(f" ip ospf dead-interval {i.dead}")
            if i.passive:
                L.append(" ip ospf passive")
            if i.mtu_ignore:
                L.append(" ip ospf mtu-ignore")
        if i.bfd:
            L.append(" bfd")
        if i.acl_in:
            L.append(f" ip access-group {i.acl_in} in")
        if i.acl_out:
            L.append(f" ip access-group {i.acl_out} out")
        if i.nat:
            L.append(f" ip nat {i.nat}")
        if i.shutdown:
            L.append(" shutdown")
        L.append("!")
    if dev.ospf:
        o = dev.ospf
        L.append("router ospf")
        L.append(f" router-id {o.router_id}")
        L.append(f" timers throttle spf {o.spf[0]} {o.spf[1]} {o.spf[2]}")
        for proto, rm in o.redistribute:
            L.append(f" redistribute {proto}" + (f" route-map {rm}" if rm else ""))
        if o.default_originate:
            L.append(" default-information originate")
        L.append("!")
    if dev.bgp:
        b = dev.bgp
        L.append(f"router bgp {b.asn}")
        for n in b.neighbors:
            L.append(f" neighbor {n.ip} remote-as {n.remote_as}")
            if n.rm_in:
                L.append(f" neighbor {n.ip} route-map {n.rm_in} in")
            if n.rm_out:
                L.append(f" neighbor {n.ip} route-map {n.rm_out} out")
        for p in b.networks:
            L.append(f" network {p}")
        for proto, rm in b.redistribute:
            L.append(f" redistribute {proto}" + (f" route-map {rm}" if rm else ""))
        L.append("!")
    for p, nh in dev.statics:
        L.append(f"ip route {p} {nh}")
    if dev.statics:
        L.append("!")
    for name in sorted(dev.prefix_lists):
        for e in dev.prefix_lists[name]:
            s = f"ip prefix-list {name} seq {e.seq} {e.action} {e.prefix}"
            if e.ge is not None:
                s += f" ge {e.ge}"
            if e.le is not None:
                s += f" le {e.le}"
            L.append(s)
        L.append("!")
    for name in sorted(dev.route_maps):
        for e in dev.route_maps[name]:
            L.append(f"route-map {name} {e.action} {e.seq}")
            if e.match_pl:
                L.append(f" match ip address prefix-list {e.match_pl}")
        L.append("!")
    for name in sorted(dev.acls):
        L.append(f"ip access-list {name}")
        for e in sorted(dev.acls[name], key=lambda x: x.seq):
            L.append(_acl_line(e))
        L.append("!")
    for acl, out in dev.nat:
        L.append(f"ip nat inside source list {acl} interface {out} overload")
    if dev.nat:
        L.append("!")
    L.append("end")
    return "\n".join(L) + "\n"


def _iface_sort(n: str):
    import re
    m = re.match(r"([a-z]+)(\d*)", n)
    return (m.group(1), int(m.group(2) or 0), n)


# --------------------------------------------------------------------------- parse
class ParseError(Exception):
    pass


def parse(text: str) -> Device:
    dev: Optional[Device] = None
    ctx = None          # ("iface", Iface) | ("ospf",) | ("bgp",) | ("acl", name) | ("rm", name, entry)
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if line in ("!", "end"):
            ctx = None
            continue
        t = line.split()
        indented = raw.startswith(" ")
        try:
            if not indented:
                ctx = None
                if t[0] == "hostname":
                    dev = Device(t[1])
                elif t[0] == "device-role":
                    dev.kind = t[1]
                elif t[0] == "interface":
                    ctx = ("iface", dev.iface(t[1]))
                elif t[:2] == ["router", "ospf"]:
                    dev.ospf = Ospf(router_id="0.0.0.0")
                    ctx = ("ospf",)
                elif t[:2] == ["router", "bgp"]:
                    dev.bgp = Bgp(int(t[2]))
                    ctx = ("bgp",)
                elif t[:2] == ["ip", "route"]:
                    dev.statics.append([t[2], t[3]])
                elif t[:2] == ["ip", "prefix-list"]:
                    e = PlEntry(int(t[4]), t[5], t[6])
                    rest = t[7:]
                    for k in range(0, len(rest) - 1, 2):
                        setattr(e, rest[k], int(rest[k + 1]))
                    dev.prefix_lists.setdefault(t[2], []).append(e)
                elif t[0] == "route-map":
                    e = RmEntry(int(t[3]), t[2])
                    dev.route_maps.setdefault(t[1], []).append(e)
                    ctx = ("rm", t[1], e)
                elif t[:2] == ["ip", "access-list"]:
                    dev.acls.setdefault(t[2], [])
                    ctx = ("acl", t[2])
                elif t[:3] == ["ip", "nat", "inside"]:
                    dev.nat.append([t[5], t[7]])
                else:
                    raise ParseError(f"unknown statement '{line}'")
                continue
            # ---- indented
            if ctx is None:
                raise ParseError(f"orphan line '{line}'")
            if ctx[0] == "iface":
                i: Iface = ctx[1]
                if t[0] == "description":
                    i.desc = " ".join(t[1:])
                elif t[:2] == ["ip", "address"]:
                    ipaddress.ip_interface(t[2])
                    i.ip = t[2]
                elif t[0] == "bandwidth":
                    i.bandwidth = int(t[1])
                elif t[0] == "mtu":
                    i.mtu = int(t[1])
                elif t[:3] == ["ip", "ospf", "area"]:
                    i.ospf_area = int(t[3])
                elif t[:3] == ["ip", "ospf", "cost"]:
                    i.ospf_cost = int(t[3])
                elif t[:3] == ["ip", "ospf", "hello-interval"]:
                    i.hello = int(t[3])
                elif t[:3] == ["ip", "ospf", "dead-interval"]:
                    i.dead = int(t[3])
                elif t[:3] == ["ip", "ospf", "passive"]:
                    i.passive = True
                elif t[:3] == ["ip", "ospf", "mtu-ignore"]:
                    i.mtu_ignore = True
                elif t[0] == "bfd":
                    i.bfd = True
                elif t[:2] == ["ip", "access-group"]:
                    if t[3] == "in":
                        i.acl_in = t[2]
                    else:
                        i.acl_out = t[2]
                elif t[:2] == ["ip", "nat"]:
                    i.nat = t[2]
                elif t[0] == "shutdown":
                    i.shutdown = True
                elif t[:3] == ["switchport", "access", "vlan"]:
                    i.sw_mode, i.access_vlan = "access", int(t[3])
                elif t[:3] == ["switchport", "mode", "trunk"]:
                    i.sw_mode = "trunk"
                elif t[:4] == ["switchport", "trunk", "allowed", "vlan"]:
                    i.trunk_vlans = [int(v) for v in t[4].split(",") if v]
                else:
                    raise ParseError(f"unknown interface command '{line}'")
            elif ctx[0] == "ospf":
                o = dev.ospf
                if t[0] == "router-id":
                    o.router_id = t[1]
                elif t[:3] == ["timers", "throttle", "spf"]:
                    o.spf = [int(x) for x in t[3:6]]
                elif t[0] == "redistribute":
                    o.redistribute.append([t[1], t[3] if len(t) > 3 else None])
                elif t[0] == "default-information":
                    o.default_originate = True
                else:
                    raise ParseError(f"unknown ospf command '{line}'")
            elif ctx[0] == "bgp":
                b = dev.bgp
                if t[0] == "neighbor" and t[2] == "remote-as":
                    b.neighbors.append(Neighbor(t[1], int(t[3])))
                elif t[0] == "neighbor" and t[2] == "route-map":
                    n = next(n for n in b.neighbors if n.ip == t[1])
                    if t[4] == "in":
                        n.rm_in = t[3]
                    else:
                        n.rm_out = t[3]
                elif t[0] == "network":
                    b.networks.append(t[1])
                elif t[0] == "redistribute":
                    b.redistribute.append([t[1], t[3] if len(t) > 3 else None])
                else:
                    raise ParseError(f"unknown bgp command '{line}'")
            elif ctx[0] == "rm":
                if t[:4] == ["match", "ip", "address", "prefix-list"]:
                    ctx[2].match_pl = t[4]
                else:
                    raise ParseError(f"unknown route-map command '{line}'")
            elif ctx[0] == "acl":
                port = int(t[6]) if len(t) > 6 and t[5] == "eq" else None
                dev.acls[ctx[1]].append(AclEntry(int(t[0]), t[1], t[2], t[3], t[4], port))
        except ParseError as e:
            raise ParseError(f"line {lineno}: {e}")
        except (IndexError, ValueError, StopIteration) as e:
            raise ParseError(f"line {lineno}: cannot parse '{line}' ({e})")
    if dev is None:
        raise ParseError("no hostname")
    return dev


# --------------------------------------------------------------------------- snapshot
@dataclass
class Host:
    name: str
    ip: str              # "10.1.10.10/24"
    gw: str
    attach: List[str]    # [device, iface]
    group: str
    mtu: int = 1500

    @property
    def addr(self) -> str:
        return self.ip.split("/")[0]


class Snapshot:
    """A full network snapshot: device configs (text), hosts, links, policy and traffic."""

    def __init__(self, configs: Dict[str, str], hosts: List[dict], links: List[List[str]],
                 policy: dict, traffic: List[list]):
        self.configs = dict(configs)
        self.hosts_raw = copy.deepcopy(hosts)
        self.links = [list(l) for l in links]
        self.policy = copy.deepcopy(policy)
        self.traffic = [list(t) for t in traffic]
        self.devices: Dict[str, Device] = {n: parse(t) for n, t in self.configs.items()}
        self.hosts: Dict[str, Host] = {h["name"]: Host(**h) for h in hosts}

    # files as they would sit in the Git repository
    def files(self) -> Dict[str, str]:
        f = {f"configs/{n}.cfg": t for n, t in sorted(self.configs.items())}
        f["inventory/hosts.json"] = json.dumps(self.hosts_raw, indent=1, sort_keys=True) + "\n"
        f["inventory/links.json"] = json.dumps(self.links, indent=1) + "\n"
        f["policy/intent_policy.json"] = json.dumps(self.policy, indent=1, sort_keys=True) + "\n"
        f["telemetry/traffic_matrix.json"] = json.dumps(self.traffic, indent=1) + "\n"
        return f

    def to_json(self) -> dict:
        return {"configs": self.configs, "hosts": self.hosts_raw, "links": self.links,
                "policy": self.policy, "traffic": self.traffic}

    @classmethod
    def from_json(cls, d: dict) -> "Snapshot":
        return cls(d["configs"], d["hosts"], d["links"], d["policy"], d["traffic"])


def build_snapshot(devices: Dict[str, Device], hosts: List[Host], links, policy, traffic) -> Snapshot:
    return Snapshot({n: render(d) for n, d in devices.items()},
                    [asdict(h) for h in hosts], links, policy, traffic)
