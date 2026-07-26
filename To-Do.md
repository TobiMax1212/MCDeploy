# Homelab / MCDeploy – To Do Later

## Prometheus Exporters
- [ ] Build `smartctl_exporter` role (build variant with `make`, analogous to `ipmi_exporter`)
- [ ] Build `ipmi_exporter` role (build variant, needs real BMC – not testable in a VM)
- [ ] Set up Prometheus server role on the Pi (scrape_configs for all exporters, template-based)
- [ ] Set up Grafana role on the Pi (datasource config against Prometheus)
- [ ] Fine-tune scrape intervals per job (node: 15s, ipmi: 30–60s, smartctl: 5–10min)

## Minecraft / NeoForge
- [ ] Add multi-server-capable structure (`minecraft_forge_server_name`, parameterize paths + systemd unit names)
- [ ] Manage RCON password properly (move out of `defaults/main.yaml` into `group_vars`/`host_vars` + `.gitignore`, or Ansible Vault)
- [ ] Task/mechanism for changing the RCON password without manually deleting `server.properties` (e.g. `lineinfile` instead of the full template)
- [ ] Optional: marker-file logic for "deliberately stopped" vs. "crashed", so Ansible doesn't automatically bring a manually stopped server back up

## Logging / Observability
- [ ] Loki container role on the Pi (Docker Compose, analogous to Prometheus)
- [ ] Promtail role on the Xeon (reads `logs/latest.log`, pushes to Loki)
- [ ] Add Loki as an additional datasource in Grafana
- [ ] Dashboard/panel for join/leave events, errors/warnings from the Minecraft log
- [ ] Alert rules for critical log patterns (e.g. `Exception`, `OutOfMemoryError`)

## Drift Detection / Automation
- [ ] Finalize Ofelia job configuration (schedule, clarify `job-exec` vs. `job-run`)
- [ ] Wrapper logic: evaluate `ansible-playbook` exit codes (distinguish unreachable vs. actual task failure)
- [ ] Prometheus textfile collector for Ansible run status (`ansible_drift_detected`, `ansible_host_unreachable`)
- [ ] Build separate Grafana alerts for drift detection vs. host unreachable
- [ ] Optional: AIDE/Tripwire for real file integrity checking (independent of Ansible check mode)

## Deploy Script / End-Goal Automation
- [ ] Interactive deploy script (prompts for variables like server version, IP addresses)
- [ ] Bake generated YAMLs into the Docker image
- [ ] Compose setup that ties together the Ansible container + Ofelia + generated config

## Firewall / Network
- [ ] `ufw` rules for exporter ports (allow only the Pi IP, not the whole subnet)
- [ ] `ufw` rule for the RCON port (25575), reachable only from required sources
- [ ] Network access between Xeon and Pi for Promtail → Loki push (if logging is implemented)

## Miscellaneous / Cleanup
- [ ] Keep `.yamllint` rules consistent project-wide (document why the line-length limit is 200 instead of 160)