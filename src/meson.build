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
  'extensions/navigation_view_extensions.py',
  'objects/global_objects/app_events.py',
  'objects/global_objects/app_section.py',
  'objects/global_objects/architecture.py',
  'objects/global_objects/event_bus.py',
  'objects/global_objects/helper_functions.py',
  'objects/global_objects/modules_scanner.py',
  'objects/global_objects/multistage_process.py',
  'objects/global_objects/repositories.py',
  'objects/global_objects/repository.py',
  'objects/global_objects/runtime_env.py',
  'objects/global_objects/settings.py',
  'objects/git_directory/git_directory_default_content_builder.py',
  'objects/git_directory/git_directory.py',
  'objects/git_directory/git_installation.py',
  'objects/git_directory/git_manager.py',
  'objects/git_directory/git_update.py',
  'objects/overlay/overlay_directory.py',
  'objects/overlay/overlay_installation.py',
  'objects/overlay/overlay_manager.py',
  'objects/overlay/overlay_update.py',
  'objects/project/project_directory.py',
  'objects/project/project_installation.py',
  'objects/project/project_stage_installation.py',
  'objects/project/project_manager.py',
  'objects/project/project_update.py',
  'objects/project/project_stage.py',
  'objects/releng/releng_directory.py',
  'objects/releng/releng_installation.py',
  'objects/releng/releng_manager.py',
  'objects/releng/releng_update.py',
  'objects/root_helper/root_function.py',
  'objects/root_helper/root_helper_client.py',
  'objects/root_helper/root_helper_server.py',
  'objects/snapshot/snapshot_installation.py',
  'objects/snapshot/snapshot_manager.py',
  'objects/snapshot/snapshot.py',
  'objects/toolset/hotfix_patching.py',
  'objects/toolset/toolset_application.py',
  'objects/toolset/toolset_env_builder.py',
  'objects/toolset/toolset_installation.py',
  'objects/toolset/toolset_manager.py',
  'objects/toolset/toolset.py',
  'objects/toolset/toolset_update.py',
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
  'ui/components/multistage_process_execution_view/multistage_process_execution_view.py',
  'ui/components/repository_list/repository_list_view.py',
  'ui/components/root_command_output/root_command_output_view.py',
  'ui/components/item_select/item_select_view.py',
  'ui/components/wizard/wizard_view.py',
  'ui/components/stages_tree_view.py',
  'ui/components/cl_toggle_group.py',
  'ui/git_directory/git_directory_create_config_view.py',
  'ui/git_directory/git_directory_details_view.py',
  'ui/main_window/main_window_content.py',
  'ui/main_window/main_window.py',
  'ui/main_window/main_window_side_menu_button.py',
  'ui/main_window/main_window_side_menu.py',
  'ui/overlay/overlay_create_view.py',
  'ui/project/project_create_view.py',
  'ui/project/project_details_view.py',
  'ui/project/project_stage_create_view.py',
  'ui/project/project_stage_details_view.py',
  'ui/releng/releng_create_view.py',
  'ui/snapshot/snapshot_create_view.py',
  'ui/snapshot/snapshot_details_view.py',
  'ui/toolset/toolset_create_view.py',
  'ui/toolset/toolset_details_view.py',
]

install_data(catalystlab_sources, install_dir: moduledir)
