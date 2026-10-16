"""AFC for OrcaSlicer: an AFC tab with the printer's units, lanes and dryers.

AFC is Armored Turtle's Klipper add-on for filament changers. The tab reads it
through Moonraker at the address in the printer's connection settings.
"""

import orca

from .page import AFCPage

__version__ = "0.1.0"


@orca.plugin
class AFCPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(AFCPage)
