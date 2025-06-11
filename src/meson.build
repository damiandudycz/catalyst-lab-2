pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'catalystlab'
gnome = import('gnome')

gnome.compile_resources('catalystlab',
  'catalystlab.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'catalystlab.in',
  output: 'catalystlab',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x'
)

catalystlab_sources = [
  '__init__.py',
  'main.py',
  'default_dir_content_builder/default_dir_content_builder.py',
  'extensions/navigation_view_extensions.py',
  'git_directory/git_directory.py',
  'git_directory/git_installation.py',
  'git_directory/git_manager.py',
  'git_directory/git_update.py',
  'global_objects/app_events.py',
  'global_objects/app_section.py',
  'global_objects/architecture.py',
  'global_objects/event_bus.py',
  'global_objects/helper_functions.py',
  'global_objects/modules_scanner.py',
  'global_objects/multistage_process.py',
  'global_objects/repositories.py',
  'global_objects/repository.py',
  'global_objects/runtime_env.py',
  'global_objects/settings.py',
  'overlay/overlay_directory.py',
  'overlay/overlay_installation.py',
  'overlay/overlay_manager.py',
  'overlay/overlay_update.py',
  'project/project_directory.py',
  'project/project_installation.py',
  'project/project_manager.py',
  'project/project_update.py',
  'releng/releng_directory.py',
  'releng/releng_installation.py',
  'releng/releng_manager.py',
  'releng/releng_update.py',
  'root_helper/root_function.py',
  'root_helper/root_helper_client.py',
  'root_helper/root_helper_server.py',
  'snapshot/snapshot_installation.py',
  'snapshot/snapshot_manager.py',
  'snapshot/snapshot.py',
  'toolset/hotfix_patching.py',
  'toolset/toolset_application.py',
  'toolset/toolset_env_builder.py',
  'toolset/toolset_installation.py',
  'toolset/toolset_manager.py',
  'toolset/toolset.py',
  'toolset/toolset_update.py',
  'ui/app_sections/about/about_section.py',
  'ui/app_sections/bugs/bugs_section.py',
  'ui/app_sections/builds/builds_section.py',
  'ui/app_sections/environments/environments_section.py',
  'ui/app_sections/help/help_section.py',
  'ui/app_sections/overlays/overlays_section.py',
  'ui/app_sections/projects/projects_section.py',
  'ui/app_sections/releng/releng_section.py',
  'ui/app_sections/snapshots/snapshots_section.py',
  'ui/app_sections/templates/templates_section.py',
  'ui/app_sections/welcome/welcome_section.py',
  'ui/components/root_access_button.py',
  'ui/components/status_indicator.py',
  'ui/git_directory_create/git_directory_create_config_view.py',
  'ui/git_directory_details/git_directory_details_view.py',
  'ui/main_window/main_window_content.py',
  'ui/main_window/main_window.py',
  'ui/main_window/main_window_side_menu_button.py',
  'ui/main_window/main_window_side_menu.py',
  'ui/multistage_process_execution_view/multistage_process_execution_view.py',
  'ui/overlay_create/overlay_create_view.py',
  'ui/project_create/project_create_view.py',
  'ui/releng_create/releng_create_view.py',
  'ui/releng_select/releng_select_view.py',
  'ui/repository_list/repository_list_view.py',
  'ui/root_command_output/root_command_output_view.py',
  'ui/snapshot_create/snapshot_create_view.py',
  'ui/snapshot_details/snapshot_details_view.py',
  'ui/toolset_create/toolset_create_view.py',
  'ui/toolset_details/toolset_details_view.py',
  'ui/toolset_select/toolset_select_view.py',
  'ui/snapshot_select/snapshot_select_view.py',
  'ui/wizard/wizard_view.py',
]

install_data(catalystlab_sources, install_dir: moduledir)
