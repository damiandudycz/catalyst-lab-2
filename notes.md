# CatalystLab.

CatalystLab is a utility designed to simplify the process of building various
Gentoo Linux stages. It leverages tools such as Catalyst, Releng, QEMU, and 
others, while providing a streamlined and user-friendly interface. By 
abstracting the complexity of these tools, CatalystLab enables users to work 
efficiently without requiring in-depth knowledge of the underlying components.

## Components description.

## TODOs.

### General.
- [x] Create a view for monitoring ServerCall output. Open by clicking on server call on the list from RootButton.
- [x] Instead of manually registering AppSections, create a decorator that will take all section details and register a class.
- [ ] In builds store logs - catalyst log, and links to detected bugs
- [ ] In bugs allow linking to bugzilla issues
- [ ] bwrap: Unknown option --overlay-src in Fedora
- [ ] Create system checks for host required components - bwrap (including capabilities / version), pkexec etc

### Toolsets.
- [x] Block environment calls on single env to one command at a time.
- [x] Create a class for spawning toolset env mountings. This class can keep the spawn mounted and accept next commands to execute. This way we don’t need to spawn new toolset mountings for every call. This class can contain code to spawn, clean, call commands and more. This could also be done in Toolset class itself.
- [ ] Make it possible to save toolset env calls in files and load by name. These files can contain both - command to execute and Binding configurations. Also add escaping to passed commands, so that we can still use things like “, ‘ in these commands with bwrap calls.
- [ ] Mark as not used after server call is terminated / fails
- [ ] Add view with output from all steps combined.
- [ ] SquashFS support - pack new toolset into squashfs, load on demand from squashfs

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
- [ ] Allow receiving calls longer than 4096
- [x] Job.process doesn't seem to be initiated. Need to check
- [ ] CANCEL_JOB sends a term signal, but stall server process doesn't react to it. Other processes might have similar issue, need to check that

