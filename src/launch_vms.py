#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import os
from fabric import Connection, Config
import getpass
import asyncio

from async_process_utils import ConnectionWrapper, run_single_command, check_file_exists, validate_image_use, MacVlan, MacVtap, User, Tap, Bridge, create_connection_from_config

from async_fs_utils import copy_to_remote

import argparse

async def ensure_file_image(local_path: str, remote_path: str, connectionWrapper: ConnectionWrapper, overwrite_image: bool = False):
    if remote_path == "":
        print("Error: 'remote_disk_image_path' is required in the JSON file.")
        sys.exit(1)

    file_exists = await check_file_exists(remote_path, connectionWrapper)

    if file_exists:
        if not await validate_image_use(remote_path, connectionWrapper):
            print(
                f"Error: Disk image '{remote_path}' is currently in use by another qemu process")
            sys.exit(1)

    if overwrite_image or not file_exists:
        if os.path.exists(local_path):
            print(
                f"Copying existing disk image '{local_path}' to remote image '{remote_path}'")
            await copy_to_remote(local_path, remote_path, connectionWrapper)
        else:
            print(
                f"Error: Local disk image '{local_path}' does not exist to overwrite remote image.")
            sys.exit(1)



async def launch_single_vm(vm_configuration: dict, connectionWrapper: ConnectionWrapper, overwrite_image: bool = True, kill_running_vms=True):
    """
    Run the VM with the given configuration.
    """

    binary = "qemu-system-x86_64"

    args = ["-accel", "kvm", "-cpu", "max"]

    display_mode = ""

    file_exists = await check_file_exists(
        vm_configuration["remote_disk_image_path"], connectionWrapper)

    if file_exists:
        if not await validate_image_use(vm_configuration["remote_disk_image_path"], connectionWrapper, kill_running_vms=kill_running_vms):
            print(
                f"Error: Disk image '{vm_configuration['remote_disk_image_path']}' is currently in use by another qemu process")
            sys.exit(1)

    for key, value in vm_configuration.items():
        if key == "memory":
            args.append("-m")
            args.append(str(value))

        elif key == "cpu_count":
            args.append("-smp")
            args.append(str(value))

        elif key == "virtfs_path":
            if value == "{pwd}":
                path = os.getcwd()
            else:
                path = value
            args.append("-virtfs")
            args.append("local,path=" + str(path) +
                        ",security_model=mapped-xattr,mount_tag=share,id=share")

        elif key == "interfaces":
            i = 4
            for interface in value:
                match interface["type"]:
                    case "macvtap":
                        macvtap = MacVtap(interface, connectionWrapper)
                        await macvtap.create()
                        args.extend(await macvtap.get_args(i))
                        i = i + 1

                    case "tap":
                        tap = Tap(interface, connectionWrapper)
                        await tap.create()
                        args.extend(await tap.get_args(i))
                        i = i + 1

                    case "user":
                        user = User(interface, connectionWrapper)
                        user.create()
                        args.extend(user.get_args())

                    case _:
                        print(
                            f"""
                            Error: Unknown interface type {interface['type']}.
                            Supported types are 'macvtap', 'tap', and 'user'.
                            """)
                        sys.exit(1)

        elif key == "display_mode":
            if value == "background":
                display_mode = "background"
                args.extend(["-vga", "none", "-serial", "none", "-nographic"])
            elif value == "terminal":
                args.extend(["-vga", "none", "-nographic"])
            elif value == "graphic":
                display_mode = "graphic"
                args.extend(["-vga", "virtio"])
            else:
                print(f"Error: Unknown display mode '{value}'.")
                sys.exit(1)

    drive_number = 0
    if vm_configuration.get("remote_disk_image_path", "") == "":
        print("Error: 'remote_disk_image_path' is required in the JSON file.")
        sys.exit(1)

    await ensure_file_image(vm_configuration.get("local_disk_image_path", ""), vm_configuration["remote_disk_image_path"], connectionWrapper, overwrite_image)
    args.append("-drive")
    args.append(f"file={vm_configuration['remote_disk_image_path']},format=qcow2,if=virtio,index={drive_number},media=disk")
    drive_number += 1

    for value in vm_configuration.get("additional_disk_images", []):
        local_path = value.get("local_disk_image_path", "")
        remote_path = value.get("remote_disk_image_path", "")
        await ensure_file_image(local_path, remote_path, connectionWrapper, overwrite_image)
        args.append("-drive")
        args.append(f"file={remote_path},format=qcow2,if=virtio,index={drive_number},media=disk")
        drive_number += 1

    print(f"Launching VM with command: {binary} {' '.join(args)}")
    if (display_mode == "background" or display_mode == "graphic"):
        await run_single_command(f"{binary} {' '.join(args)}", connectionWrapper,
                                 no_pipe=True, fail_on_returncode=True, asynchronous=False, disown=True, no_output=True)
    else:
        await run_single_command(f"{binary} {' '.join(args)}", connectionWrapper, fail_on_returncode=True, disown=False, asynchronous=False, pty=True, no_pipe=False)


async def setup_host_network(host_network: list[dict], connectionWrapper: ConnectionWrapper):
    for iface in host_network:
        match iface["type"]:
            case "bridge":
                bridge = Bridge(iface, connectionWrapper)
                await bridge.create()
            case "macvlan":
                macvlan = MacVlan(iface, connectionWrapper)
                await macvlan.create()
            case _:
                print(
                    f"Error: Unknown host network interface type '{iface['type']}'. Supported types are 'bridge' and 'macvlan'.")
                sys.exit(1)


async def run_vms_on_single_host(vms: list[dict], host_network: list[dict] | None, ssh_configuration: dict, overwrite_image: bool = True, kill_running_vms=True):
    fabric_config = Config()
    password = getpass.getpass(
        f"Sudo password for {ssh_configuration.get('host')}: ")
    fabric_config.sudo.password = password

    if host_network is not None:
        print("setting up host network")
        connectionWrapper = await create_connection_from_config(ssh_configuration, fabric_config=fabric_config)
        await setup_host_network(host_network, connectionWrapper)

    results = []

    for vm_configuration in vms:
        connectionWrapper = await create_connection_from_config(
            ssh_configuration, fabric_config=fabric_config)
        results.append(asyncio.create_task(launch_single_vm(vm_configuration, connectionWrapper,
                                                            overwrite_image=overwrite_image,
                                                            kill_running_vms=kill_running_vms)))

    results = await asyncio.gather(*results)
    print(
        f"Running VMS on host {ssh_configuration.get("host", "localhost")} done")


async def main():
    """
    Main function to run the script.
    """

    parser = argparse.ArgumentParser(
        description="Launch VMs based on a JSON configuration file.")
    parser.add_argument('json_path', type=str,
                        help='Path to the JSON configuration file.')
    parser.add_argument('--overwrite-image', action='store_true',
                        help='Do not overwrite the remote disk image if it already exists.')

    args = parser.parse_args()
    overwrite_image = args.overwrite_image

    try:
        with open(args.json_path, 'r') as f:
            configuration = json.load(f)
    except Exception as e:
        print(f"Error reading JSON file: {e}")
        sys.exit(1)

    results = []

    for host_configuration in configuration:
        if host_configuration.get("host") is None:
            host = "localhost"
        else:
            host = host_configuration.get("host")

        results.append(asyncio.create_task(run_vms_on_single_host(host_configuration.get(
            "vms", []), host_configuration.get("host_network", None), host_configuration.get("ssh_config", None), overwrite_image, True)))

    results = await asyncio.gather(*results)
    print(f"Running on hosts done")


if __name__ == "__main__":
    asyncio.run(main())
