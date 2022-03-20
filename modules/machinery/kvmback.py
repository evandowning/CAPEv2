# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

from __future__ import absolute_import
import os
import logging
import subprocess
import xml.etree.ElementTree as ET

from lib.cuckoo.common.abstracts import LibVirtMachinery

log = logging.getLogger(__name__)
from lib.cuckoo.common.exceptions import CuckooCriticalError, CuckooMachineError

class KVMBack(LibVirtMachinery):
    """Virtualization layer for KVM based on python-libvirt."""

    # Set KVM connection string.
    dsn = "qemu:///system"

    def start(self, label):
        vm_info = self.db.view_machine_by_label(label)
        vm_options = getattr(self.options, vm_info.name)

        folder = os.path.dirname(vm_options.image)
        backing_file_path = os.path.join(folder,'snapshot_{0}.qcow2'.format(vm_info.name))

        # Create backing file
        try:
            proc = subprocess.Popen(
                ["qemu-img", "create", "-f", "qcow2", "-b", vm_options.image, "-F", "qcow2", backing_file_path],
                universal_newlines=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            output, err = proc.communicate()
            if err:
                raise OSError(err)
        except OSError as e:
            raise CuckooMachineError(f"QEMU failed starting the machine: {e}")

        # Start VM
        super(KVMBack, self).start(label)
        machine = self.db.view_machine_by_label(label)
        if not machine:
            log.info(f"Can't get iface for {label}")
