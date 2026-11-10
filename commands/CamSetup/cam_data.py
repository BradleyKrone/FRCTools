# Machine, tool and feeds-and-speeds data for the FRC CAM Setup command.
#
# Pure data -- no Fusion calls -- so it can be tuned without touching the builder.
# Everything here is in INCHES and IPM (inches per minute); cam_builder converts
# when it writes Fusion parameters.
#
# Source: Notes/Shop/CNC/Shapeoko 5.1 Pro/Shapeoko 5.1 Pro.md. Every row there is
# still marked "Starting point", so treat these as starting values and update
# them here as cuts get proven out.

MATERIALS = ['Wood', 'Aluminum', 'Polycarb', 'SRPP']

# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------
# `library_match` is a list of lowercase substrings looked for in the vendor /
# model / description of machines in the user's Fusion machine library. If one
# is found the setup gets that machine (so posting later picks the right post);
# otherwise the setup is created with no machine.
MACHINES = {
    'Shapeoko 5 Pro 4x4': {
        'travel': (48.7, 48.7, 6.1),        # X, Y, Z (Z is 5.51 with the Sweepy Pro boot)
        'max_feed_xy': 200.0,               # Carbide's recommended max; 275 absolute
        'max_feed_z': 100.0,
        'rpm_range': (8000, 24000),         # Carbide 3D 65mm VFD spindle
        'library_match': ['shapeoko'],
        'feeds': 'shapeoko',
    },
    'ShopBot': {
        # TODO: ShopBot numbers are copies of the Shapeoko for now -- fill in the
        # real bed size / spindle once known.
        'travel': (96.0, 48.0, 6.0),
        'max_feed_xy': 200.0,
        'max_feed_z': 100.0,
        'rpm_range': (8000, 24000),
        'library_match': ['shopbot'],
        'feeds': 'shapeoko',
    },
}

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
# Single flute O-flute end mills from the note. Flute length / overall length /
# shoulder aren't recorded in the note yet. TODO: measure the real bits.
TOOLS = {
    '1/4': {
        'description': '1/4" O-Flute (FRC)',
        'number': 1,
        'diameter': 0.250,
        'flutes': 1,
        'flute_length': 0.75,
        'shoulder_length': 0.85,
        'body_length': 1.0,
        'overall_length': 2.0,
    },
    '1/8': {
        'description': '1/8" O-Flute (FRC)',
        'number': 2,
        'diameter': 0.125,
        'flutes': 1,
        'flute_length': 0.50,
        'shoulder_length': 0.60,
        'body_length': 0.75,
        'overall_length': 1.5,
    },
}

# ---------------------------------------------------------------------------
# Feeds & speeds:  FEEDS[feeds_key][material][tool][strategy]
# ---------------------------------------------------------------------------
#   rpm      spindle speed
#   feed     cutting feed (IPM)
#   plunge   plunge / ramp feed (IPM)
#   doc      'slot': depth per pass;  'adaptive': max roughing stepdown
#   woc      'adaptive' only: optimal load (radial width of cut)
# 'slot' rows are used for full-width cuts: profile contours and helical bores.
# 'adaptive' rows are used for pocket / through-cutout clearing.
FEEDS = {
    'shapeoko': {
        # Aluminum: the team's shop-proven numbers (hand-made Setup4), not the notes.
        'Aluminum': {
            '1/4': {'slot':     {'rpm': 18000, 'feed': 55, 'plunge': 25, 'doc': 0.05},
                    'adaptive': {'rpm': 18000, 'feed': 55, 'plunge': 25, 'doc': 0.125, 'woc': 0.050}},
            '1/8': {'slot':     {'rpm': 18000, 'feed': 40, 'plunge': 20, 'doc': 0.012},
                    'adaptive': {'rpm': 18000, 'feed': 40, 'plunge': 20, 'doc': 0.125, 'woc': 0.025}},
        },
        'Polycarb': {
            '1/4': {'slot':     {'rpm': 16000, 'feed': 60, 'plunge': 20, 'doc': 0.125},
                    'adaptive': {'rpm': 16000, 'feed': 90, 'plunge': 25, 'doc': 0.250, 'woc': 0.040}},
            '1/8': {'slot':     {'rpm': 18000, 'feed': 40, 'plunge': 12, 'doc': 0.0625},
                    'adaptive': {'rpm': 18000, 'feed': 55, 'plunge': 15, 'doc': 0.125, 'woc': 0.020}},
        },
        # SRPP: tuned for speed, not finish. The 1/4" runs at the machine's 200 IPM
        # max; RPM is set so the single flute still takes a fat ~0.011" chip, which
        # carries the heat away so the polypropylene doesn't melt and re-weld.
        # Slot DOC stays 0.125 so a 1/8" sheet profiles in ONE pass (even stepdowns:
        # anything smaller doubles the passes). The 1/8" is capped lower -- it snaps.
        'SRPP': {
            '1/4': {'slot':     {'rpm': 18000, 'feed': 200, 'plunge': 60, 'doc': 0.125},
                    'adaptive': {'rpm': 18000, 'feed': 200, 'plunge': 60, 'doc': 0.250, 'woc': 0.0625}},
            '1/8': {'slot':     {'rpm': 18000, 'feed': 90, 'plunge': 30, 'doc': 0.0625},
                    'adaptive': {'rpm': 18000, 'feed': 120, 'plunge': 30, 'doc': 0.125, 'woc': 0.025}},
        },
        'Wood': {
            '1/4': {'slot':     {'rpm': 18000, 'feed': 80, 'plunge': 25, 'doc': 0.125},
                    'adaptive': {'rpm': 18000, 'feed': 120, 'plunge': 30, 'doc': 0.250, 'woc': 0.050}},
            '1/8': {'slot':     {'rpm': 18000, 'feed': 50, 'plunge': 15, 'doc': 0.0625},
                    'adaptive': {'rpm': 18000, 'feed': 70, 'plunge': 20, 'doc': 0.125, 'woc': 0.025}},
        },
    },
}

# The note's tested-on-paper recipe for 19/64" holes through 1/16" wall
# aluminum tube: 1/8" bit, 2D Bore (no spring pass -- not needed). Used for every tube hole in
# aluminum; other materials fall back to the 1/8" slot row.
TUBE_BORE_ALUMINUM = {'rpm': 20000, 'feed': 20, 'plunge': 6, 'pitch': 0.0156}

# Coolant written on every tool / operation. The Carbide 3D post only knows
# flood (M7) and off -- 'air' makes it refuse to post.
COOLANT = 'flood'

# ---------------------------------------------------------------------------
# Feature classification (inches)
# ---------------------------------------------------------------------------
BEARING_ODS = [1.125, 0.875]        # 1/2" hex bearing, FR6ZZ flanged bearing
BEARING_TOL = 0.005                 # |D - OD| at or under this -> bearing hole
BEARING_ROUGH_STOCK = 0.010         # radial stock the bearing adaptive rough leaves for the clean-up
BEARING_FIT_OFFSET = -0.005         # finish-bore radial stock to leave: - = bigger hole (matches hand-made Setup4)
BEARING_CLEANUP_PITCH = 0.25        # clean-up bore depth per revolution (only skims BEARING_ROUGH_STOCK)
SMALL_HOLE_MAX = 0.30               # holes below this use the 1/8" bit
DRILL_MATCH_TOL = 0.010             # |D - 1/8| at or under this -> plunge drill, no helix
BORE_MAX = 1.0                      # round holes above this are cleared by adaptive
MIN_BORE_RATIO = 1.10               # helical bore needs D >= ratio * tool D
SMALL_TOOL_RADIUS = 0.130           # inside corner radius under this -> 1/8" bit
SMALL_TOOL_WIDTH = 0.35             # cut-outs narrower than this -> 1/8" bit

# ---------------------------------------------------------------------------
# Setup / hold-down (inches)
# ---------------------------------------------------------------------------
THROUGH_OVERCUT = 0.0               # through cuts go this far past the stock bottom
STOCK_SIDE_OFFSET = 0.125           # plate stock box grows this much on each side
TAB_COUNT = 4
TAB_WIDTH = 0.25
TAB_HEIGHT = 0.03

# WCS origin box points (Fusion's `wcs_origin_boxPoint` choices).
# Plates: Z0 on the spoilboard (stock bottom), X/Y at the front-left corner.
# Tubes: Z0 on top of the face being cut, the "top-left" corner seen from above.
# Fusion's corner numbering (verified live, see LESSONS_LEARNED.md):
#   1 = -X -Y (front-left)   2 = +X -Y   3 = -X +Y (back-left)   4 = +X +Y
PLATE_WCS_POINT = 'bottom 1'
TUBE_WCS_POINT = 'top 3'
