# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

# Loaded by GDB, not by the host Python interpreter. This x86_64 helper uses
# entry registers only, so normal -g1 kernel builds are sufficient.
import json
import os
import signal
from pathlib import Path

import gdb

config = json.loads(Path(os.environ["SERENITY_EXT2_CRASH_CONFIG"]).read_text())
port = None
pending = None
armed = False
finished = False
sync_calls = 0
writes = []
status = None


def save(state, **extra):
    global status, finished
    status = state
    finished = True
    Path(config["result"]).write_text(json.dumps({"status": state, "writes": writes, **extra}, indent=2))


def guarded(method):
    def wrapper(self):
        try:
            return method(self)
        except Exception as error:
            save("error", error=str(error))
            return True
    return wrapper


class SyncReturned(gdb.Breakpoint):
    def __init__(self, frame):
        parent = frame.older()
        super().__init__("*" + hex(parent.pc()), internal=True)
        self.rbp = int(parent.read_register("rbp"))

    @guarded
    def stop(self):
        if int(gdb.parse_and_eval("$rbp")) != self.rbp:
            return False
        self.enabled = False
        assert armed, "fixture probe was not observed"
        assert int(gdb.parse_and_eval("$rax")) == 0 and int(gdb.parse_and_eval("$rdx")) & 0xff == 0
        save("complete" if config["cut"] == 0 else "missed-cut")
        return True


class Sync(gdb.Breakpoint):
    @guarded
    def stop(self):
        global sync_calls
        sync_calls += 1
        if sync_calls == 1:
            access.enabled = True
            completion.enabled = True
        elif sync_calls == 2:
            SyncReturned(gdb.newest_frame())
            self.enabled = False
        else:
            raise RuntimeError("unexpected sync marker")
        return False


class Access(gdb.Breakpoint):
    @guarded
    def stop(self):
        global port, pending
        this = int(gdb.parse_and_eval("$rdi"))
        direction = int(gdb.parse_and_eval("$rsi"))
        lba = int(gdb.parse_and_eval("$rdx"))
        blocks = int(gdb.parse_and_eval("$rcx")) & 0xff
        if port is None and direction == 0 and lba == config["probe_lba"] and blocks == 1:
            port = this
        if this == port:
            pending = {"direction": direction, "lba": lba, "blocks": blocks}
        return False


class Completion(gdb.Breakpoint):
    @guarded
    def stop(self):
        global armed, pending
        if int(gdb.parse_and_eval("$rdi")) != port or pending is None:
            return False
        assert int(gdb.parse_and_eval("$rsi")) == 2, "AHCI request failed"
        command = pending
        pending = None
        if not armed:
            assert command["direction"] == 0 and command["lba"] == config["probe_lba"]
            armed = True
        elif command["direction"] == 1:
            writes.append(command)
            if config["cut"] and len(writes) == config["cut"]:
                save("cut")
                # The emulated CPU is stopped here, after command completion.
                os.kill(config["qemu_pid"], signal.SIGKILL)
                return True
        return False


access = Access("*_ZN6Kernel8AHCIPort13access_deviceENS_23AsyncBlockDeviceRequest11RequestTypeEmh",
                type=gdb.BP_HARDWARE_BREAKPOINT, internal=True)
completion = Completion("*_ZN6Kernel8AHCIPort24complete_current_requestENS_18AsyncDeviceRequest13RequestResultE",
                        type=gdb.BP_HARDWARE_BREAKPOINT, internal=True)
access.enabled = False
completion.enabled = False
sync = Sync("*" + config["sync_symbol"], type=gdb.BP_HARDWARE_BREAKPOINT, internal=True)
try:
    while not finished:
        gdb.execute("continue")
except gdb.error as error:
    if status != "cut":
        save("error", error=str(error))
if status != "cut":
    gdb.execute("detach")
