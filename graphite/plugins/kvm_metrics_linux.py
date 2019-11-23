#!/usr/bin/env python3

import sys
import libvirt
import psutil
import time
import argparse
import traceback


""" Usage:
  ./kvm_metrics_linux.py -s "hypervisors.mig.staging.tempest" """

def get_vm_info():
    argv_parser = argparse.ArgumentParser(description="Provides basic metrics for each VM on the HyperVisor",
                                          usage="kvm_metrics_linux.py -s 'hypervisors.mig.staging.tempest'")
    argv_parser.add_argument("-s", "--scheme", help="Scheme for the Graphite. So you can prepend the result with your value: "
                                                    "like: --scheme :::hypervisors.mig.staging.tempest:::.RESULT_NAME RESULT_VALUE")
    args = vars(argv_parser.parse_args())
    HYPERVISOR_SPECS = {}
    VMS_SPECS = []

    mem = psutil.virtual_memory()
    ram_total = mem.total
    cpu_count_total = psutil.cpu_count()

    conn = libvirt.openReadOnly()
    if conn is None:
        print('Failed to open connection to the HyperVisor!', file=sys.stderr)
        sys.exit(3)

    try:

        domainIDs = conn.listDomainsID()
        if domainIDs is None:
            print('Failed to get domainIDs on the HyperVisor!', file=sys.stderr)
            sys.exit(3)
        hostname = conn.getHostname()

        HYPERVISOR_SPECS.update({'ram_total': ram_total, 'ram_free': conn.getFreeMemory(), 'cpu_count_total': cpu_count_total,
                                 'hostname': hostname.replace(".", "_"), 'running_vms': len(domainIDs)})

        for domain_id in domainIDs:
            domain = conn.lookupByID(domain_id)
            #uuid = domain.UUIDString()
            state, maxmem, mem, cpus, cput = domain.info()
            vm_hostname = domain.name()
            VMS_SPECS.append({'vm_name': vm_hostname.replace(".", "_"), 'vm_state': state, 'vm_running': domain.isActive(),
                              'vm_is_persistent': domain.isPersistent(), 'vm_ram_total_kb': maxmem, 'vm_ram_kb': mem,
                              'vm_cpu_count_total': cpus})

        conn.close()
        HYPERVISOR_SPECS.update({'vm_specs': VMS_SPECS})

    except Exception:
        print("Can't get corresponding info.\n".format(traceback.format_exc()))
        sys.exit(3)


    for key, value in HYPERVISOR_SPECS.items():
        if 'scheme' in args and args['scheme'] != "" and args['scheme'] is not None:
            if key != "vm_specs":
                print("{0}.{1} {2} {3}".format(args['scheme'], key, value, int(time.time())))
            else:
                for vm in value:
                    for k, v in vm.items():
                        v_name = vm.get('vm_name')
                        print("{0}.vm_specs.{1}.{2} {3} {4}".format(args['scheme'], v_name, k, v, int(time.time())))
        else:
            if key != "vm_specs":
                print("{0} {1} {2}".format(key, value, int(time.time())))
            else:
                for vm in value:
                    v_name = vm.get('vm_name')
                    for k, v in vm.items():
                        print("vm_specs.{0}.{1} {2} {3}".format(v_name, k, v, int(time.time())))


if __name__ == "__main__":
    get_vm_info()
