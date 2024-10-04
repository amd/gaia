from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules('turnkeyml')
datas = collect_data_files('turnkeyml')