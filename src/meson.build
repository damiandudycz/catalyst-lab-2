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
  'global_objects/app_events.py',
  'environment/environment.py',
  'main_window/main_window.py',
  'main_window/main_window_side_menu.py',
  'main_window/main_window_side_menu_button.py',
  'main_window/main_window_content.py',
  'app_sections/app_section.py',
  'app_sections/app_section_details.py',
  'app_sections/welcome_section.py',
]

install_data(catalystlab_sources, install_dir: moduledir)
