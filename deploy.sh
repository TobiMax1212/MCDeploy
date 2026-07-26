#!/bin/bash
set -euo pipefail

# --- Sicherheitscheck: nicht als root ausführen ---
if [ "$(id -u)" -eq 0 ]; then
    echo "Bitte dieses Skript NICHT als root/mit sudo ausführen." >&2
    echo "Der SSH-Key soll unter deinem eigenen Home-Verzeichnis landen, nicht unter /root." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="${SCRIPT_DIR}/Raspi-Stack"
ANSIBLE_DIR="${STACK_DIR}/ansible"
DOCKER_DIR="${STACK_DIR}/docker"

echo "=== MCDeploy Setup ==="

# --- SSH-Keypair ---
read -p "Path for SSH key [~/.ssh/id_ed25519_ms]: " SSH_KEY_PATH
SSH_KEY_PATH="${SSH_KEY_PATH:-~/.ssh/id_ed25519_ms}"
SSH_KEY_PATH="${SSH_KEY_PATH/#\~/$HOME}"   # ~ zu $HOME expandieren, falls eingegeben

if [ -f "$SSH_KEY_PATH" ]; then
    echo "Key existiert bereits unter ${SSH_KEY_PATH}, überspringe Erzeugung."
else
    mkdir -p "$(dirname "$SSH_KEY_PATH")"
    chmod 700 "$(dirname "$SSH_KEY_PATH")"
    ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "mcdeploy"
    echo "Neues Schlüsselpaar erzeugt: ${SSH_KEY_PATH}"
fi

chmod 600 "$SSH_KEY_PATH"
chmod 644 "${SSH_KEY_PATH}.pub"
echo "Berechtigungen gesetzt: ${SSH_KEY_PATH} ist nur für dich lesbar (600)."

read -p "Xeon Server IP: " SERVER_IP
read -p "SSH User [tobimax]: " SSH_USER
SSH_USER="${SSH_USER:-tobimax}"

echo "Kopiere Public Key auf ${SERVER_IP} (Passwort wird einmalig benötigt)..."
ssh-copy-id -i "${SSH_KEY_PATH}.pub" "${SSH_USER}@${SERVER_IP}"

# --- RCON ---
read -s -p "RCON password: " RCON_PASSWORD
echo

# --- group_vars/main_server.yaml aktualisieren ---
MAIN_SERVER_FILE="${ANSIBLE_DIR}/inventory/group_vars/main_server.yaml"
CONTAINER_KEY_PATH="/root/.ssh/$(basename "$SSH_KEY_PATH")"

if grep -q "^ansible_ssh_private_key_file:" "$MAIN_SERVER_FILE"; then
    sed -i "s#^ansible_ssh_private_key_file:.*#ansible_ssh_private_key_file: ${CONTAINER_KEY_PATH}#" "$MAIN_SERVER_FILE"
else
    echo "ansible_ssh_private_key_file: ${CONTAINER_KEY_PATH}" >> "$MAIN_SERVER_FILE"
fi

# --- minecraft_forge_rcon_password aktualisieren ---
MINECRAFT_DEFAULTS_FILE="${ANSIBLE_DIR}/roles/minecraft/defaults/main.yaml"

sed -i "s#^minecraft_forge_rcon_password:.*#minecraft_forge_rcon_password: \"${RCON_PASSWORD}\"#" "$MINECRAFT_DEFAULTS_FILE"

echo "Konfiguration aktualisiert."

# --- Docker Compose Stack starten ---
read -p "Docker-Stack jetzt starten (docker compose up -d)? [y/N]: " START_DOCKER
if [[ "$START_DOCKER" =~ ^[Yy]$ ]]; then
    echo "Starte Docker-Container..."
    (cd "$DOCKER_DIR" && sudo docker compose up -d)
else
    echo "Docker-Stack wird übersprungen."
fi

echo "Fertig."