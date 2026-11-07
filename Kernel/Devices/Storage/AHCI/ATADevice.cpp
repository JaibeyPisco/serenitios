/*
 * Copyright (c) 2021, Liav A. <liavalb@hotmail.co.il>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/StringView.h>
#include <Kernel/Devices/Storage/AHCI/ATADevice.h>
#include <Kernel/Devices/Storage/StorageManagement.h>
#include <Kernel/Sections.h>

namespace Kernel {

static StorageDevice::LUNAddress convert_ata_address_to_lun_address(AHCIController const& controller, ATA::Address ata_address)
{
    return StorageDevice::LUNAddress { controller.controller_id(), ata_address.port, ata_address.subport };
}

ATADevice::ATADevice(AHCIController const& controller, ATA::Address ata_address, u16 capabilities, u16 logical_sector_size, u64 max_addressable_block)
    : StorageDevice(convert_ata_address_to_lun_address(controller, ata_address), controller.hardware_relative_controller_id(), logical_sector_size, max_addressable_block)
    , m_controller(controller)
    , m_ata_address(ata_address)
    , m_capabilities(capabilities)
{
}

ATADevice::~ATADevice() = default;

ErrorOr<void> ATADevice::sync()
{
    auto buffer = UserOrKernelBuffer::for_kernel_buffer(static_cast<u8*>(nullptr));
    auto request = TRY(try_make_request<AsyncBlockDeviceRequest>(AsyncBlockDeviceRequest::Flush, 0, 0, buffer, 0));
    switch (request->wait().request_result()) {
    case AsyncDeviceRequest::Success:
        return {};
    case AsyncDeviceRequest::OutOfMemory:
        return ENOMEM;
    default:
        return EIO;
    }
}

void ATADevice::start_request(AsyncBlockDeviceRequest& request)
{
    VERIFY(m_controller);
    m_controller->start_request(m_ata_address, request);
}

}
