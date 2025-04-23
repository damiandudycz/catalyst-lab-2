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

## Navigation:

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

## App Sections:

Main views of the application are called AppSection. They are definied in
AppSection enum. AppSectionDetails provides additional information about these
modules, such as title, label, icon, class and relations to main window
behavior.

Every main AppSection must implement this init:
def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):

content_navigation_view is created and passed by main_window_content.

These modules can also be created separately from other places of code if needed
using:
AppSectionDetails.create_section(self, content_navigation_view: Adw.NavigationView)

If no further navigation is required by the module content_navigation_view can
be set to NULL, but it's better to use main_navigation_view in this situation.

## Notes:

### TODOS:
- Environments
    - Automatically building .squashfs toolset environments
    - Get the URL using current user architectute
    - Allow updating modules (whole system and used tools)
    - Mark envs as experimental, stable, etc
- Build bugs module
    - Automatically collect bugs from builds
    - Connect with bugzilla
- Dynamic directories binding
    - when starting the build, bind user and system folders to
      toolset_environment, so that it uses these instead of env folders.
- Notes module
    - Allow storing notes for projects
- Templates module
    - Allow using templates
    - Templates could be derived from things like releng default specs or user definied
    - Templates should be able to use other templates in them
        - There should be a stack based mechanism to detect circular dependencies
- Projects
    - Allow exporting .spec and other catalyst files instead of building in app
    - Allow modifying final .spec before build
    - Store both app generated build and user modified and allow showing differences using diff
- UI
    - Embed modules in ScrollViews
    - Consider moving module help to AppSection and insert to view when creating module view in AppSectionDetails
    - Allow a global and local toggles to show/hide module help
    - Allow passing flag to Environments module when opened from Welcome, to add "Continue" button
    - Module help section could also link to help pages and videos
    - Consider alternative layout with top tabs
        - Tabs can still be hidden based on AppSectionDetails.show_in_side_bar
    - Change welcome wizard to pagination with dots at bottom
- Tutorial
    - Create help module containing text and video helps
    - Allow opening help for specific subject from various parts of application
