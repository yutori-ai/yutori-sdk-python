from yutori.navigator.macos.menus import menu_elements


def row(role, label, depth, token):
    return {"role": role, "label": label, "depth": depth, "element_token": token}


def test_menu_paths_preserve_nested_ancestry_without_structural_wrappers():
    menus = menu_elements(
        {
            "elements": [
                row("AXMenuBarItem", "File", 1, "a"),
                row("AXMenuItem", "New", 3, "b"),
                row("AXMenuItem", "Export", 3, "c"),
                row("AXMenuItem", "PDF", 5, "d"),
                row("AXMenuBarItem", "Edit", 1, "e"),
                row("AXMenuItem", "Copy", 3, "f"),
            ]
        }
    )
    assert [menu["path"] for menu in menus] == [
        ["File"],
        ["File", "New"],
        ["File", "Export"],
        ["File", "Export", "PDF"],
        ["Edit"],
        ["Edit", "Copy"],
    ]


def test_non_menubar_contexts_and_unaddressable_or_malformed_rows_are_not_guessed():
    assert (
        menu_elements(
            {
                "elements": [
                    row("AXMenuItem", "Context action", 3, "context"),
                    row("AXMenuBarItem", "File", 1, None),
                    row("AXWindow", "Document", 1, "window"),
                    row("AXMenuItem", "Other context", 3, "other"),
                    row("AXMenuBarItem", "Broken", True, "bad"),
                    None,
                ]
            }
        )
        == ()
    )


def test_the_apple_menu_is_never_projected():
    menus = menu_elements(
        {
            "elements": [
                row("AXMenuBarItem", "Apple", 1, "apple"),
                row("AXMenuItem", "Recent Items", 3, "recent"),
                row("AXMenuItem", "Private document.pdf", 5, "doc"),
                row("AXMenuBarItem", "File", 1, "file"),
                row("AXMenu", None, 2, None),
                row("AXMenuItem", "New", 3, "new"),
            ]
        }
    )
    assert [menu["path"] for menu in menus] == [["File"], ["File", "New"]]
