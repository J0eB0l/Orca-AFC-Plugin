"""AFC for OrcaSlicer: an AFC tab with the printer's units, lanes and dryers,
and an "AFC G-code" step that fits sliced files to AFC.

AFC is Armored Turtle's Klipper add-on for filament changers. The plugin reads
it through Moonraker at the address in the printer's connection settings.
"""

import orca

from .page import AFCPage
from .postprocess import AFCGcode

__version__ = "0.2.2"


@orca.plugin
class AFCPlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(AFCPage)
        orca.register_capability(AFCGcode)
