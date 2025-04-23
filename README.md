# catalystlab

## Main layout:

# CatalystlabApplication
# ╰── CatalystlabWindow
#     ╰── AdwNavigationView (navigation_view)
#         ╰── AdwNavigationPage
#             ╰── AdwOverlaySplitView (split_view)
#                 ├── AdwNavigationPage
#                 |   ╰── AdwToolbarView
#                 |       ╰── CatalystlabWindowSideMenu (side_menu)
#                 ╰── CatalystlabWindowContent (content_view)

When presenting main AppSection inside CatalystlabWindowContent, it is embedded
into structure with NavigationView->ToolbarView. This way it can display header
and content and also provide own navigation.

To push new view, developer can use either main module NavigationView (created
automatically) or send an AppEvent.PUSH_SECTION to push on main application
navigation view.

When module is embedded into this structure, toggle side menu is also added to
it. This button visibility is controlled by main_window, and it's action is
passed back to main_window.

When AppEvent.PUSH_SECTION is received, main_window inserts it embedded into
structure inside content_view and pushes main navigation view back to root.

