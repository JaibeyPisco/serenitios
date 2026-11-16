/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibGfx/Rect.h>
#include <LibGfx/SystemTheme.h>

namespace Gfx::DesktopGeometry {

constexpr int top_panel_height = 32;
constexpr int bottom_panel_height = 27;
constexpr int dock_height = 56;
constexpr int dock_bottom_margin = 8;

inline bool uses_modern_dock()
{
    auto const& buffer = current_system_theme_buffer();
    return buffer.is_valid() && buffer.data<SystemTheme>()->window_theme[static_cast<int>(WindowThemeRole::WindowTheme)] == WindowThemeProvider::Modern;
}

inline int bottom_reserved_height()
{
    return uses_modern_dock() ? dock_height + dock_bottom_margin : bottom_panel_height;
}

inline IntRect work_area(IntRect screen)
{
    screen.set_y(screen.y() + top_panel_height);
    screen.set_height(max(0, screen.height() - top_panel_height - bottom_reserved_height()));
    return screen;
}

}
