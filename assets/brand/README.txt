Drop exported brand assets in this folder. Nothing here is required — anything
missing falls back to a region cropped out of assets/background.png or to a
pattern drawn in code. Anything present is picked up on the next launch with no
code changes.

Recognised filenames
--------------------
<anything>.ttf / .otf   The event display font (the face used for
                        "FRESHMEN FIRST-WEEK FUNFEST"). The first font file
                        found is registered at startup and used for every
                        heading, button, countdown and for the numbers in the
                        layout preview. Currently falls back to Arial Black.

title_lockup.png        The title lockup + seals, transparent background,
                        >= 2000 px wide. Currently cropped out of the strip
                        artwork and keyed against its own black backdrop, which
                        works but softens the edges.

mascot.png              The panther mascot, transparent, >= 1000 px tall.
                        Appears at the right end of both window headers.
                        No fallback — it is simply absent until exported.

Also accepted: lockup.png / title.png for the lockup, panther.png for the mascot.

Export settings
---------------
PNG-24 with transparency, native size (do not downscale), no baked-in shadows.
