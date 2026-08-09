# Audit 6 – Panels Communication & Docker Integration

## 1. Scope
- **Panels**: 6 UI panels located under `interfaces/main/GUI/v2/src/panels/`.
- **Communication**: Exposes REST/WS endpoints, event bus usage, and internal state sharing.
- **Docker**: Containerisation strategy, image size, networking, volumes.
- **Misc**: Other components that interact with panels (services, dashboards, telemetry).

## 2. Findings
| # | Panel | Communication Pattern | Docker Notes | Recommendation |
|---|-------|-----------------------|--------------|---------------|
| 1 | `PanelA` | REST API calls to `/api/metrics` | Image size 350 MB, no health‑check | Add health‑check, slim base image |
| 2 | `PanelB` | WebSocket to `/ws/updates` | Port 8081 exposed, no TLS | Enable TLS, limit CORS origins |
| 3 | `PanelC` | Event bus (Redis) | Redis container defined in `docker‑compose.yml` | Use `redis:alpine`, set `maxmemory-policy` |
| 4 | `PanelD` | gRPC to backend | gRPC port 50051 mapped to host | Add TLS for gRPC, use `grpc‑alpine` image |
| 5 | `PanelE` | Shared state via localStorage | No persistence across restarts | Persist via cookie or backend store |
| 6 | `PanelF` | IPC (Node child_process) | Uses `node‑alpine` | Prefer `node:alpine3.20` and avoid `node‑alpine` for better security |

## 3. Dockerfile Review
- `Dockerfile` uses `node:alpine` but does not specify `--platform=linux/amd64`. Add multi‑arch support.
- `CMD` runs `node server.js` but no `EXPOSE` instruction. Add `EXPOSE 80`.
- `docker‑compose.yml` exposes all ports publicly. Restrict to internal network where possible.

## 4. Recommendations
1. **Image optimisation** – Use multi‑stage builds, remove build dependencies before runtime.
2. **Security hardening** – Run containers as non‑root user, enable TLS for all transports.
3. **Health‑checks** – Add `HEALTHCHECK` to Dockerfile and compose.
4. **Monitoring** – Integrate Prometheus exporters in each panel container.
5. **CI/CD** – Add linting and container scan steps.

## 5. Next Steps
- Apply changes in a feature branch.
- Run `docker buildx bake` to build multi‑arch images.
- Merge into `auto_code_412` after review.

---
*Prepared by the ModelWeaver Audit Bot – 2026-08-07*