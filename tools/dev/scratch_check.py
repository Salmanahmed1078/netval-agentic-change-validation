import time, sys
from netval.topologies import TOPOLOGIES
from netval.model import parse, render
from netval.twin import Twin, SERVICES
for name, fn in TOPOLOGIES.items():
    t0=time.time(); s=fn()
    for n,txt in s.configs.items():
        assert render(parse(txt))==txt, n
    tw=Twin(s); t1=time.time()
    pol=s.policy["invariants"]
    bad=[]
    for tgt in pol["mgmt_reachable"]["targets"]:
        r=tw.reach(pol["mgmt_reachable"]["src"],tgt,"icmp")
        if r["disp"]!="ACCEPTED": bad.append(("mgmt",tgt,r["disp"],r["detail"]))
    for a,c in pol["isolation"]:
        for sv in SERVICES:
            if tw.allowed(a,c,sv): bad.append(("iso",a,c,sv))
    for e in pol["no_leak"]["externals"]:
        for p,rr in tw.route_table(e).items():
            if rr.proto=="connected": continue
            import ipaddress
            for f in pol["no_leak"]["forbidden"]:
                if ipaddress.ip_network(p).subnet_of(ipaddress.ip_network(f)): bad.append(("leak",e,p))
    hosts=tw.endpoints(); loops=0; acc=0; tot=0
    for a in hosts:
        for c in hosts:
            if a==c: continue
            for sv in SERVICES:
                r=tw.reach(a,c,sv); tot+=1
                acc+= r["disp"]=="ACCEPTED"
                if "LOOP" in r["disp"]: loops+=1
    t2=time.time()
    adj=tw.all_adjacencies(); down=[k for k,v in adj.items() if v!="up"]
    loads=tw.link_loads(); mx=max(loads.items(), key=lambda kv: kv[1])
    conv=[(a,c,tw.convergence(a,c)["seconds"]) for a,c in s.policy["redundant_links"]]
    badc=[x for x in conv if x[2] is None or x[2]>3]
    print(f"== {name}: devs={len(s.devices)} hosts={len(hosts)} links={len(s.links)} build={t1-t0:.2f}s flows={tot} accepted={acc} loops={loops} allpairs={t2-t1:.2f}s")
    print("  adj down:",down, " bgp:",tw.bgp_sessions())
    print("  max load:",mx, " conv bad:",badc[:5], " conv max:",max(c[2] for c in conv))
    print("  invariant problems:",bad[:8])
    # matrix sample
    for a in hosts[:30]:
        row=[c for c in hosts if c!=a and tw.allowed(a,c,"icmp")]
        print("   ",a,"icmp->",len(row), [c for c in hosts if c!=a and not tw.allowed(a,c,"icmp")][:6])
