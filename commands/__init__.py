# Here you define the commands that will be added to your add-in.

# If you want to add an additional command, duplicate one of the existing directories and import it here.
# You need to use aliases (import "entry" as "my_module") assuming you have the default module named "entry".
from .BoltPattern import entry as BoltPattern
from .CCDistance import entry as CCDistance
from .FaceFillet import entry as FaceFillet
from .FilletXpert import entry as FilletXpert
from .Lighten import entry as Lighten
from .QuickCircle import entry as QuickCircle
from .PartsGen import entry as PartsGen
from .Tubify import entry as Tubify
from .AutoHole import entry as AutoHole

# Fusion will automatically call the start() and stop() functions.
commands = [
    BoltPattern,
    CCDistance,
    FaceFillet,
    FilletXpert,
    Lighten,
    QuickCircle,
    PartsGen,
    Tubify,
    AutoHole
]


# Assumes you defined a "start" function in each of your modules.
# The start function will be run when the add-in is started.
def start():
    for command in commands:
        command.start()


# Assumes you defined a "stop" function in each of your modules.
# The stop function will be run when the add-in is stopped.
def stop():
    for command in commands:
        command.stop()