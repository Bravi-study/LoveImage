import random

CREDITS = {"1K": 6, "2K": 10, "4K": 16}

SCENES = [
    "an evening walk on an old city bridge at golden hour, elegant casual outfits, his or her arm around the partner, soft bokeh",
    "a cozy cabin by a fireplace on a winter evening, knitted sweaters, sitting close and holding mugs, warm light",
    "walking close together under one umbrella on a rainy evening street, city lights on wet pavement, cinematic mood",
    "a windy sea shore at sunset, light linen clothes, laughing and embracing, warm low sun",
    "an autumn park with golden leaves, wool coats and scarves, walking hand in hand, soft afternoon light",
    "a terrace of a small Italian town at dusk, summer clothes, sharing a quiet moment, warm string lights",
]

TEMPLATE = (
    "Create one new realistic romantic photograph of the two people from the reference images together as a loving couple. "
    "The first image shows one partner and the second image shows the other. "
    "Keep both faces, hair, skin tone, age and natural proportions exactly recognizable, as if they had really posed together. "
    "Scene: {scene}. "
    "Both are adults, dressed for the scene, in a tender, non-sexual pose. "
    "Natural, tasteful editorial photography. No text, no logos, no watermark."
)


def build(scene: str | None) -> str:
    """Only this template and the checked scene reach the image model, the visitor's words never do."""
    return TEMPLATE.format(scene=scene or random.choice(SCENES))
