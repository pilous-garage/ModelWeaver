"""Routes /infra/* pour lire les domaines sqlite env/modules/services/runtime."""
from services.api.router import register
from modules.sqlite.services import db as sdb, read as SR
from modules.sqlite.modules import db as mdb, read as MR
from modules.sqlite.runtime import db as rdb, read as RR
from modules.sqlite.env import db as edb, read as ER

def op_infra_services(_params):
    d = sdb(); svcs = SR.list_services(d); d.close()
    return {"services": svcs, "count": len(svcs)}

def op_infra_modules(_params):
    d = mdb()
    mods = []
    for m in MR.list_modules(d):
        routes = MR.list_routes(d, m["path"])
        mods.append({"path": m["path"], "type": m["type"],
                     "status": m["status"], "routes": [{"path": r["path"],
                     "name": r["name"], "signature": r.get("signature","?")} for r in routes],
                     "routes_count": len(routes)})
    d.close()
    return {"modules": mods, "count": len(mods)}

def op_infra_runtime(_params):
    d = rdb()
    state = RR.get_state(d)
    services = RR.list_runtime(d, status="running")
    d.close()
    return {"runtime_state": state, "service_runtime": services,
            "service_runtime_count": len(services)}

register("infra/services", op_infra_services)
register("infra/modules", op_infra_modules)
register("infra/runtime", op_infra_runtime)
