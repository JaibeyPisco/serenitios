/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibGfx/Rect.h>

namespace Gfx::DesktopGeometry {

constexpr int top_panel_height = 32;
constexpr int bottom_panel_height = 27;

inline IntRect work_area(IntRect screen)
{
    screen.set_y(screen.y() + top_panel_height);
    screen.set_height(max(0, screen.height() - top_panel_height - bottom_panel_height));
    return screen;
}

}
