# Machine, tool and feeds-and-speeds data for the FRC CAM Setup command.
#
# Pure data -- no Fusion calls -- so it can be tuned without touching the builder.
# Everything here is in INCHES and IPM (inches per minute); cam_builder converts
# when it writes Fusion parameters.
#
# Source: Notes/Shop/CNC/Shapeoko 5.1 Pro/Shapeoko 5.1 Pro.md. Every row there is
# still marked "Starting point", so treat these as starting values and update
# them here as cuts get proven out. Wood uses CIC Workshop's published numbers
# for its bits (see MATERIAL_TOOLS).

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
        'label': '1/4 O-flute',
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
        'label': '1/8 O-flute',
        'number': 2,
        'diameter': 0.125,
        'flutes': 1,
        'flute_length': 0.50,
        'shoulder_length': 0.60,
        'body_length': 0.75,
        'overall_length': 1.5,
    },
}

# Per-material bit overrides: a material listed here cuts with these bits instead
# of TOOLS. Same keys (and diameters) as TOOLS, so feature classification doesn't
# change. Tool numbers stay 1 / 2 so the post and tool changes look the same.
#
# Wood runs CIC Workshop bits (cicworkshop.com/collections/bits):
#   Golden Boy XL (GDBOYXL) -- 1/4" x 1.5" 3-flute upcut rougher, 3" OAL
#   UC2175                  -- 1/8" x 3/4" 2-flute upcut, 1/4" shank, 2" OAL
MATERIAL_TOOLS = {
    'Wood': {
        '1/4': {
            'description': '1/4" Golden Boy XL 3FL Upcut (CIC GDBOYXL)',
            'label': '1/4 Golden Boy XL',
            'number': 1,
            'diameter': 0.250,
            'flutes': 3,
            'flute_length': 1.5,
            'shoulder_length': 1.6,
            'body_length': 1.75,
            'overall_length': 3.0,
        },
        '1/8': {
            'description': '1/8" 2FL Upcut (CIC UC2175)',
            'label': '1/8 upcut',
            'number': 2,
            'diameter': 0.125,
            'shank_diameter': 0.250,
            'flutes': 2,
            'flute_length': 0.75,
            'shoulder_length': 0.80,
            'body_length': 0.90,
            'overall_length': 2.0,
        },
    },
}


def tool(key, material=None):
    # The bit a material cuts `key` ('1/4' / '1/8') with.
    return MATERIAL_TOOLS.get(material, {}).get(key) or TOOLS[key]

# ---------------------------------------------------------------------------
# Feeds & speeds:  FEEDS[feeds_key][material][tool][strategy]
# ---------------------------------------------------------------------------
#   rpm      spindle speed
#   feed     cutting feed (IPM)
#   plunge   plunge / ramp feed (IPM)
#   doc      'slot': depth per pass;  'adaptive': max roughing stepdown
#   woc      'adaptive' only: optimal load (radial width of cut)
#   ramp       optional: helical ramp feed (IPM); defaults to plunge
#   ramp_angle optional: helical ramp angle (deg); defaults to 2
#   pitch      optional, 'slot' only: helical bore depth per rev (in); defaults to doc
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
        # Wood: the CIC bits in MATERIAL_TOOLS. Slot rows are CIC's published
        # "Hobby CNC with Spindle" numbers (18k RPM, plunge 50% of feed): Golden
        # Boy XL 100 IPM @ 0.25" per pass (raised below), UC2175 70 IPM @ 0.125".
        # Adaptive takes a thin radial bite, so it runs deeper and faster: the XL
        # at 0.5" per pass (a 3/4" sheet = 2 even passes of 0.375", 1/2" and
        # thinner = 1), the UC2175 at half its flute length.
        # If the wood scorches, drop RPM before dropping feed (a bigger chip
        # carries the heat away).
        # Adaptive ramps: the first test cut showed the 2 deg / plunge-feed helix
        # was far too timid in wood (~17 s per entry at 0.5" deep), so the XL
        # helixes in at 5 deg / 100 IPM and the UC2175 at 3 deg / 50 IPM.
        # It also cut with no strain at a 16% bite, so the optimal load went up
        # (feed unchanged: adaptive caps engagement, so tight corners don't spike):
        # XL 0.040 -> 0.0625 (25%), UC2175 0.020 -> 0.025. A wider bite thins the
        # chip (~0.0032" at 18k), so if it scorches drop to ~16k RPM.
        # 200 IPM was tried and dropped: adaptive is mostly tight arcs, so the
        # Shapeoko's acceleration keeps it well under 150-200 IPM anyway -- a
        # GRBL planner sim saved only 0-0.8 min on a 12-cut-out job. A wider
        # bite isn't acceleration-limited, so the XL's load went 0.080" -> 0.100"
        # (40%). If it chatters, back off to 0.080". IDC rates its similar "Beast"
        # rougher at 0.2" stepover, but only with 100 IPM / 0.0015" chip -- going
        # wider than 0.100" here means dropping the feed too.
        # Profiles ramp along the contour into each layer; same fix, the XL at
        # 5 deg / slot feed (a 0.375" layer ramps in over ~4.3").
        # The XL profiled 0.25" layers with no strain, so its slot DOC went to
        # 0.375": a 0.70-3/4" sheet profiles in 2 passes (was 3), 1/2" in 2,
        # 1/4" in 1. Helical bores keep their proven 0.25" pitch ('pitch').
        'Wood': {
            '1/4': {'slot':     {'rpm': 18000, 'feed': 100, 'plunge': 50, 'doc': 0.375,
                                 'ramp': 100, 'ramp_angle': 5, 'pitch': 0.25},
                    'adaptive': {'rpm': 18000, 'feed': 150, 'plunge': 50, 'doc': 0.50, 'woc': 0.100,
                                 'ramp': 100, 'ramp_angle': 5}},
            '1/8': {'slot':     {'rpm': 18000, 'feed': 70, 'plunge': 35, 'doc': 0.125,
                                 'ramp': 50, 'ramp_angle': 3},
                    'adaptive': {'rpm': 18000, 'feed': 90, 'plunge': 35, 'doc': 0.375, 'woc': 0.025,
                                 'ramp': 50, 'ramp_angle': 3}},
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
# Inside corners smaller than the 1/4" bit's radius (from TOOLS, minus this
# rounding allowance) go to the 1/8" bit. Derived, not typed in: a hard-coded
# 0.130 once sent Tab & Slot's 0.127" dogbones and hand-drawn 0.125" fillets --
# which the 1/4" fits -- to the 1/8". A corner equal to the bit radius cuts fine.
CORNER_FIT_TOL = 0.0005
SMALL_TOOL_WIDTH = 0.35             # cut-outs narrower than this -> 1/8" bit
# Through cut-outs (and 'large' round holes) at least this wide get one inside
# profile with tabs holding the slug, instead of adaptive clearing the whole
# slug. Width is Cutout.width (2 * area / perimeter: about a slot's short side,
# half a circle's diameter). Materials not listed always clear.
CUTOUT_PROFILE_MIN_WIDTH = {'Wood': 1.0}
# Cleared cut-outs: the adaptive leaves this much radial stock, then a 2D
# contour finishes the wall. Adaptive won't drive into a corner about the size
# of the bit (Tab & Slot dogbones are bit + 0.004"), so without the contour the
# dogbones keep a sliver of material and the tab won't seat.
CUTOUT_FINISH_STOCK = 0.010

# ---------------------------------------------------------------------------
# Setup / hold-down (inches)
# ---------------------------------------------------------------------------
THROUGH_OVERCUT = 0.0               # through cuts go this far past the stock bottom
STOCK_SIDE_OFFSET = 0.125           # plate stock box grows this much on each side
TAB_COUNT = 4
TAB_WIDTH = 0.25
TAB_HEIGHT = 0.03
# Per-material tab heights; wood needs thicker tabs to hold the part.
TAB_HEIGHT_BY_MATERIAL = {'Wood': 0.2}


def tab_height(material):
    return TAB_HEIGHT_BY_MATERIAL.get(material, TAB_HEIGHT)

# WCS origin box points (Fusion's `wcs_origin_boxPoint` choices).
# Plates: Z0 on the spoilboard (stock bottom), X/Y at the front-left corner.
# Tubes: Z0 on top of the face being cut, the "top-left" corner seen from above.
# Fusion's corner numbering (verified live, see LESSONS_LEARNED.md):
#   1 = -X -Y (front-left)   2 = +X -Y   3 = -X +Y (back-left)   4 = +X +Y
PLATE_WCS_POINT = 'bottom 1'
TUBE_WCS_POINT = 'top 3'
