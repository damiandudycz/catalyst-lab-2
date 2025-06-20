# CatalystLab.

CatalystLab is a utility designed to simplify the process of building various
Gentoo Linux stages. It leverages tools such as Catalyst, Releng, QEMU, and 
others, while providing a streamlined and user-friendly interface. By 
abstracting the complexity of these tools, CatalystLab enables users to work 
efficiently without requiring in-depth knowledge of the underlying components.

## Components description.

## TODOs:

### General.
- [x] Create a view for monitoring ServerCall output. Open by clicking on server call on the list from RootButton.
- [x] Instead of manually registering AppSections, create a decorator that will take all section details and register a class.
- [ ] In builds store logs - catalyst log, and links to detected bugs
- [ ] In bugs allow linking to bugzilla issues
- [x] bwrap: Unknown option --overlay-src in Fedora
- [ ] Create system checks for host required components - bwrap (including capabilities / version), pkexec etc
- [ ] When restoring things from Repository it should try to also restore IDs, but only if these are free. Otherwise generate new IDs
- [ ] When using host env, use tools from env, and not from flatpak container - bwrap, unsquashfs, etc.

### Toolsets.
- [x] Block environment calls on single env to one command at a time.
- [x] Create a class for spawning toolset env mountings. This class can keep the spawn mounted and accept next commands to execute. This way we don’t need to spawn new toolset mountings for every call. This class can contain code to spawn, clean, call commands and more. This could also be done in Toolset class itself.
- [ ] Make it possible to save toolset env calls in files and load by name. These files can contain both - command to execute and Binding configurations. Also add escaping to passed commands, so that we can still use things like “, ‘ in these commands with bwrap calls.
- [x] Mark as not used after server call is terminated / fails
- [ ] Add view with output from all steps combined.
- [x] SquashFS support - pack new toolset into squashfs, load on demand from squashfs
- [ ] Combine hidden dependencies with first emerge that needs them. Make sure the dependency is always installed first. Dont show dependency in the installer view as step.
- [ ] Consider scanning and displaying all installed apps from world file
- [ ] Pass toolset to steps instead of reaching to process (In installer, updater, etc)
- [x] After installation, created squashfs doesn't contain changes applied, like installed apps. Probably due to some bwrap mappings.
- [x] In toolset update, metadata for packages is generated and stored in Verify step, but compress step might still fail, leaving wrong metadata
- [ ] Make bwrap version used depend on runtime environment - for flatpak use flatpak installed version, for host, use host version.

### RootHelperClient.
- [x] Add structure that collects multiple root calls and keeps root opened while it’s not marked as finished. New calls should be possible to add to these groups live and executed one by one. This can be added to root_function decorator so that it can accept a group to add call to or create and return new one if not provided, but these decorators still need to also return ServerCall itself. These groups should accept also normal functions as user, to create long flow for some larger task.
- [ ] Add display name property to root_function, to display in ServerCall. It should be provided like @root_call(name).
- [x] Add timeout to requests, and use timeout for example in watchdog ping, exit
- [x] Add possibility to call stop_root_helper without sending EXIT. This should be used for example when detecting that server is non responsive
- [x] It's possible that @root_functions are registered when they are first imported. If that's the case we can sometimes generate a server code with incomplete list of actions. To fix this we need to quickly import all modules containing root_functions or create some auto discovery service.

### RootHelperServer.
- [ ] Find better way to pass server token to new server instance, as current one sometimes fails, leaving server initialization unresponsive and blocking entire application.
- [ ] Watchdog needs more complex check, because sometimes client can still work, but pipe gets broken or other issue arises. For example ping based
- [x] IMPORTANT! When output of running command is generated too fast, the pipe gets blocked, making server unresponsive.
- [ ] Add timeout for decoding and send decoded as event
- [ ] Combine handlers into one that also adds Pipe argument
- [x] For pipes decoding/encoding use simpler format <PipeID>:<Message>
- [x] Allow receiving calls longer than 4096
- [x] Job.process doesn't seem to be initiated. Need to check
- [x] CANCEL_JOB sends a term signal, but stall server process doesn't react to it. Other processes might have similar issue, need to check that

### Releng.
- [ ] GIT commands migh require setting user details and accepting github certificate.
- [ ] Add view to set commit message, and show changed files
- [ ] Add possibility to clone using git@, fork branch and to push changes

### Git directories.
- [ ] Add installation stage for checking if created GIT directory is correct for given type - eg. contains struct for overlay, releng etc.
- [ ] Add view for setting commit message and selecting files when commiting git_directory changes.
- [ ] Handle not configured GIT settings (username, email, missing known_hosts)

## App requirements:
 - BWrap >= 0.11
 - pkexec
 - overlayfs (kernel)
 - squashfs-tools
 - git

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
AppSectionDetails.create_section(
    self, 
    content_navigation_view: Adw.NavigationView
)

If no further navigation is required by the module content_navigation_view can
be set to NULL, but it's better to use main_navigation_view in this situation.

## Notes:

### Future TODOS:
- Build bugs module
    - Automatically collect bugs from builds
    - Connect with bugzilla
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

