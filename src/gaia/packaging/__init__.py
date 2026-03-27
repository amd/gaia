from .channel import ChannelConfig

# Provides a convenient shortcut to load a channel configuration.

def load_channel_config(channel_name: str) -> ChannelConfig:
    return ChannelConfig(channel_name)
