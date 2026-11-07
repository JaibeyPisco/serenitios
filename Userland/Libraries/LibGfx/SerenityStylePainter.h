/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibGfx/ClassicStylePainter.h>

namespace Gfx {

// Shared control rendering for the principal Serenity interface. Reuses the
// existing shadow/transparency helpers rather than introducing a second toolkit.
class SerenityStylePainter final : public ClassicStylePainter {
public:
    virtual void paint_button(Painter&, IntRect const&, Palette const&, ButtonStyle, bool pressed, bool hovered, bool checked, bool enabled, bool focused, bool default_button) override;
    virtual void paint_tab_button(Painter&, IntRect const&, Palette const&, bool active, bool hovered, bool enabled, TabPosition, bool in_active_window, bool accented) override;
    virtual void paint_frame(Painter&, IntRect const&, Palette const&, FrameStyle, bool skip_vertical_lines) override;
    virtual void paint_check_box(Painter&, IntRect const&, Palette const&, bool enabled, bool checked, bool pressed) override;
    virtual void paint_radio_button(Painter&, IntRect const&, Palette const&, bool checked, bool pressed) override;
};

}
