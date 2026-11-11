# Here you define the commands that will be added to your add-in.

# If you want to add an additional command, duplicate one of the existing directories and import it here.
# You need to use aliases (import "entry" as "my_module") assuming you have the default module named "entry".
from ..lib import fusionAddInUtils as futil
from .BoltPattern import entry as BoltPattern
from .CCDistance import entry as CCDistance
from .FaceFillet import entry as FaceFillet
from .Lighten import entry as Lighten
from .QuickCircle import entry as QuickCircle
from .PartsGen import entry as PartsGen
from .AutoHole import entry as AutoHole
from .JointInspector import entry as JointInspector
from .CamSetup import entry as CamSetup
from .TabSlot import entry as TabSlot

# Fusion will automatically call the start() and stop() functions.
commands = [
    BoltPattern,
    CCDistance,
    FaceFillet,
    Lighten,
    QuickCircle,
    PartsGen,
    AutoHole,
    JointInspector,
    CamSetup,
    TabSlot
]


# Assumes you defined a "start" function in each of your modules.
# The start function will be run when the add-in is started.
def start():
    for command in commands:
        command.start()


# Assumes you defined a "stop" function in each of your modules.
# The stop function will be run when the add-in is stopped.
def stop():
    # One command failing to clean up must not stop the others (or the toolbar
    # clean-up that follows in FRCTools.stop) -- anything left behind makes the
    # next run() fail on a duplicate id until Fusion is restarted.
    for command in commands:
        try:
            command.stop()
        except Exception:
            futil.handle_error(f'stop {command.__name__}')