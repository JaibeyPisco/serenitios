/*
 * Copyright (c) 2020, Andreas Kling <kling@serenityos.org>
 * Copyright (c) 2021-2022, Filiph Sandström <filiph.sandstrom@filfatstudios.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibGfx/Bitmap.h>
#include <LibGfx/ClassicWindowTheme.h>
#include <LibGfx/Font/FontDatabase.h>
#include <LibGfx/Painter.h>
#include <LibGfx/Palette.h>
#include <LibGfx/StylePainter.h>

namespace Gfx {

bool ClassicWindowTheme::uses_modern_controls(Palette const& palette)
{
    return palette.window_theme_provider(WindowThemeRole::WindowTheme) == WindowThemeProvider::Modern;
}

IntRect ClassicWindowTheme::modern_title_text_rect(IntRect titlebar, IntRect controls)
{
    int left = titlebar.left() + 8;
    if (!controls.is_empty())
        left = max(left, controls.right() + 8);
    return { left, titlebar.top(), max(0, titlebar.right() - 8 - left + 1), titlebar.height() };
}

void ClassicWindowTheme::paint_modern_control(Painter& painter, IntRect const& rect, Palette const& palette, Control control, WindowState state, bool hovered, bool pressed, bool modified, bool maximized)
{
    PainterStateSaver saver(painter);
    painter.add_clip_rect(rect);
    int diameter = min(14, min(rect.width() - 4, rect.height() - 4));
    if (diameter < 8)
        return;
    auto circle = IntRect { 0, 0, diameter, diameter }.centered_within(rect);
    Color fill = control == Control::Close ? Color(240, 86, 80) : control == Control::Minimize ? Color(242, 189, 65)
                                                                                               : Color(52, 195, 90);
    if (state == WindowState::Inactive)
        fill = fill.mixed_with(palette.inactive_window_border1(), .45f);
    if (pressed)
        fill = fill.darkened(.75f);
    else if (hovered)
        fill = fill.lightened(1.12f);
    painter.fill_ellipse(circle, fill);
    auto center = circle.center();
    auto ink = Color(40, 35, 30);
    if (modified && control == Control::Close) {
        painter.fill_ellipse({ center.x() - 2, center.y() - 2, 4, 4 }, ink);
    } else if (hovered || pressed) {
        if (control == Control::Close) {
            painter.draw_line(center.translated(-3, -3), center.translated(3, 3), ink);
            painter.draw_line(center.translated(-3, 3), center.translated(3, -3), ink);
        } else if (control == Control::Minimize) {
            painter.draw_line(center.translated(-3, 0), center.translated(3, 0), ink);
        } else {
            painter.draw_rect({ center.x() - 3, center.y() - 3, 7, 7 }, ink);
            if (maximized)
                painter.draw_line(center.translated(-1, -1), center.translated(3, -1), ink);
        }
    }
}

int ClassicWindowTheme::menubar_height() const
{
    return max(20, FontDatabase::default_font().pixel_size_rounded_up() + 6);
}

Gfx::IntRect ClassicWindowTheme::titlebar_icon_rect(WindowType window_type, WindowMode window_mode, IntRect const& window_rect, Palette const& palette) const
{
    if (window_type == WindowType::Normal && uses_modern_controls(palette))
        return {};
    if (window_mode == WindowMode::RenderAbove)
        return {};

    auto titlebar_rect = this->titlebar_rect(window_type, window_mode, window_rect, palette);
    Gfx::IntRect icon_rect {
        titlebar_rect.x() + 2,
        titlebar_rect.y(),
        16,
        16,
    };
    icon_rect.center_vertically_within(titlebar_rect);
    icon_rect.translate_by(0, 1);
    return icon_rect;
}

Gfx::IntRect ClassicWindowTheme::titlebar_text_rect(WindowType window_type, WindowMode window_mode, IntRect const& window_rect, Palette const& palette) const
{
    if (window_type == WindowType::Normal && uses_modern_controls(palette))
        return modern_title_text_rect(titlebar_rect(window_type, window_mode, window_rect, palette), {});
    auto titlebar_rect = this->titlebar_rect(window_type, window_mode, window_rect, palette);
    auto titlebar_icon_rect = this->titlebar_icon_rect(window_type, window_mode, window_rect, palette);
    return {
        titlebar_rect.x() + 3 + (titlebar_icon_rect.is_empty() ? 0 : (titlebar_icon_rect.width() + 2)),
        titlebar_rect.y(),
        titlebar_rect.width() - 5 - (titlebar_icon_rect.is_empty() ? 0 : (titlebar_icon_rect.width() + 2)),
        titlebar_rect.height()
    };
}

void ClassicWindowTheme::paint_normal_frame(Painter& painter, WindowState window_state, WindowMode window_mode, IntRect const& window_rect, StringView window_title, Bitmap const& icon, Palette const& palette, IntRect const& leftmost_button_rect, int menu_row_count, [[maybe_unused]] bool window_modified) const
{
    auto frame_rect = frame_rect_for_window(WindowType::Normal, window_mode, window_rect, palette, menu_row_count);
    frame_rect.set_location({ 0, 0 });
    Gfx::StylePainter::paint_window_frame(painter, frame_rect, palette);

    auto& title_font = FontDatabase::window_title_font();

    auto titlebar_rect = this->titlebar_rect(WindowType::Normal, window_mode, window_rect, palette);
    auto titlebar_icon_rect = this->titlebar_icon_rect(WindowType::Normal, window_mode, window_rect, palette);
    auto titlebar_inner_rect = titlebar_text_rect(WindowType::Normal, window_mode, window_rect, palette);
    auto titlebar_title_rect = titlebar_inner_rect;
    titlebar_title_rect.set_width(title_font.width(window_title));

    auto [title_color, border_color, border_color2, stripes_color, shadow_color] = compute_frame_colors(window_state, palette);

    if (uses_modern_controls(palette)) {
        int radius = max(0, palette.window_border_radius() - palette.window_border_thickness());
        painter.fill_rect_with_rounded_corners(titlebar_rect, border_color, radius, radius, 0, 0);
        auto text_rect = modern_title_text_rect(titlebar_rect, leftmost_button_rect);
        if (!text_rect.is_empty())
            painter.draw_text(text_rect, window_title, title_font, TextAlignment::CenterLeft, title_color, Gfx::TextElision::Right);
        return;
    }

    painter.draw_line(titlebar_rect.bottom_left(), titlebar_rect.bottom_right().moved_left(1), palette.button());
    painter.draw_line(titlebar_rect.bottom_left().moved_down(1), titlebar_rect.bottom_right().translated(-1, 1), palette.button());

    painter.fill_rect_with_gradient(titlebar_rect, border_color, border_color2);

    auto title_alignment = palette.title_alignment();

    int stripe_right = leftmost_button_rect.left() - 3;

    auto clipped_title_rect = titlebar_title_rect;
    clipped_title_rect.set_width(stripe_right - clipped_title_rect.x());
    if (!clipped_title_rect.is_empty()) {
        painter.draw_text(clipped_title_rect.translated(1, 2), window_title, title_font, title_alignment, shadow_color, Gfx::TextElision::Right);
        // FIXME: The translated(0, 1) wouldn't be necessary if we could center text based on its baseline.
        painter.draw_text(clipped_title_rect.translated(0, 1), window_title, title_font, title_alignment, title_color, Gfx::TextElision::Right);
    }

    if (window_mode == WindowMode::RenderAbove)
        return;

    if (stripes_color.alpha() > 0) {
        switch (title_alignment) {
        case Gfx::TextAlignment::CenterLeft: {
            int stripe_left = titlebar_title_rect.right() + 4;

            if (stripe_left && stripe_right && stripe_left < stripe_right) {
                for (int i = 2; i <= titlebar_inner_rect.height() - 2; i += 2) {
                    painter.draw_line({ stripe_left, titlebar_inner_rect.y() + i }, { stripe_right, titlebar_inner_rect.y() + i }, stripes_color);
                }
            }
            break;
        }
        case Gfx::TextAlignment::CenterRight: {
            for (int i = 2; i <= titlebar_inner_rect.height() - 2; i += 2) {
                painter.draw_line({ titlebar_inner_rect.left(), titlebar_inner_rect.y() + i }, { stripe_right - titlebar_title_rect.width() - 3, titlebar_inner_rect.y() + i }, stripes_color);
            }
            break;
        }
        case Gfx::TextAlignment::Center: {
            auto stripe_width = (leftmost_button_rect.left() / 2 - titlebar_title_rect.width() / 2) - titlebar_icon_rect.width() - 3;

            for (int i = 2; i <= titlebar_inner_rect.height() - 2; i += 2) {
                painter.draw_line({ titlebar_inner_rect.left(), titlebar_inner_rect.y() + i }, { titlebar_inner_rect.left() + stripe_width, titlebar_inner_rect.y() + i }, stripes_color);
                painter.draw_line({ stripe_right - stripe_width, titlebar_inner_rect.y() + i }, { stripe_right, titlebar_inner_rect.y() + i }, stripes_color);
            }
            break;
        }
        default:
            dbgln("Unhandled title alignment!");
        }
    }

    painter.draw_scaled_bitmap(titlebar_icon_rect, icon, icon.rect());
}

IntRect ClassicWindowTheme::menubar_rect(WindowType window_type, WindowMode window_mode, IntRect const& window_rect, Palette const& palette, int menu_row_count) const
{
    if (window_type != WindowType::Normal)
        return {};
    return { palette.window_border_thickness(), palette.window_border_thickness() - 1 + titlebar_height(window_type, window_mode, palette) + 2, window_rect.width(), menubar_height() * menu_row_count };
}

IntRect ClassicWindowTheme::titlebar_rect(WindowType window_type, WindowMode window_mode, IntRect const& window_rect, Palette const& palette) const
{
    auto& title_font = FontDatabase::window_title_font();
    auto window_titlebar_height = titlebar_height(window_type, window_mode, palette);
    // FIXME: The top of the titlebar doesn't get redrawn properly if this padding is different
    int total_vertical_padding = title_font.pixel_size_rounded_up() - 1;

    if (window_type == WindowType::Notification)
        return { window_rect.width() + 3, total_vertical_padding / 2 - 1, window_titlebar_height, window_rect.height() };
    return { palette.window_border_thickness(), palette.window_border_thickness(), window_rect.width(), window_titlebar_height };
}

ClassicWindowTheme::FrameColors ClassicWindowTheme::compute_frame_colors(WindowState state, Palette const& palette) const
{
    switch (state) {
    case WindowState::Highlighted:
        return { palette.highlight_window_title(), palette.highlight_window_border1(), palette.highlight_window_border2(), palette.highlight_window_title_stripes(), palette.highlight_window_title_shadow() };
    case WindowState::Moving:
        return { palette.moving_window_title(), palette.moving_window_border1(), palette.moving_window_border2(), palette.moving_window_title_stripes(), palette.moving_window_title_shadow() };
    case WindowState::Active:
        return { palette.active_window_title(), palette.active_window_border1(), palette.active_window_border2(), palette.active_window_title_stripes(), palette.active_window_title_shadow() };
    case WindowState::Inactive:
        return { palette.inactive_window_title(), palette.inactive_window_border1(), palette.inactive_window_border2(), palette.inactive_window_title_stripes(), palette.inactive_window_title_shadow() };
    default:
        VERIFY_NOT_REACHED();
    }
}

void ClassicWindowTheme::paint_notification_frame(Painter& painter, WindowMode window_mode, IntRect const& window_rect, Palette const& palette, IntRect const& close_button_rect) const
{
    auto frame_rect = frame_rect_for_window(WindowType::Notification, window_mode, window_rect, palette, 0);
    frame_rect.set_location({ 0, 0 });
    Gfx::StylePainter::paint_window_frame(painter, frame_rect, palette);

    auto titlebar_rect = this->titlebar_rect(WindowType::Notification, window_mode, window_rect, palette);
    painter.fill_rect_with_gradient(Gfx::Orientation::Vertical, titlebar_rect, palette.active_window_border1(), palette.active_window_border2());

    if (palette.active_window_title_stripes().alpha() > 0) {
        int stripe_top = close_button_rect.bottom() + 3;
        int stripe_bottom = window_rect.height() - 3;
        if (stripe_top && stripe_bottom && stripe_top < stripe_bottom) {
            for (int i = 2; i <= palette.window_title_height() - 2; i += 2)
                painter.draw_line({ titlebar_rect.x() + i, stripe_top }, { titlebar_rect.x() + i, stripe_bottom }, palette.active_window_title_stripes());
        }
    }
}

IntRect ClassicWindowTheme::frame_rect_for_window(WindowType window_type, WindowMode window_mode, IntRect const& window_rect, Gfx::Palette const& palette, int menu_row_count) const
{
    auto window_titlebar_height = titlebar_height(window_type, window_mode, palette);
    auto border_thickness = palette.window_border_thickness();

    switch (window_type) {
    case WindowType::Normal:
        return {
            window_rect.x() - border_thickness,
            window_rect.y() - window_titlebar_height - border_thickness - 1 - menu_row_count * menubar_height(),
            window_rect.width() + (border_thickness * 2),
            window_rect.height() + (border_thickness * 2) + 1 + window_titlebar_height + menu_row_count * menubar_height(),
        };
    case WindowType::Notification:
        return {
            window_rect.x() - 3,
            window_rect.y() - 3,
            window_rect.width() + 6 + window_titlebar_height,
            window_rect.height() + 6
        };
    default:
        return window_rect;
    }
}

Vector<IntRect> ClassicWindowTheme::layout_buttons(WindowType window_type, WindowMode window_mode, IntRect const& window_rect, Palette const& palette, size_t buttons, bool is_maximized) const
{
    (void)is_maximized;
    int window_button_width = palette.window_title_button_width();
    int window_button_height = palette.window_title_button_height();
    int pos;
    Vector<IntRect> button_rects;
    if (window_type == WindowType::Normal && uses_modern_controls(palette)) {
        auto titlebar = titlebar_rect(window_type, window_mode, window_rect, palette);
        int left = titlebar.left() + 8;
        for (size_t i = 0; i < buttons; ++i) {
            IntRect rect { left, 0, window_button_width, window_button_height };
            rect.center_vertically_within(titlebar);
            button_rects.append(rect);
            left += window_button_width;
        }
        return button_rects;
    }
    if (window_type == WindowType::Notification)
        pos = titlebar_rect(window_type, window_mode, window_rect, palette).top() + 2;
    else
        pos = titlebar_text_rect(window_type, window_mode, window_rect, palette).right();

    for (size_t i = 0; i < buttons; i++) {
        if (window_type == WindowType::Notification) {
            // The button height & width have to be equal or it leaks out of its area
            Gfx::IntRect rect { 0, pos, window_button_height, window_button_height };
            rect.center_horizontally_within(titlebar_rect(window_type, window_mode, window_rect, palette));
            button_rects.append(rect);
            pos += window_button_height;
        } else {
            pos -= window_button_width;
            Gfx::IntRect rect { pos, 0, window_button_width, window_button_height };
            rect.center_vertically_within(titlebar_text_rect(window_type, window_mode, window_rect, palette));
            button_rects.append(rect);
        }
    }
    return button_rects;
}

int ClassicWindowTheme::titlebar_height(WindowType window_type, WindowMode window_mode, Palette const& palette) const
{
    auto& title_font = FontDatabase::window_title_font();
    switch (window_type) {
    case WindowType::Normal:
    case WindowType::Notification: {
        if (window_mode == WindowMode::RenderAbove)
            return max(palette.window_title_height() - 4, title_font.pixel_size() + 2);
        else
            return max(palette.window_title_height(), title_font.pixel_size() + 6);
    }
    default:
        return 0;
    }
}

void ClassicWindowTheme::paint_taskbar(Painter& painter, IntRect const& taskbar_rect, Palette const& palette) const
{
    painter.fill_rect(taskbar_rect, palette.button());
    painter.draw_line({ 0, 1 }, { taskbar_rect.width() - 1, 1 }, palette.threed_highlight());
}

void ClassicWindowTheme::paint_button(Painter& painter, IntRect const& rect, Palette const& palette, ButtonStyle button_style, bool pressed, bool hovered, bool checked, bool enabled, bool focused, bool default_button) const
{
    StylePainter::current().paint_button(painter, rect, palette, button_style, pressed, hovered, checked, enabled, focused, default_button);
}

}
