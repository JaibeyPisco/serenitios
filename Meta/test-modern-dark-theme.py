#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Check modern themes' explicit loader roles, unchanged geometry and contrast.

This is a resource check, not a substitute for the guest loader/live-palette
fixture and actual Appearance, input, error-dialog and performance verification.
"""

import argparse
import configparser
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def read(path):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read(path)
    return config


def roles(header, name):
    block = header.split(f"#define ENUMERATE_{name}_ROLES(C)", 1)[1].split("\n\n", 1)[0]
    return re.findall(r"C\((\w+)\)", block)


def luminance(color):
    components = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in components]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=("Modern Dark", "Modern Light"), default="Modern Dark")
    args = parser.parse_args()
    header = (REPO / "Userland/Libraries/LibGfx/SystemTheme.h").read_text()
    candidate = read(REPO / "Base/res/themes" / f"{args.theme}.ini")
    default = read(REPO / "Base/res/themes/Default.ini")
    sections = {"COLOR": "Colors", "ALIGNMENT": "Alignments", "WINDOW_THEME": "Window",
                "FLAG": "Flags", "METRIC": "Metrics", "PATH": "Paths"}
    for category, section in sections.items():
        expected = set(roles(header, category))
        actual = set(candidate[section])
        assert actual == expected, (section, "missing", expected - actual, "unknown", actual - expected)
    for name, value in candidate["Colors"].items():
        assert re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value), (name, value)
        # Preserve only the alpha already used by Default for disabled title
        # effects and rubber-band selection; no translucent surface is added.
        if len(value) == 9:
            assert value.lower() == default["Colors"][name].lower(), name
    metric_defaults = {"TitleButtonInactiveAlpha": "255"}
    for name, value in candidate["Metrics"].items():
        assert value == default["Metrics"].get(name, metric_defaults.get(name)), name
    assert candidate["Alignments"] == default["Alignments"]
    assert candidate["Window"]["WindowTheme"] == "Classic"
    assert candidate["Flags"]["IsDark"] == ("true" if args.theme == "Modern Dark" else "false")
    assert candidate["Flags"]["TitleButtonsIconOnly"] == default["Flags"]["TitleButtonsIconOnly"]
    scheme = read(REPO / "Base" / candidate["Paths"]["ColorScheme"].lstrip("/"))
    assert candidate["Flags"]["BoldTextAsBright"] == scheme["Options"]["ShowBoldTextAsBright"]
    for name, value in candidate["Paths"].items():
        if value:
            assert (REPO / "Base" / value.lstrip("/")).exists(), (name, value)
        if "Shadow" in name:
            assert value == default["Paths"].get(name, ""), name
    for icon in ("close", "close-modified", "minimize", "maximize", "restore"):
        assert (REPO / "Base" / candidate["Paths"]["TitleButtonIcons"].lstrip("/") / f"window-{icon}.png").is_file()

    pairs = [("WindowText", "Window", 4.5), ("BaseText", "Base", 4.5),
             ("ButtonText", "Button", 4.5), ("ButtonText", "HoverHighlight", 4.5),
             ("ButtonText", "InactiveSelection", 4.5), ("SelectionText", "Selection", 4.5),
             ("MenuBaseText", "MenuBase", 4.5), ("MenuSelectionText", "MenuSelection", 4.5),
             ("ActiveWindowTitle", "ActiveWindowBorder1", 4.5),
             ("InactiveWindowTitle", "InactiveWindowBorder1", 4.5),
             ("TooltipText", "Tooltip", 4.5), ("TrayText", "Tray", 4.5),
             ("PlaceholderText", "Base", 4.5), ("DisabledTextFront", "Button", 3),
             ("ThreedShadow1", "Button", 3), ("Accent", "Button", 3),
             ("Accent", "HoverHighlight", 3), ("FocusOutline", "Button", 3),
             ("SelectionText", "Accent", 3)]
    pairs += [(name, "Base", 4.5) for name in candidate["Colors"] if name.startswith("Syntax")]
    results = []
    for foreground, background, target in pairs:
        ratio = contrast(candidate["Colors"][foreground], candidate["Colors"][background])
        assert ratio >= target, (foreground, background, ratio, target)
        results.append(dict(foreground=foreground, background=background, ratio=round(ratio, 2), target=target))
    print(json.dumps(dict(theme=args.theme, resource_checks_passed=True, explicit_colors=len(candidate["Colors"]),
                          unchanged_metrics=True, contrast_pairs=results,
                          guest_and_visual_tests_still_required=True), indent=2))


if __name__ == "__main__":
    main()
