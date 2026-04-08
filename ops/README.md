# CORSHub — Operations

Deployment instructions for the `corshub` Helm chart, published as an OCI
artifact on Harbor.

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| Helm | 3.8 (OCI support GA) |
| kubectl | matching the target cluster |
| Access to `harbor.peinser.com` | read credentials required |

## Authenticate with Harbor

```bash
helm registry login harbor.peinser.com \
  --username <your-username> \
  --password-stdin
```

## Render the chart locally

```bash
helm template corshub \
  oci://harbor.peinser.com/uas/charts/corshub \
  --version <version> \
  --values ops/values.yaml
```

## Install / upgrade

```bash
helm upgrade --install corshub \
  oci://harbor.peinser.com/uas/charts/corshub \
  --version <version> \
  --values ops/values.yaml \
  --namespace corshub \
  --create-namespace \
  --atomic \
  --timeout 120s
```

`--atomic` rolls back automatically if the deployment does not become healthy
within `--timeout`.

## Available chart versions

```bash
helm show chart oci://harbor.peinser.com/uas/charts/corshub
```

Or browse the Harbor UI at `https://harbor.peinser.com`.

## Configuration

Edit `ops/values.yaml` before rendering or installing.  Key decisions:

| Key | Default | Notes |
|-----|---------|-------|
| `image.tag` | `""` (chart `appVersion`) | **Always pin in production.** |
| `replicaCount` | `1` | Scale with caution — see below. |
| `ingress.enabled` | `false` | Enable **or** `httpRoute.enabled`, not both. |
| `httpRoute.enabled` | `false` | Preferred for Gateway API clusters. |
| `app.workers` | `4` | Sanic worker processes per pod. |

### Multi-replica deployments

The default transport is an in-process `asyncio.Queue`.  With more than one
replica, a base station and its rovers must land on the **same pod** or they
will not exchange corrections.  Options:

- Use a session-affinity `Service` (`sessionAffinity: ClientIP`).
- Replace the transport backend with a shared broker (Redis, NATS) and set
  `replicaCount` freely.
