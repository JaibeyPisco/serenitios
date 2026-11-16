/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibDesktop/AppFile.h>

namespace Taskbar {

// An installed .af path is the group key. Empty means keep the window separate.
// Match the kernel-reported full path, never a basename/title or a parent PID.
// Multiple launch profiles and terminal/wrapper launches are deliberately not
// inferred from an executable alone: that would lose their launch semantics.
inline ByteString application_file_for_executable(StringView executable, ReadonlySpan<NonnullRefPtr<Desktop::AppFile>> apps)
{
    if (!executable.starts_with('/'))
        return {};
    RefPtr<Desktop::AppFile> match;
    for (auto const& app : apps) {
        if (!app->is_valid() || app->executable() != executable)
            continue;
        if (match)
            return {};
        match = app;
    }
    if (!match || match->run_in_terminal() || !match->arguments().is_empty())
        return {};
    return match->filename();
}

}
