# MCDeploy

MCDeploy is an end-to-end deployment stack for a self-hosted Minecraft server. It combines
a containerized monitoring and scheduling layer with an Ansible-driven provisioning workflow,
so that a fresh target machine can be turned into a running, observable Minecraft server with
a single, repeatable process.

The stack is designed to run its control plane on a small always-on host (for example a
Raspberry Pi) while the actual game server is provisioned onto a separate, more powerful
machine over SSH.

## What it does

- Provisions a [NeoForge](https://neoforged.net/) Minecraft server, including the required
  Java runtime, via Ansible.
- Manages the server as a `systemd` service, with EULA acceptance, JVM tuning and RCON
  configuration handled automatically.
- Ships a Prometheus node exporter to the target host so system metrics are available for
  scraping.
- Runs a monitoring stack (Prometheus and Grafana) and an nginx front end in Docker.
- Re-runs the provisioning playbook on a schedule through Ofelia, keeping the target host in
  its declared state.

## Architecture

The control host runs the Docker stack defined in
[`Raspi-Stack/docker/docker-compose.yaml`](Raspi-Stack/docker/docker-compose.yaml):

| Service          | Purpose                                                             |
| ---------------- | ------------------------------------------------------------------ |
| `prometheus`     | Scrapes metrics from the target host and the local exporters.      |
| `grafana`        | Dashboards on top of Prometheus.                                   |
| `web_server`     | nginx front end.                                                   |
| `nginx_exporter` | Exposes nginx metrics to Prometheus.                               |
| `ansible`        | Runs the deployment playbook against the target host over SSH.     |
| `scheduler`      | Ofelia daemon that triggers the Ansible run on a nightly schedule. |

The Ansible layer in [`Raspi-Stack/ansible/`](Raspi-Stack/ansible/) applies three roles to
the target host, in order: `prometheus_exporter`, `java`, and `minecraft`. See
[`docs/architecture.md`](docs/architecture.md) for a more detailed breakdown.

## Repository layout

```
Raspi-Stack/
  ansible/
    deploy.yaml                    Playbook: exporter, Java, Minecraft
    inventory/group_vars/          Host connection details (see .example file)
    roles/
      java/                        Installs the OpenJDK runtime
      minecraft/                   Installs and manages the NeoForge server
      prometheus_exporter/         Installs the node exporter
  docker/
    docker-compose.yaml            Monitoring and scheduling stack
    dockerfile                     Ansible runner image
    prometheus/prometheus.yaml     Scrape configuration
    templates/                     nginx configuration templates
config.yaml                        Deployment configuration
deploy.sh                          Deployment entry point
docs/
  architecture.md                  Design and component overview
  troubleshooting.md               Common problems and fixes
```

## Requirements

- A control host with Docker and Docker Compose installed.
- A target host reachable over SSH, with a user that has `sudo` privileges.
- An SSH key pair, with the public key installed on the target host.

## Configuration

Before deploying, provide the connection details for your target host. Copy the example
inventory file and fill in your own values:

```bash
cp Raspi-Stack/ansible/inventory/group_vars/main_server.yaml.example \
   Raspi-Stack/ansible/inventory/group_vars/main_server.yaml
```

Set `ansible_host`, `ansible_user` and `ansible_ssh_private_key_file` to match your
environment. Review the Minecraft defaults in
[`Raspi-Stack/ansible/roles/minecraft/defaults/main.yaml`](Raspi-Stack/ansible/roles/minecraft/defaults/main.yaml)
and override anything you want to change — in particular the RCON password, which ships with
a placeholder value.

Note that `main_server.yaml`, `inventory.ini` and private keys are excluded from version
control via `.gitignore`, so your credentials stay local.

## Installation

<!--
TODO (fill in): step-by-step installation instructions for your setup.
Suggested points to cover:
- How to clone the repository onto the control host.
- Which values in config.yaml / the inventory must be set first.
- The exact command(s) used to bring up the Docker stack.
- How the first Ansible run is triggered (manually vs. via the scheduler).
- Any one-time setup on the target host (SSH key, sudo, firewall/ports).
-->

1. _TODO: describe the first installation step._
2. _TODO: describe the next step._
3. _TODO: continue as needed._

## Testing

<!--
TODO (fill in): how to verify that a deployment succeeded.
Suggested points to cover:
- How to confirm the Minecraft service is running (e.g. systemctl status on the target).
- How to connect a client to the server and the expected result.
- How to check that Prometheus is scraping the target and Grafana shows data.
- How to verify RCON access.
-->

1. _TODO: describe the first verification step._
2. _TODO: describe the next step._
3. _TODO: continue as needed._

## Troubleshooting

If something does not work as expected, see [`docs/troubleshooting.md`](docs/troubleshooting.md)
for known issues and their resolutions.
