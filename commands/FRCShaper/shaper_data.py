# ---------------------------------------------------------------------------
# FRC Shaper part tables. All sizes in inches (shaper_gen converts to cm).
#
# Library parts are found by lineage URN (no ?version=, so always the latest). Bolts, washers
# and nuts are modelled with the component origin on the seat and +Z pointing away from the
# face they sit on (measured live -- see LESSONS_LEARNED.md). Motors are found by geometry
# instead (`shaper_gen._motor_mount`), so any vendor model works as is.
# ---------------------------------------------------------------------------

PARTS_PATH  = 'Argos CAD > 1 Parts > Standard Parts > Parts_Gen'
MOTORS_PATH = 'Argos CAD > 1 Parts > Standard Parts > Electronics > Motors'

HEAD_BUTTON = 'Button Head'
HEAD_SOCKET = 'Socket Head'
HEADS = (HEAD_BUTTON, HEAD_SOCKET)

NUT_LOCKNUT = 'Nylock Nut'
NUT_NONE    = 'None (Tapped)'
NUTS = (NUT_LOCKNUT, NUT_NONE)

THREAD_AUTO = 'Auto (From Hole Size)'

# Stock lengths a team keeps on hand -- the bolt is the shortest one that's long enough.
_STOCK = (0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)

# A thread is picked automatically from the hole's diameter: the first whose `max_hole` it
# doesn't exceed.
THREADS = {
    '10-32': dict(
        major=0.190, pitch=1 / 32, max_hole=0.215, lengths=_STOCK,
        # Generated stand-ins when the library has no file of that length (dims from the
        # library files, measured live).
        heads={HEAD_BUTTON: dict(dia=0.361, height=0.101),
               HEAD_SOCKET: dict(dia=0.312, height=0.190)},
        # Library files by (head, length).
        bolts={(HEAD_BUTTON, 0.625): dict(urn='urn:adsk.wipprod:dm.lineage:otYK9gfvS66I0T6nr3rX0w',
                                          file='Bolt_10-32_5/8_Button', folder='Bolts'),
               (HEAD_SOCKET, 0.625): dict(urn='urn:adsk.wipprod:dm.lineage:Fq3ohf5mTeaOyTTEtD3ezw',
                                          file='Bolt_10-32_5/8_Cap', folder='Bolts')},
        washer=dict(urn='urn:adsk.wipprod:dm.lineage:IXITY8uKStGJSSwDSlLsNQ',
                    file='10-32_washer', folder='Washers', thickness=0.060),
        nut=dict(urn='urn:adsk.wipprod:dm.lineage:ZWF-hmswQMa_67Uz92GEFQ',
                 file='10-32_Locknut', folder='Nuts', height=0.234),
        # Round spacer for an air gap in the stack.
        spacer=dict(od=0.375, id=0.196),
    ),
    '1/4-20': dict(
        major=0.250, pitch=1 / 20, max_hole=0.300, lengths=_STOCK,
        heads={HEAD_BUTTON: dict(dia=0.437, height=0.132),
               HEAD_SOCKET: dict(dia=0.375, height=0.250)},
        bolts={(HEAD_BUTTON, 0.25): dict(urn='urn:adsk.wipprod:dm.lineage:bib30h7LQIq-9xvsvidVMg',
                                         file='Bolt_1/4-20_1/4_Button', folder='Bolts')},
        washer=dict(urn='urn:adsk.wipprod:dm.lineage:rGGAeZYcT-mEOsAdfXK8CQ',
                    file='1/4-20_washer', folder='Washers', thickness=0.060),
        nut=dict(urn='urn:adsk.wipprod:dm.lineage:KOBQlzjKQiOWoU8gTEtcEA',
                 file='1/4-20_Locknut', folder='Nuts', height=0.313),
        spacer=dict(od=0.500, id=0.257),
    ),
}

# How far past the nut a bolt must stick out (threads showing), in pitches.
MIN_THREADS_PAST_NUT = 1
# A tapped hole (no nut): how much thread to put into the last plate.
TAPPED_ENGAGEMENT = 0.25


def thread_for_hole(hole_dia_in: float):
    """Name of the thread a clearance hole of this diameter takes, or None."""
    for name, thread in THREADS.items():
        if hole_dia_in <= thread['max_hole']:
            return name
    return None


# Motors. `pilot`/`pattern` are the mount's centre-hole and bolt-circle diameters, `count`
# the number of evenly spaced positions on the circle (a plate may drill any subset).
# Patterns from BoltPattern/entry.py and the vendor drawings (LESSONS_LEARNED.md); a motor
# with no `urn` is recognised and named, but there is no model to place yet.
MOTORS = {
    'Kraken X60': dict(pilot=0.75, pattern=2.0, count=12,
                       urn='urn:adsk.wipprod:dm.lineage:n9g2ZPifS22KHwAmw2AZ2A', file='Kraken'),
    'Falcon 500': dict(pilot=0.75, pattern=2.0, count=6,
                       urn='urn:adsk.wipprod:dm.lineage:ere4LOKNQRm8WHjdQw6ilQ', file='Falcon 500'),
    'Kraken X44': dict(pilot=0.75, pattern=1.375, count=12,
                       urn='urn:adsk.wipprod:dm.lineage:1RaN6N5VSTaPHbA-ETXDzQ',
                       file='Kraken_44x_WCP-0941'),
    'NEO Vortex': dict(pilot=0.75, pattern=2.0, count=8, urn=None, file='NEO Vortex'),
    'NEO 550':    dict(pilot=0.5118, pattern=0.9843, count=4, urn=None, file='NEO 550'),
}

# Matching tolerances (inches).
MOTOR_DIA_TOL  = 0.03
MOTOR_HOLE_TOL = 0.02
# Plate holes on a motor's bolt circle are #10 clearance-ish; ignore bigger/smaller circles.
MOTOR_HOLE_DIA_RANGE = (0.10, 0.30)
