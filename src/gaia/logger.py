import logging
import sys
from pathlib import Path

class GaiaLogger:
    def __init__(self, log_file='gaia.log'):
        self.log_file = Path(log_file)
        self.loggers = {}

        # Base configuration
        self.default_level = logging.INFO
        logging.basicConfig(level=self.default_level,
                            format='[%(asctime)s] | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s',
                            handlers=[
                                logging.StreamHandler(sys.stdout),
                                logging.FileHandler(self.log_file)
                            ])

        # Default levels for different modules
        self.default_levels = {
            'gaia.agents': logging.DEBUG,
            'gaia.interface': logging.DEBUG,
            'gaia.llm': logging.DEBUG,
        }

        # Suppress specific aiohttp.access log messages
        aiohttp_access_logger = logging.getLogger('aiohttp.access')
        aiohttp_access_logger.addFilter(self.filter_aiohttp_access)

    def filter_aiohttp_access(self, record):
        return not (record.name == 'aiohttp.access' and 'POST /stream_to_ui' in record.getMessage())

    def get_logger(self, name):
        if name not in self.loggers:
            logger = logging.getLogger(name)
            level = self._get_level_for_module(name)
            logger.setLevel(level)
            self.loggers[name] = logger
        return self.loggers[name]

    def _get_level_for_module(self, name):
        for module, level in self.default_levels.items():
            if module in name:
                return level
        return self.default_level

    def set_level(self, name, level):
        if name in self.loggers:
            self.loggers[name].setLevel(level)
        else:
            self.default_levels[name] = level

# Create a global instance
log_manager = GaiaLogger()

# Convenience function to get a logger
def get_logger(name):
    return log_manager.get_logger(name)
