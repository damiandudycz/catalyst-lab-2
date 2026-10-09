# Project templates

New projects can be created from a template. A template describes the stages of a project (and optionally files like
Portage configuration), and can have options, like architecture or init system, that change what is created.

Templates come from two places:

- **Included in Catalyst Lab** — directories in [`data/project_templates`](../data/project_templates), for example
  [Gentoo release](../data/project_templates/gentoo-release/template.toml).
- **Git repositories** — listed in [`data/project_templates/repositories.txt`](../data/project_templates/repositories.txt).
  When a repository template is selected, only its `template.toml` is downloaded to show its options. When the project
  is created, the repository is cloned under the project name and its content is replaced with the generated project,
  so the project keeps the history of the template.

Both kinds have the same format: a directory (or repository root) with `template.toml`, and optionally a `files`
directory.

Templates can't run code. Values are generated with small expressions (see [Expressions](#expressions)) that only
allow a fixed set of operations, so templates from other repositories are safe to use.

## Repository layout

```
template.toml        Required, definition of template.
files/               Optional, files copied to projects (see [[files]]).
README.md            Optional, also README, LICENSE, COPYING (with any extension), .gitignore, .gitattributes.
```

Repositories are checked when they are downloaded, and are rejected if their latest commit contains anything else:

- other files or directories in the root,
- symbolic links, submodules or executable files,
- more than 2000 files, files larger than 10 MB, or more than 50 MB of files in total.

## template.toml

```toml
format = 1                      # Optional, version of template format.
name = "Gentoo release"         # Shown in list of templates and used as default project name.
description = "Stages built like official Gentoo releases."
```

### Options: `[[variables]]`

Options selected when creating a project, shown in this order. All of them are optional, a template without
variables creates always the same project.

```toml
[[variables]]
id = "init"                     # Name used in expressions.
title = "Init system"           # Shown in the app.
description = "..."             # Optional, shown under the title.
type = "choice"                 # choice (default), boolean or architecture.
default = "openrc"              # Optional, the first available option is used otherwise.
when = "architecture != 'm68k'" # Optional, the option is shown only when this is true. Otherwise its value is none.
options = [
    { value = "openrc", title = "OpenRC" },
    { value = "systemd", title = "systemd", when = "architecture in ['amd64', 'arm64']" },
    "other",                    # Short form, value is also the title.
]
```

Types:

| Type | Values |
|---|---|
| `choice` | Texts from `options`. Options can have their own `when`, to be available only for some selections. |
| `boolean` | `true` or `false`, shown as a switch. Has no `options`. |
| `multiple` | Any number of `options`, shown as a list of switches. Its value is a list of selected values, `default` is a list too. |
| `architecture` | Architecture names (`amd64`, `arm64`, `arm`, `ppc`, `ppc64`, `ppc64le`, `riscv`, `s390x`, `sparc`, `x86`...). The selected one is also the architecture of the project, so the app doesn't ask for it separately. Only one per template. |

A `when` condition can use variables defined above it. When a selected value is no longer available (because of
another selection), the default or first available option is used.

### Groups: `[[groups]]`

Configuration that can be enabled when creating a project and applied to selected stages, for example sets of
packages. Every group is shown with a switch, and lists stages it can be applied to.

```toml
groups_title = "Additional software"    # Top level, title of groups in the app.

[[groups]]
id = "kernel"
title = "Kernel and firmware"
description = "Distribution kernel with initramfs."   # Optional.
default = true                          # Optional, enabled by default.
when = "architecture in ['amd64', 'arm64']"  # Optional, the group is offered only when this is true.
stages = ["stage4", "stage4-desktop"]   # Ids of stages the group can be applied to.
default_stages = ["stage4"]             # Optional, stages selected by default (all of stages otherwise).

[groups.arguments]                      # Added to arguments of selected stages.
stage4_packages = ["sys-kernel/gentoo-kernel-bin", "sys-kernel/linux-firmware"]

[[groups.files]]                        # Copied to directories of selected stages.
source = "files/kernel"
destination = "portage"                 # Relative to the stage directory.
```

- Lists in arguments are added to lists of the stage and of other groups (without duplicates), other values replace
  them.
- Arguments that the target of a stage doesn't have are skipped, so a group can set for example both
  `stage4_packages` and `livecd_packages`.
- Enabled groups are available in expressions as list `groups`, for example `'kernel' in groups`. Groups can use
  variables in `when` and expressions, `[values]` can use `groups`.

### Computed values: `[values]`

Values computed from variables, available in expressions below (and in later values), in this order. Useful when the
same value is used in many places.

```toml
[values]
spec_directory = "{{ f'{variant}/' if variant else '' }}"
stage4_services = "{{ ['sshd|default'] if init == 'openrc' else [] }}"
```

### Stages: `[[stages]]`

Stages of the project. A parent has to be defined before its children.

```toml
[[stages]]
id = "stage3"                   # Used in parent of other stages.
parent = "stage1"               # Optional, id of the stage whose build is used as seed.
name = "stage3-{{ init }}"      # Name of stage in project.
target = "stage3"               # Catalyst target: stage1, stage3, stage4, livecd_stage1, livecd_stage2...
releng_template = "{{ spec_directory }}stage3-{{ init }}-23.spec"  # Optional, see below.
when = "include_stage3"         # Optional, the stage is created only when this is true.

[stages.arguments]              # Optional values of stage arguments.
stage4_packages = ["app-admin/sudo", "net-misc/chrony"]
stage4_rcadd = "{{ stage4_services }}"
```

- `releng_template` is a path of a spec in the Releng directory selected for the project, relative to
  `releases/specs/<architecture>` (for example `releases/specs/amd64` or `releases/specs/ppc/ppc64`). The stage
  inherits values from this spec. A missing spec is reported when the project is created, but the stage is still
  created.
- `arguments` are Catalyst spec options, with `/` replaced by `_` (`stage4/packages` → `stage4_packages`). Values can
  be texts, numbers, booleans or lists of texts. Empty values (`""`, `[]` or none) are skipped. Other kinds of values
  can be given in the format of `stage.json` files of projects, for example
  `profile = { type = "StageAutomaticOption", value = "INHERIT_FROM_PARENT" }`. To find names and formats, configure
  a stage in the app and look at its `stages/<name>/stage.json`.
- Arguments that are not set get the same defaults as stages added in the app (inherited from the parent or the Releng
  spec when possible). Values set by the template are shown as **Custom value** in stage settings.
- In lists, items that are none are skipped, so expressions can leave them out.
- Values that are paths to files of the project (for example fsscript or kernel configuration added with
  `[[files]]`) should use placeholders, replaced when building: `@STAGE_DIR@` (directory of the stage),
  `@PROJECT_DIR@` (directory of the project), and like in Releng specs `@REPO_DIR@` (Releng directory). For example
  `stage4_fsscript = "@STAGE_DIR@/fsscript.sh"`. Projects are moved and cloned, so they shouldn't contain
  absolute paths.

### Files: `[[files]]`

Files copied to the project, for example Portage configuration or root overlays of stages.

```toml
[[files]]
source = "files/stage4"                 # File or directory in template.
destination = "stages/stage4-{{ init }}" # Path in project.
when = "init == 'openrc'"               # Optional.
```

- Paths can't lead outside of the template or the project.
- Files ending with `.template` are copied without this suffix, with `{{ expressions }}` in their contents replaced.
  Other files are copied without changes.
- Stage directories are `stages/<stage name>`. Their `portage` directory is Portage configuration of the stage, and
  `root_overlay` its root overlay (files added to the root of stage4 or LiveCD). When a template adds files there,
  the stage uses them: **Stage overlay** is added to its Portage confdir or root overlay sources (after Releng and
  parent sources). Groups are an easy way to add them only to selected stages.

## Expressions

Texts in `template.toml` can contain expressions in `{{ }}`. A text that is a single expression gets its value (which
can be a list or boolean), otherwise values are inserted as text. `when` conditions are expressions without `{{ }}`.

Expressions can use:

- names of variables, computed values, `groups` and `project_name`,
- texts, numbers, `True`, `False`, `None` and lists,
- `and`, `or`, `not`, comparisons (`==`, `!=`, `<`, `<=`, `>`, `>=`), `in`, `not in`,
- `+` (texts, lists, numbers) and `-` (numbers),
- conditional values: `'a' if condition else 'b'`,
- f-strings: `f'{variant}/'`,
- functions `lower(text)`, `upper(text)`, `replace(text, old, new)`, `join(separator, list)`.

Everything else (calling other functions, attributes, indexing, loops, comprehensions, lambdas, imports...) is
rejected. Expressions are limited in length, complexity and size of their results.

## Testing a template

Run Catalyst Lab from the checkout ([Development](../README.md#development)) and either:

- put the template directory in `data/project_templates`, or
- add the repository to `data/project_templates/repositories.txt`. Local repositories can be added with a `file://`
  URL, for example `file:///home/me/my-template`. Only committed files are used.

Then create a new project, select **From template** and your template. Errors in `template.toml` are shown in the list
of templates or on the options page. Check that every combination of options creates the stages you expect: the
options page lists them as you change options.

## Adding a template to Catalyst Lab

Templates in Git repositories are added to the list in the app with a pull request:

1. Create a public Git repository with your template, following the [repository layout](#repository-layout).
2. Fork [catalyst-lab-2](https://github.com/damiandudycz/catalyst-lab-2) and add a line to
   [`data/project_templates/repositories.txt`](../data/project_templates/repositories.txt):
   ```
   https://github.com/you/your-template.git Title of your template
   ```
   The URL is followed by an optional title, shown before the template is downloaded.
3. Open a pull request that changes only this file. Describe what the template creates and which architectures and
   Releng specs it was tested with.

Templates are downloaded from the default branch of the repository, so later changes are available without changing
the list.
