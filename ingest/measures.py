"""The units and names training data is recorded under, in one place.

Both doors — manual entry and an import — write these, so they are defined once
where neither has to import the other.
"""

#: The unit every weight is recorded in. The owner logs in pounds (2026-09-25).
#: Stored as entered, never converted: 225 lb through kilograms and back is
#: 224.999 lb.
WEIGHT_UNIT = "lb"

#: Body weight is the `body_mass` measurement — the name Forge's tool and the
#: fixture already use, so a question about it needs no new vocabulary.
BODY_WEIGHT = "body_mass"
