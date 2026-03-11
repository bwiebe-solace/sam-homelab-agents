"""
MCP stdio server providing Proxmox VE management tools.

Communicates with the Proxmox VE REST API using API token authentication.

Tools:
  - list_nodes:       List all Proxmox cluster nodes with status and resource usage
  - list_vms:         List VMs on a node (auto-discovers node if omitted)
  - get_vm_status:    Full status for a specific VM by vmid
  - start_vm:         Start a VM and wait for the task to complete
  - stop_vm:          Hard power-off a VM and wait for the task to complete
  - shutdown_vm:      Gracefully shut down a VM and wait for the task to complete
  - snapshot_vm:      Create a snapshot of a VM
  - list_snapshots:   List snapshots for a VM
  - delete_snapshot:  Delete a specific snapshot
  - get_node_status:  CPU, memory, disk, and uptime for a node
"""

import asyncio
import json
import os
from datetime import date
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

PROXMOX_HOST = os.environ["PROXMOX_HOST"].rstrip("/")
PROXMOX_USER = os.environ["PROXMOX_USER"]
PROXMOX_TOKEN_NAME = os.environ["PROXMOX_TOKEN_NAME"]
PROXMOX_TOKEN_VALUE = os.environ["PROXMOX_TOKEN_VALUE"]
PROXMOX_VERIFY_SSL = os.environ.get("PROXMOX_VERIFY_SSL", "false").lower() == "true"

_client = httpx.AsyncClient(
    base_url=f"{PROXMOX_HOST}/api2/json",
    headers={"Authorization": f"PVEAPIToken={PROXMOX_USER}!{PROXMOX_TOKEN_NAME}={PROXMOX_TOKEN_VALUE}"},
    verify=PROXMOX_VERIFY_SSL,
    timeout=30.0,
)

server = Server("proxmox-tools")


async def _get_first_node() -> str:
    resp = await _client.get("/nodes")
    nodes = resp.json().get("data", [])
    if not nodes:
        raise ValueError("No Proxmox nodes found")
    return nodes[0]["node"]


async def _wait_for_task(node: str, upid: str, timeout: int = 30) -> dict:
    """Poll GET /nodes/{node}/tasks/{upid}/status until status != 'running'."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await _client.get(f"/nodes/{node}/tasks/{upid}/status")
        data = resp.json().get("data", {})
        if data.get("status") != "running":
            return data
        await asyncio.sleep(1)
    return {"status": "timeout", "exitstatus": "timeout"}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_nodes",
            description=(
                "List all Proxmox cluster nodes. Returns each node's name, online status, "
                "CPU usage, and memory usage. Call this first if you don't know the node name."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list_vms",
            description=(
                "List all QEMU virtual machines on a Proxmox node. Returns vmid, name, status, "
                "CPU, and memory for each VM. If node is omitted, the first available node is used."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_vm_status",
            description=(
                "Get the full current status of a specific VM, including power state, CPU, memory, "
                "disk I/O, and network I/O."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID (numeric). Use list_vms to find the vmid for a VM by name.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "required": ["vmid"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="start_vm",
            description=(
                "Start a stopped VM. Waits for the task to complete and returns the result. "
                "Use list_vms to find the vmid by VM name before calling this."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID to start.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "required": ["vmid"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="stop_vm",
            description=(
                "Hard power-off a VM immediately. This is equivalent to pulling the power cord — "
                "data loss may occur. Prefer shutdown_vm for graceful shutdown unless the VM is "
                "unresponsive. Waits for the task to complete."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID to stop.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "required": ["vmid"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="shutdown_vm",
            description=(
                "Gracefully shut down a VM by sending an ACPI shutdown signal. The guest OS "
                "performs a clean shutdown. Prefer this over stop_vm unless the VM is unresponsive. "
                "Waits for the task to complete."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID to shut down.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "required": ["vmid"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="snapshot_vm",
            description=(
                "Create a snapshot of a VM. If snapname is not provided, a name is auto-generated "
                "in the format snap-YYYY-MM-DD. Waits for the task to complete."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID to snapshot.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                    "snapname": {
                        "type": "string",
                        "description": "Snapshot name. If omitted, auto-generated as snap-YYYY-MM-DD.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional description for the snapshot.",
                    },
                },
                "required": ["vmid"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="list_snapshots",
            description="List all snapshots for a specific VM.",
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "required": ["vmid"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="delete_snapshot",
            description=(
                "Delete a specific snapshot from a VM. This is irreversible — always confirm "
                "with the user before deleting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "The VM ID.",
                    },
                    "snapname": {
                        "type": "string",
                        "description": "The name of the snapshot to delete.",
                    },
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "required": ["vmid", "snapname"],
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="get_node_status",
            description="Get CPU, memory, disk usage, and uptime for a specific Proxmox node.",
            inputSchema={
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Proxmox node name. If omitted, the first available node is used automatically.",
                    },
                },
                "additionalProperties": False,
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        if name == "list_nodes":
            result = await _list_nodes()
        elif name == "list_vms":
            result = await _list_vms(arguments)
        elif name == "get_vm_status":
            result = await _get_vm_status(arguments)
        elif name == "start_vm":
            result = await _start_vm(arguments)
        elif name == "stop_vm":
            result = await _stop_vm(arguments)
        elif name == "shutdown_vm":
            result = await _shutdown_vm(arguments)
        elif name == "snapshot_vm":
            result = await _snapshot_vm(arguments)
        elif name == "list_snapshots":
            result = await _list_snapshots(arguments)
        elif name == "delete_snapshot":
            result = await _delete_snapshot(arguments)
        elif name == "get_node_status":
            result = await _get_node_status(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]


async def _list_nodes() -> list[dict[str, Any]]:
    resp = await _client.get("/nodes")
    resp.raise_for_status()
    nodes = resp.json().get("data", [])
    return [
        {
            "node": n.get("node"),
            "status": n.get("status"),
            "cpu": n.get("cpu"),
            "maxcpu": n.get("maxcpu"),
            "mem": n.get("mem"),
            "maxmem": n.get("maxmem"),
            "uptime": n.get("uptime"),
        }
        for n in nodes
    ]


async def _list_vms(args: dict[str, Any]) -> list[dict[str, Any]]:
    node = args.get("node") or await _get_first_node()
    resp = await _client.get(f"/nodes/{node}/qemu")
    resp.raise_for_status()
    vms = resp.json().get("data", [])
    return [
        {
            "vmid": v.get("vmid"),
            "name": v.get("name"),
            "status": v.get("status"),
            "cpu": v.get("cpu"),
            "maxcpu": v.get("maxcpu"),
            "mem": v.get("mem"),
            "maxmem": v.get("maxmem"),
        }
        for v in vms
    ]


async def _get_vm_status(args: dict[str, Any]) -> dict[str, Any]:
    vmid = args["vmid"]
    node = args.get("node") or await _get_first_node()
    resp = await _client.get(f"/nodes/{node}/qemu/{vmid}/status/current")
    resp.raise_for_status()
    return resp.json().get("data", {})


async def _start_vm(args: dict[str, Any]) -> dict[str, Any]:
    vmid = args["vmid"]
    node = args.get("node") or await _get_first_node()
    resp = await _client.post(f"/nodes/{node}/qemu/{vmid}/status/start")
    resp.raise_for_status()
    upid = resp.json().get("data", "")
    task_result = await _wait_for_task(node, upid)
    return {"upid": upid, "task_result": task_result}


async def _stop_vm(args: dict[str, Any]) -> dict[str, Any]:
    vmid = args["vmid"]
    node = args.get("node") or await _get_first_node()
    resp = await _client.post(f"/nodes/{node}/qemu/{vmid}/status/stop")
    resp.raise_for_status()
    upid = resp.json().get("data", "")
    task_result = await _wait_for_task(node, upid)
    return {"upid": upid, "task_result": task_result}


async def _shutdown_vm(args: dict[str, Any]) -> dict[str, Any]:
    vmid = args["vmid"]
    node = args.get("node") or await _get_first_node()
    resp = await _client.post(f"/nodes/{node}/qemu/{vmid}/status/shutdown")
    resp.raise_for_status()
    upid = resp.json().get("data", "")
    task_result = await _wait_for_task(node, upid)
    return {"upid": upid, "task_result": task_result}


async def _snapshot_vm(args: dict[str, Any]) -> dict[str, Any]:
    vmid = args["vmid"]
    node = args.get("node") or await _get_first_node()
    snapname = args.get("snapname") or f"snap-{date.today().isoformat()}"
    description = args.get("description", "")
    body = {"snapname": snapname, "description": description}
    resp = await _client.post(f"/nodes/{node}/qemu/{vmid}/snapshot", json=body)
    resp.raise_for_status()
    upid = resp.json().get("data", "")
    task_result = await _wait_for_task(node, upid)
    return {"snapname": snapname, "upid": upid, "task_result": task_result}


async def _list_snapshots(args: dict[str, Any]) -> list[dict[str, Any]]:
    vmid = args["vmid"]
    node = args.get("node") or await _get_first_node()
    resp = await _client.get(f"/nodes/{node}/qemu/{vmid}/snapshot")
    resp.raise_for_status()
    return resp.json().get("data", [])


async def _delete_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    vmid = args["vmid"]
    snapname = args["snapname"]
    node = args.get("node") or await _get_first_node()
    resp = await _client.delete(f"/nodes/{node}/qemu/{vmid}/snapshot/{snapname}")
    resp.raise_for_status()
    upid = resp.json().get("data", "")
    task_result = await _wait_for_task(node, upid)
    return {"snapname": snapname, "upid": upid, "task_result": task_result}


async def _get_node_status(args: dict[str, Any]) -> dict[str, Any]:
    node = args.get("node") or await _get_first_node()
    resp = await _client.get(f"/nodes/{node}/status")
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return {
        "node": node,
        "cpu": data.get("cpu"),
        "cpuinfo": data.get("cpuinfo"),
        "memory": data.get("memory"),
        "rootfs": data.get("rootfs"),
        "uptime": data.get("uptime"),
        "loadavg": data.get("loadavg"),
        "kversion": data.get("kversion"),
    }


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
