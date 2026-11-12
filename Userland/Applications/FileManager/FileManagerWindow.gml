@GUI::Widget {
    fill_with_background_color: true
    layout: @GUI::VerticalBoxLayout {
        spacing: 2
    }

    @GUI::ToolbarContainer {
        name: "toolbar_container"

        @GUI::Toolbar {
            name: "main_toolbar"
            collapsible: true
            grouped: true
        }

        @GUI::Toolbar {
            name: "breadcrumb_toolbar"

            @GUI::Label {
                text: "Location: "
                autosize: true
            }

            @GUI::PathBreadcrumbbar {
                name: "breadcrumbbar"
            }
        }
    }

    @GUI::Widget {
        name: "modern_navigation"
        visible: false
        fixed_height: 36
        layout: @GUI::HorizontalBoxLayout {
            margins: [4, 8, 4, 8]
            spacing: 8
        }
    }

    @GUI::HorizontalSplitter {
        name: "splitter"

        @GUI::Widget {
            name: "folder_pane"
            preferred_width: 175
            layout: @GUI::VerticalBoxLayout {
                spacing: 0
            }

            @GUI::Label {
                name: "folder_heading"
                text: "Folders"
                text_alignment: "CenterLeft"
                fixed_height: 24
                visible: false
            }

            @GUI::TreeView {
                name: "tree_view"
            }
        }
    }

    @GUI::Statusbar {
        name: "statusbar"

        @GUI::Progressbar {
            name: "progressbar"
            text: "Generating thumbnails: "
            visible: false
        }
    }
}
