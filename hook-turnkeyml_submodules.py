from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules('turnkeyml_submodules')
datas = collect_data_files('turnkeyml_submodules')
