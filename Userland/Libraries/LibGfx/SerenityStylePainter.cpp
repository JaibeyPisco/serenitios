/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibGfx/DesignMetrics.h>
#include <LibGfx/Painter.h>
#include <LibGfx/Palette.h>
#include <LibGfx/SerenityStylePainter.h>

namespace Gfx {

static void rounded_surface(Painter& painter, IntRect const& rect, Color fill, Color border, int radius, int thickness = 1)
{
    if (rect.is_empty())
        return;
    radius = min(radius, min(rect.width(), rect.height()) / 2);
    painter.fill_rect_with_rounded_corners(rect, border, radius);
    auto inner = rect.shrunken(thickness * 2, thickness * 2);
    if (!inner.is_empty())
        painter.fill_rect_with_rounded_corners(inner, fill, max(0, radius - thickness));
}

void SerenityStylePainter::paint_button(Painter& painter, IntRect const& rect, Palette const& palette, ButtonStyle style, bool pressed, bool hovered, bool checked, bool enabled, bool focused, bool default_button)
{
    if (palette.window_theme_provider(WindowThemeRole::WindowTheme) == WindowThemeProvider::Modern) {
        auto fill = !enabled ? palette.window() : pressed || checked ? palette.inactive_selection()
            : hovered                                                ? palette.hover_highlight()
                                                                     : palette.button();
        auto border = !enabled ? palette.disabled_text_front() : focused ? palette.focus_outline()
            : default_button || checked                                  ? palette.accent()
                                                                         : palette.threed_shadow1();
        if (style == ButtonStyle::Coolbar && !pressed && !hovered && !checked && !focused)
            return;
        rounded_surface(painter, rect, fill, border, DesignMetrics::control_radius, enabled && focused ? 2 : 1);
        return;
    }
    auto fill = palette.button();
    auto border = palette.threed_shadow1();
    if (!enabled) {
        fill = palette.window();
    } else {
        if (hovered || checked)
            fill = palette.hover_highlight();
        if (pressed)
            fill = palette.inactive_selection();
        if (focused || default_button || checked)
            border = palette.accent();
    }
    if (style == ButtonStyle::Coolbar && !pressed && !hovered && !checked && !focused)
        return;
    rounded_surface(painter, rect, fill, border, DesignMetrics::control_radius, enabled && (focused || default_button) ? 2 : 1);
}

void SerenityStylePainter::paint_tab_button(Painter& painter, IntRect const& rect, Palette const& palette, bool active, bool hovered, bool enabled, TabPosition position, bool in_active_window, bool accented)
{
    if (palette.window_theme_provider(WindowThemeRole::WindowTheme) == WindowThemeProvider::Modern) {
        auto fill = active ? palette.base() : enabled && hovered ? palette.hover_highlight()
                                                                 : palette.window();
        auto border = !enabled ? palette.disabled_text_front() : active && accented && in_active_window ? palette.accent()
                                                                                                        : palette.threed_shadow1();
        rounded_surface(painter, rect, fill, border, DesignMetrics::control_radius);
        return;
    }
    auto fill = active ? palette.base() : palette.window();
    if (hovered && enabled && !active)
        fill = palette.hover_highlight();
    rounded_surface(painter, rect, fill, active ? palette.threed_shadow1() : palette.window(), DesignMetrics::control_radius);
    if (!active || !accented || !enabled)
        return;
    auto accent = in_active_window ? palette.accent() : palette.disabled_text_front();
    auto line = rect.shrunken(8, 8);
    if (line.is_empty())
        return;
    switch (position) {
    case TabPosition::Top:
        painter.draw_line(line.bottom_left(), line.bottom_right(), accent, 2);
        break;
    case TabPosition::Bottom:
        painter.draw_line(line.top_left(), line.top_right(), accent, 2);
        break;
    case TabPosition::Left:
        painter.draw_line(line.top_right(), line.bottom_right(), accent, 2);
        break;
    case TabPosition::Right:
        painter.draw_line(line.top_left(), line.bottom_left(), accent, 2);
        break;
    }
}

void SerenityStylePainter::paint_frame(Painter& painter, IntRect const& rect, Palette const& palette, FrameStyle style, bool skip_vertical_lines)
{
    if (style == FrameStyle::NoFrame || rect.is_empty())
        return;
    if (style == FrameStyle::Window) {
        paint_window_frame(painter, rect, palette);
        return;
    }
    auto color = palette.threed_shadow1();
    painter.draw_line(rect.top_left(), rect.top_right(), color);
    painter.draw_line(rect.bottom_left(), rect.bottom_right(), color);
    if (skip_vertical_lines && (style == FrameStyle::RaisedPanel || style == FrameStyle::SunkenPanel))
        return;
    painter.draw_line(rect.top_left(), rect.bottom_left(), color);
    painter.draw_line(rect.top_right(), rect.bottom_right(), color);
}

void SerenityStylePainter::paint_check_box(Painter& painter, IntRect const& rect, Palette const& palette, bool enabled, bool checked, bool pressed)
{
    if (palette.window_theme_provider(WindowThemeRole::WindowTheme) == WindowThemeProvider::Modern) {
        auto border = enabled ? checked ? palette.accent() : palette.threed_shadow1() : palette.disabled_text_front();
        auto fill = !enabled ? palette.window() : pressed ? palette.inactive_selection()
            : checked                                     ? palette.accent()
                                                          : palette.base();
        rounded_surface(painter, rect, fill, border, DesignMetrics::indicator_radius);
        if (checked && rect.width() >= 9 && rect.height() >= 9) {
            auto ink = !enabled ? palette.disabled_text_front() : pressed ? palette.base_text()
                                                                          : palette.selection_text();
            auto center = rect.center();
            painter.draw_line(center.translated(-3, 0), center.translated(-1, 2), ink, 2);
            painter.draw_line(center.translated(-1, 2), center.translated(4, -3), ink, 2);
        }
        return;
    }
    auto color = enabled ? palette.accent() : palette.disabled_text_front();
    auto fill = checked ? color : (pressed ? palette.hover_highlight() : palette.base());
    rounded_surface(painter, rect, fill, checked ? color : palette.threed_shadow1(), DesignMetrics::indicator_radius);
    if (!checked || rect.width() < 9 || rect.height() < 9)
        return;
    auto center = rect.center();
    painter.draw_line(center.translated(-3, 0), center.translated(-1, 2), palette.selection_text(), 2);
    painter.draw_line(center.translated(-1, 2), center.translated(4, -3), palette.selection_text(), 2);
}

void SerenityStylePainter::paint_radio_button(Painter& painter, IntRect const& rect, Palette const& palette, bool checked, bool pressed)
{
    if (rect.is_empty())
        return;
    painter.fill_ellipse(rect, checked ? palette.accent() : palette.threed_shadow1());
    auto inner = rect.shrunken(2, 2);
    if (!inner.is_empty())
        painter.fill_ellipse(inner, pressed ? palette.hover_highlight() : palette.base());
    if (checked) {
        auto dot = rect.shrunken(8, 8);
        if (!dot.is_empty())
            painter.fill_ellipse(dot, palette.accent());
    }
}

}
