"""Where the plugin and its assets live, wherever it is installed."""
import os

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS = os.path.join(PLUGIN_ROOT, "assets")


def asset(*parts):
    return os.path.join(ASSETS, *parts)
