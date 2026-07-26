#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="${SCRIPT_DIR}/ansible"
DOCKER_DIR="${SCRIPT_DIR}/docker"

echo "=== MCDeploy Setup ==="

read -p "Path to SSH private key [~/.ssh/id_ed25519_ms]: " SSH_KEY_PATH
SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/id_ed25519_ms}"

read -s -p "RCON password: " RCON_PASSWORD
echo

# --- group_vars/main_server.yaml aktualisieren ---
MAIN_SERVER_FILE="${ANSIBLE_DIR}/inventory/group_vars/main_server.yaml"

if grep -q "^ansible_ssh_private_key_file:" "$MAIN_SERVER_FILE"; then
    sed -i "s#^ansible_ssh_private_key_file:.*#ansible_ssh_private_key_file: ${SSH_KEY_PATH}#" "$MAIN_SERVER_FILE"
else
    echo "ansible_ssh_private_key_file: ${SSH_KEY_PATH}" >> "$MAIN_SERVER_FILE"
fi

# --- minecraft_forge_rcon_password aktualisieren ---
MINECRAFT_DEFAULTS_FILE="${ANSIBLE_DIR}/roles/minecraft/defaults/main.yaml"

sed -i "s#^minecraft_forge_rcon_password:.*#minecraft_forge_rcon_password: \"${RCON_PASSWORD}\"#" "$MINECRAFT_DEFAULTS_FILE"

echo "Konfiguration aktualisiert."

# --- Docker Compose Stack starten ---
echo "Starte Docker-Container..."
(cd "$DOCKER_DIR" && docker compose up -d)

echo "Fertig."