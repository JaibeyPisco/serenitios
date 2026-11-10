/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/ByteString.h>
#include <AK/Vector.h>
#include <LibGUI/Forward.h>

namespace FileManager {

void initialize_archive_operations();
bool archive_operations_in_progress();
ErrorOr<void> create_archive(Vector<ByteString> const& sources, ByteString const& destination, GUI::Window*);
ErrorOr<void> extract_archive(ByteString const& source, ByteString const& destination, GUI::Window*);

}
