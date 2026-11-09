# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Read stopped x86_64 kernel thread state; loaded by GDB with diagnostic types.

Never calls guest functions or changes registers/memory. The separately compiled
type object must match the kernel source/configuration being inspected.
"""

import gdb


def symbol(address):
    return gdb.execute(f"info symbol {address:#x}", to_string=True).strip()


def dump():
    inferior = gdb.selected_inferior()
    thread_type = gdb.lookup_type("Kernel::Thread")
    list_type = gdb.lookup_global_symbol("diagnostic_list").type.target()
    singleton_address = int(gdb.parse_and_eval("(unsigned long)&'Kernel::s_thread_list'"))
    list_address = int.from_bytes(inferior.read_memory(singleton_address, 8).tobytes(), "little")
    protected_list = gdb.Value(list_address).cast(list_type.pointer()).dereference()
    node = protected_list["m_value"]["m_storage"]["m_first"]
    offset = next(field.bitpos // 8 for field in thread_type.fields() if field.name == "m_global_thread_list_node")
    seen = set()
    while int(node) and len(seen) < 1024:
        address = int(node) - offset
        if address in seen:
            gdb.write("Thread-list cycle detected\n")
            break
        seen.add(address)
        thread = gdb.Value(address).cast(thread_type.pointer()).dereference()
        name_array = thread["m_name"]["m_value"]["m_storage"]
        name = inferior.read_memory(int(name_array.address), 64).tobytes().split(b"\0", 1)[0].decode("utf-8", errors="replace")
        gdb.write(f"\nSAVED THREAD {address:#x} {name!r} tid={int(thread['m_tid']['m_value'])} state={thread['m_state']}\n")
        mutex = thread["m_blocking_mutex"]
        blocker = thread["m_blocker"]
        gdb.write(f"mutex={int(mutex):#x} blocker={int(blocker):#x}\n")
        if int(mutex):
            value = mutex.dereference()
            gdb.write(f"MUTEX name={value['m_name']} mode={value['m_mode']} holder={int(value['m_holder']):#x}\n")
        if int(blocker):
            vtable = int.from_bytes(inferior.read_memory(int(blocker), 8).tobytes(), "little")
            gdb.write(f"BLOCKER {symbol(vtable)}\n")
        regs = thread["m_regs"]
        gdb.write(f"saved rip={int(regs['rip']):#x} rsp={int(regs['rsp']):#x} rbp={int(regs['rbp']):#x}\n")
        bp = int(regs["rbp"])
        lower, upper = int(thread["m_kernel_stack_base"]), int(thread["m_kernel_stack_top"])
        frames = set()
        for _ in range(32):
            if bp in frames or bp < lower or bp + 16 > upper:
                break
            frames.add(bp)
            frame = inferior.read_memory(bp, 16).tobytes()
            previous = int.from_bytes(frame[:8], "little")
            pc = int.from_bytes(frame[8:], "little")
            if not pc:
                break
            gdb.write(f"  {bp:#x} -> {pc:#x}: {symbol(pc)}\n")
            bp = previous
        node = node.dereference()["m_next"]


try:
    dump()
except Exception as error:
    gdb.write(f"Saved-thread diagnostic failed: {error}\n")
