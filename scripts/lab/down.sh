#!/usr/bin/env bash
# PVE environment: VMs are managed by Proxmox, not this script.
# This is a no-op placeholder kept for compatibility.
set -euo pipefail

echo "PVE lab: VM lifecycle is managed by Proxmox (not this script)."
echo "To stop services on the controller, SSH in and run:"
echo "  ssh ubuntu@\${AADS_CONTROLLER_IP} 'cd ~/AADS && sudo docker compose --env-file .env.lab down'"
