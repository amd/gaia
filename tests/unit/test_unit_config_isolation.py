# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A unit test's own tmp_path never gains GAIA config files (#8477)."""

import threading

from gaia import config as config_mod
from gaia.config import GaiaConfig


def test_the_isolated_config_lives_outside_the_tests_folder(tmp_path):
    assert tmp_path not in config_mod.GAIA_CONFIG_FILE.parents
    assert tmp_path not in config_mod.GAIA_CONFIG_DIR.parents
    assert config_mod.GAIA_CONFIG_DIR != tmp_path


def test_a_config_read_leaves_the_tests_folder_empty(tmp_path):
    GaiaConfig.load()
    GaiaConfig.save_key("default_model", "some-model")
    assert config_mod.GAIA_CONFIG_FILE.is_file()
    assert not list(tmp_path.iterdir())


def test_config_reads_from_another_thread_stay_out_of_the_tests_folder(tmp_path):
    def read():
        for _ in range(20):
            GaiaConfig.load()

    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not list(tmp_path.iterdir())
