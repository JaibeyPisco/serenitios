/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

namespace Gfx::DesignMetrics {

// Logical pixels; the painter applies the display scale. Use the palette for
// colors so previews and accessibility tooling share the same semantic roles.
constexpr int spacing = 8;
constexpr int content_margin = 12;
constexpr int control_height = 28;
// Opt-in for verified forms; compact controls retain control_height.
constexpr int standard_control_height = 32;
constexpr int control_radius = 5;
constexpr int indicator_radius = 3;
constexpr int settings_width = 480;
constexpr int settings_height = 520;

}
